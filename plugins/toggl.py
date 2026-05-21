"""
Toggl Track MCP plugin -- Issue #142, ADR-0005.

Five tools, all hitting the **real Toggl Track API v9**, authorized by
a STATIC API token read from the ``TOGGL_API_TOKEN`` environment
variable via the provider seam:

  - ``toggl_list_time_entries`` -- ``GET /me/time_entries`` returning
    the user's recent time entries. Read-only.
  - ``toggl_create_time_entry`` -- ``POST /workspaces/{wid}/time_entries``
    with a JSON body. Write (ask-class ``external_data_write``).
  - ``toggl_stop_running_entry`` -- ``PATCH
    /workspaces/{wid}/time_entries/{tid}/stop``. Write.
  - ``toggl_list_workspaces`` -- ``GET /workspaces`` returning the
    user's workspaces (so the LLM can resolve a ``wid`` without
    out-of-band knowledge). Read-only.
  - ``toggl_list_projects`` -- ``GET /workspaces/{wid}/projects``
    returning projects in a workspace (so the LLM can resolve a
    ``project_id``). Read-only.

Clones the ``plugins/todoist.py`` spine: same injectable ``fetch_fn``
+ module-level ``set_token_provider`` seam, same scrub, same
constructor injection, same one-method ``TokenProvider`` Protocol
(no OAuth refresh -- Toggl tokens are user-rotated from the Toggl
profile page). The **one structural divergence** from todoist.py is
the auth header byte shape: Toggl uses **HTTP Basic Auth** with the
API token as the username and the literal string ``"api_token"`` as
the password -- ``Authorization: Basic base64(<token>:api_token)``.
This is the first non-Bearer static-token plugin in the registry --
the third learning-#15 transport shape after Bearer header
(gmail/calendar/todoist/notion) and ``?key=`` query param (youtube).

The token reaches the plugin via a module-level ``set_token_provider``
setter wired from ``cerebral/main.py`` -- the exact
``plugins/todoist.py`` precedent (the orchestrator calls
``module.create()`` zero-arg, so a real token can only arrive this
way). Tests bypass it by passing a stub provider + stub ``fetch_fn``
to the constructor: no real network, OAuth, keyring or browser in
the suite.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "toggl"

# ADR-0005 / Issue #142.
#   - external_data_read + network_egress_cloud: toggl_list_time_entries
#     / toggl_list_workspaces / toggl_list_projects fetch the user's
#     time entries / workspaces / projects from api.track.toggl.com
#     over the internet (the gmail.py / todoist.py / notion.py
#     surface). network_egress_cloud is what the AST audit actually
#     requires (aiohttp/httpx call sites -> NETWORK_EGRESS_ANY,
#     call_site_capabilities.py:148-167).
#   - secrets_read is a DELIBERATE over-declaration -- the youtube.py /
#     gmail.py / todoist.py / notion.py posture-B precedent (clone it,
#     do NOT contrast it). The AST audit maps secrets_read ONLY to
#     keyring.get_password/set_password
#     (call_site_capabilities.py:187-188) and is per-file/intraprocedural.
#     This plugin calls provider.current() -- never keyring.* directly
#     (the static token is read from os.environ in cerebral/main.py, an
#     unscanned file), so the audit will NOT auto-require secrets_read
#     here. We declare it anyway because the plugin's job is to surface
#     the user's time entries behind an API credential, and handing
#     that a silent-class free pass is the wrong default (ADR-0005
#     threats T1/T4). Do not "tidy this away" -- over-declaration is
#     intentional and audit-safe (_inspect only fails on
#     *under*-declaration).
#   - external_data_write (Issue #142): toggl_create_time_entry and
#     toggl_stop_running_entry mutate an external account (they create
#     and stop time entries via POST / PATCH). This is the correct
#     *required* ask-class semantic class (ADR-0005 day-1 ACL, line
#     34) -- NOT an over-declaration like secrets_read above. Like
#     external_data_read it is hand-declared: external_data_* is
#     absent from the AST capability map AND the bare-attr fallback
#     (call_site_capabilities.py:148-199 maps only fs/clipboard/network/
#     secrets/screen/device/code primitives), so the per-file AST audit
#     never auto-requires it -- a semantic capability declared because
#     the tool's effect IS the write, audit-safe.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://api.track.toggl.com/api/v9"
_CREATED_WITH = "cerebral/openmind"
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 1000
_MIN_LIMIT = 1

_FACTORY_NOT_WIRED_MSG = "Toggl is not available -- token provider not wired"
_NO_TOKEN_MSG = "no Toggl API token configured"
_MISSING_CREATE_ARG_MSG = (
    "missing required arg(s) for toggl_create_time_entry: {}"
)
_MISSING_STOP_ARG_MSG = (
    "missing required arg(s) for toggl_stop_running_entry: {}"
)
_MISSING_LIST_PROJECTS_ARG_MSG = (
    "missing required arg(s) for toggl_list_projects: {}"
)


class TokenProvider(Protocol):
    """Per-active-profile API-token handle wired from main.py.

    Carries ONLY ``current()`` because Toggl's API token is a STATIC
    user-rotated value (Toggl profile -> API Token). There is no
    OAuth refresh capability to describe, so the Protocol does not
    pretend one exists. Mirrors plugins/todoist.py and plugins/notion.py
    exactly.
    """

    def current(self) -> Optional[str]: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_toggl_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when
    ``TOGGL_API_TOKEN`` is unset, a fresh handle otherwise.
    """
    global _token_provider_factory
    _token_provider_factory = fn


class TogglAPIError(RuntimeError):
    """Any transport/HTTP failure of a Toggl call. ``status`` is the
    HTTP status code when one is available, else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


def _basic_auth_header(token: str) -> str:
    """Build the Toggl v9 ``Authorization: Basic <b64>`` header value.

    Toggl Track v9 uses HTTP Basic Auth with the API token as the
    username and the literal string ``"api_token"`` as the password.
    Pure function; covered by a byte-shape unit test against a known
    input.
    """
    raw = f"{token}:api_token".encode("ascii")
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON
    or ``None`` for HTTP 204.

    Toggl v9's in-scope endpoints all return 200+body (including the
    stop endpoint, unlike Todoist's 204-empty close/reopen/delete);
    the 204 branch is carried for spine-symmetry with todoist.py and
    is a no-op for this plugin's call sites. HTTP errors are mapped
    to TogglAPIError carrying the status code; transport/other errors
    carry status=None. Deps lazy-imported here so module import stays
    stdlib-only (learning #12).
    """
    try:
        import aiohttp  # type: ignore
    except ImportError:
        aiohttp = None  # type: ignore
    if aiohttp is not None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers,
                                            params=params, json=json) as resp:
                    resp.raise_for_status()
                    if resp.status == 204:
                        return None
                    return await resp.json()
        except aiohttp.ClientResponseError as exc:  # type: ignore[attr-defined]
            raise TogglAPIError(str(exc), status=exc.status) from exc
        except TogglAPIError:
            raise
        except Exception as exc:
            raise TogglAPIError(str(exc), status=None) from exc

    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None  # type: ignore
    if httpx is not None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, headers=headers,
                                            params=params, json=json)
                resp.raise_for_status()
                if resp.status_code == 204:
                    return None
                return resp.json()
        except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
            raise TogglAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except TogglAPIError:
            raise
        except Exception as exc:
            raise TogglAPIError(str(exc), status=None) from exc

    raise TogglAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _shape_entry(e: Any) -> dict:
    """Flatten one Toggl time-entry response row.

    Toggl entries have many fields; we surface the ones an LLM
    benefits from echoing back (id, description, workspace_id,
    project_id, start, stop, duration, tags, billable, server-side
    -1 duration means 'running').
    """
    if not isinstance(e, dict):
        return {}
    return {
        "id": e.get("id", 0),
        "description": e.get("description", "") or "",
        "workspace_id": e.get("workspace_id", 0),
        "project_id": e.get("project_id") or 0,
        "start": e.get("start", "") or "",
        "stop": e.get("stop", "") or "",
        "duration": e.get("duration", 0),
        "tags": e.get("tags", []) or [],
        "billable": bool(e.get("billable", False)),
    }


def _shape_workspace(w: Any) -> dict:
    """Flatten one Toggl workspace row."""
    if not isinstance(w, dict):
        return {}
    return {
        "id": w.get("id", 0),
        "name": w.get("name", "") or "",
        "default": bool(w.get("default", False)),
        "premium": bool(w.get("premium", False)),
    }


def _shape_project(p: Any) -> dict:
    """Flatten one Toggl project row."""
    if not isinstance(p, dict):
        return {}
    return {
        "id": p.get("id", 0),
        "name": p.get("name", "") or "",
        "workspace_id": p.get("workspace_id", 0),
        "color": p.get("color", "") or "",
        "active": bool(p.get("active", True)),
    }


class TogglPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        # Constructor-injected provider wins over the module-level setter.
        # Tests pass directly; production leaves this None and main.py wires
        # the module-level _token_provider_factory at startup.
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        # Every token (and its base64-encoded form) ever held this call,
        # so _scrub strips both from any log line / ToolResult. The b64
        # form contains the raw token in a decodable shape, so an
        # attacker reading a log line could decode it -- both forms
        # MUST be scrubbed.
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="toggl_list_time_entries",
                description=(
                    "List the user's recent Toggl Track time entries "
                    "from /me/time_entries (the last 9 days by "
                    "default). Returns id, description, workspace_id, "
                    "project_id, start, stop, duration, tags and "
                    "billable for each entry. A duration of -1 "
                    "indicates a running entry."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "since": {
                            "type": "integer",
                            "description": (
                                "Optional Unix timestamp -- only return "
                                "entries modified after this time."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum entries to return (default "
                                f"{_DEFAULT_LIMIT}, clamped to "
                                f"[{_MIN_LIMIT}, {_MAX_LIMIT}])."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="toggl_create_time_entry",
                description=(
                    "Create a new time entry in the user's Toggl Track "
                    "account. wid (workspace id) is required -- the "
                    "LLM should call toggl_list_workspaces first or "
                    "have the wid from prior context. start is an ISO "
                    "8601 timestamp; duration is in seconds (or -1 "
                    "for a running entry that will need an explicit "
                    "stop). Returns the created entry's id, "
                    "description, workspace_id, project_id, start, "
                    "stop, duration, tags and billable."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "wid": {
                            "type": "integer",
                            "description": (
                                "Toggl workspace id to create the "
                                "entry in."
                            ),
                        },
                        "start": {
                            "type": "string",
                            "description": (
                                "ISO 8601 start timestamp (e.g. "
                                "'2026-05-20T15:00:00Z')."
                            ),
                        },
                        "duration": {
                            "type": "integer",
                            "description": (
                                "Duration in seconds, or -1 to start "
                                "a running entry."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional entry description.",
                        },
                        "project_id": {
                            "type": "integer",
                            "description": (
                                "Optional project id (resolve via "
                                "toggl_list_projects)."
                            ),
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of tag names to attach."
                            ),
                        },
                        "billable": {
                            "type": "boolean",
                            "description": (
                                "Optional billable flag (premium "
                                "workspaces only)."
                            ),
                        },
                    },
                    "required": ["wid", "start", "duration"],
                },
            ),
            Tool(
                name="toggl_stop_running_entry",
                description=(
                    "Stop a running Toggl time entry. BOTH wid "
                    "(workspace id) and tid (time-entry id) are "
                    "required -- the LLM must know which specific "
                    "entry to stop (defends against 'stopped the "
                    "wrong thing' surprises in multi-workspace "
                    "accounts). Returns the stopped entry's id, "
                    "description, workspace_id, project_id, start, "
                    "stop, duration, tags and billable."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "wid": {
                            "type": "integer",
                            "description": (
                                "Toggl workspace id the entry belongs "
                                "to."
                            ),
                        },
                        "tid": {
                            "type": "integer",
                            "description": (
                                "Toggl time-entry id to stop."
                            ),
                        },
                    },
                    "required": ["wid", "tid"],
                },
            ),
            Tool(
                name="toggl_list_workspaces",
                description=(
                    "List the user's Toggl workspaces. Returns id, "
                    "name, default and premium for each. Use this to "
                    "resolve a wid before creating a time entry."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="toggl_list_projects",
                description=(
                    "List projects in a Toggl workspace. Returns id, "
                    "name, workspace_id, color and active for each. "
                    "Use this to resolve a project_id before creating "
                    "a time entry."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "wid": {
                            "type": "integer",
                            "description": (
                                "Toggl workspace id to list projects "
                                "from."
                            ),
                        },
                    },
                    "required": ["wid"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "toggl_list_time_entries":
            return await self._list_time_entries(args)
        if tool_name == "toggl_create_time_entry":
            return await self._create_time_entry(args)
        if tool_name == "toggl_stop_running_entry":
            return await self._stop_running_entry(args)
        if tool_name == "toggl_list_workspaces":
            return await self._list_workspaces(args)
        if tool_name == "toggl_list_projects":
            return await self._list_projects(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        ``error_string`` is a canonical message so callers can return
        ``ToolResult(content=err, is_error=True)`` directly (the
        todoist.py / notion.py posture)."""
        factory = self._token_factory()
        if self._provider is not None:
            return self._provider, None
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        provider = factory()
        if provider is None:
            return None, _NO_TOKEN_MSG
        return provider, None

    @staticmethod
    def _token_factory() -> Optional[TokenProviderFactory]:
        return _token_provider_factory

    def _scrub(self, text: str) -> str:
        """Strip every token form (raw + base64) seen this call from
        text bound for a log or ToolResult."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider) -> str:
        """Get the static API token from the provider. Records BOTH the
        raw token AND its base64-encoded form (the part after 'Basic '
        in the Authorization header) for scrubbing. The b64 form
        contains the token in a decodable shape, so a log line that
        leaked the header value would be just as bad as leaking the
        raw token. No refresh path -- Toggl's token is static."""
        token = provider.current() or ""
        if token:
            self._seen_tokens.add(token)
            # Also record the base64 form so _scrub strips it from any
            # leaked Authorization header value in a log line.
            raw = f"{token}:api_token".encode("ascii")
            self._seen_tokens.add(
                base64.b64encode(raw).decode("ascii")
            )
        return token

    async def _request(self, method: str, endpoint: str, token: str, *,
                       params: dict | None = None,
                       json: dict | None = None) -> Any:
        return await self._fetch(
            method,
            f"{_BASE}/{endpoint}",
            headers={
                "Authorization": _basic_auth_header(token),
                "Content-Type": "application/json",
            },
            params=params,
            json=json,
        )

    def _clamp_max_results(self, raw: Any) -> int:
        try:
            value = int(raw) if raw is not None else _DEFAULT_LIMIT
        except (TypeError, ValueError):
            value = _DEFAULT_LIMIT
        return max(_MIN_LIMIT, min(_MAX_LIMIT, value))

    @staticmethod
    def _coerce_int(raw: Any) -> int | None:
        """Coerce raw -> int, refusing bools and non-numeric. Returns
        None on failure."""
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                return int(raw.strip())
            except ValueError:
                return None
        return None

    async def _list_time_entries(self, args: dict) -> ToolResult:
        max_results = self._clamp_max_results(args.get("max_results"))
        params: dict = {}
        since = self._coerce_int(args.get("since"))
        if since is not None:
            params["since"] = since

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            entries = await self._request(
                "GET", "me/time_entries", token, params=params,
            )
        except Exception as exc:
            logger.error(
                "[toggl] toggl_list_time_entries failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Toggl request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(entries, list):
            return ToolResult(
                content="unexpected Toggl list response", is_error=True,
            )

        shaped = [_shape_entry(e) for e in entries[:max_results]]
        return ToolResult(content=json.dumps({"entries": shaped}))

    async def _create_time_entry(self, args: dict) -> ToolResult:
        # Required: wid, start, duration. Validate before any provider
        # touch so an arg-error never reads a token.
        missing: list[str] = []
        wid = self._coerce_int(args.get("wid"))
        if wid is None:
            missing.append("wid")
        start = args.get("start")
        if not isinstance(start, str) or not start:
            missing.append("start")
        duration = self._coerce_int(args.get("duration"))
        if duration is None:
            missing.append("duration")
        if missing:
            return ToolResult(
                content=_MISSING_CREATE_ARG_MSG.format(
                    ", ".join(missing)
                ),
                is_error=True,
            )

        body: dict = {
            "wid": wid,
            "start": start,
            "duration": duration,
            "created_with": _CREATED_WITH,
        }
        description = args.get("description")
        if isinstance(description, str) and description:
            body["description"] = description
        project_id = self._coerce_int(args.get("project_id"))
        if project_id is not None:
            body["project_id"] = project_id
        tags = args.get("tags")
        if isinstance(tags, list):
            cleaned = [t for t in tags if isinstance(t, str) and t]
            if cleaned:
                body["tags"] = cleaned
        billable = args.get("billable")
        if isinstance(billable, bool):
            body["billable"] = billable

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request(
                "POST", f"workspaces/{wid}/time_entries", token, json=body,
            )
        except Exception as exc:
            logger.error(
                "[toggl] toggl_create_time_entry failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Toggl request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Toggl create response", is_error=True,
            )
        return ToolResult(content=json.dumps(_shape_entry(resp)))

    async def _stop_running_entry(self, args: dict) -> ToolResult:
        missing: list[str] = []
        wid = self._coerce_int(args.get("wid"))
        if wid is None:
            missing.append("wid")
        tid = self._coerce_int(args.get("tid"))
        if tid is None:
            missing.append("tid")
        if missing:
            return ToolResult(
                content=_MISSING_STOP_ARG_MSG.format(
                    ", ".join(missing)
                ),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request(
                "PATCH",
                f"workspaces/{wid}/time_entries/{tid}/stop",
                token,
            )
        except Exception as exc:
            logger.error(
                "[toggl] toggl_stop_running_entry failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Toggl request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Toggl stop response", is_error=True,
            )
        return ToolResult(content=json.dumps(_shape_entry(resp)))

    async def _list_workspaces(self, args: dict) -> ToolResult:
        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            workspaces = await self._request("GET", "workspaces", token)
        except Exception as exc:
            logger.error(
                "[toggl] toggl_list_workspaces failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Toggl request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(workspaces, list):
            return ToolResult(
                content="unexpected Toggl workspaces response",
                is_error=True,
            )

        shaped = [_shape_workspace(w) for w in workspaces]
        return ToolResult(content=json.dumps({"workspaces": shaped}))

    async def _list_projects(self, args: dict) -> ToolResult:
        wid = self._coerce_int(args.get("wid"))
        if wid is None:
            return ToolResult(
                content=_MISSING_LIST_PROJECTS_ARG_MSG.format("wid"),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            projects = await self._request(
                "GET", f"workspaces/{wid}/projects", token,
            )
        except Exception as exc:
            logger.error(
                "[toggl] toggl_list_projects failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Toggl request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(projects, list):
            return ToolResult(
                content="unexpected Toggl projects response", is_error=True,
            )

        shaped = [_shape_project(p) for p in projects]
        return ToolResult(content=json.dumps({"projects": shaped}))


def create(fetch_fn: FetchFn | None = None) -> TogglPlugin:
    return TogglPlugin(fetch_fn=fetch_fn)
