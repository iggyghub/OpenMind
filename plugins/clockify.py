"""
Clockify MCP plugin -- Issue #145, ADR-0005.

Five tools, all hitting the **real Clockify API v1**, authorized by
a STATIC API key read from the ``CLOCKIFY_API_KEY`` environment
variable via the provider seam:

  - ``clockify_list_time_entries`` -- ``GET
    /workspaces/{wid}/user/{userId}/time-entries`` returning the
    user's recent time entries. Read-only. Requires ``wid``;
    ``userId`` resolved internally via ``GET /user``.
  - ``clockify_create_time_entry`` -- ``POST
    /workspaces/{wid}/time-entries`` with a JSON body. Write
    (ask-class ``external_data_write``).
  - ``clockify_stop_running_entry`` -- ``PATCH
    /workspaces/{wid}/user/{userId}/time-entries`` with body
    ``{"end": "<ISO>"}``. Requires ``wid``; ``userId`` resolved
    internally via ``GET /user``. Clockify's data model has at most
    one in-progress entry per user, so this stops "whatever is
    running" without taking a tid argument. Write.
  - ``clockify_list_workspaces`` -- ``GET /workspaces`` returning
    the user's workspaces (so the LLM can resolve a ``workspace_id``
    without out-of-band knowledge). Read-only.
  - ``clockify_list_projects`` -- ``GET /workspaces/{wid}/projects``
    returning projects in a workspace (so the LLM can resolve a
    ``project_id``). Read-only.

Clones the ``plugins/toggl.py`` spine: same injectable ``fetch_fn``
+ module-level ``set_token_provider`` seam, same scrub, same
constructor injection, same one-method ``TokenProvider`` Protocol
(no OAuth refresh -- Clockify keys are user-rotated from the
Clockify profile page). The **two structural divergences** from
toggl.py are: (1) the auth header byte shape -- Clockify uses an
``X-Api-Key`` custom header with the raw key as the value (no
encoding wrapper, no Bearer prefix, no Basic encoding); (2) the
stop semantic resolves ``userId`` internally via ``GET /user``
because Clockify's stop endpoint requires the path-encoded userId
but the user-facing tool should not need it. This is the FIRST
custom-header static-token plugin in the registry -- the fourth
learning-#15 transport shape after Bearer header
(gmail/calendar/todoist/notion), ``?key=`` query param (youtube),
and HTTP Basic with ``api_token`` literal (toggl).

The token reaches the plugin via a module-level ``set_token_provider``
setter wired from ``cerebral/main.py`` -- the exact
``plugins/toggl.py`` precedent (the orchestrator calls
``module.create()`` zero-arg, so a real key can only arrive this
way). Tests bypass it by passing a stub provider + stub ``fetch_fn``
to the constructor: no real network, OAuth, keyring or browser in
the suite.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "clockify"

# ADR-0005 / Issue #145.
#   - external_data_read + network_egress_cloud: clockify_list_time_entries
#     / clockify_list_workspaces / clockify_list_projects fetch the user's
#     time entries / workspaces / projects from api.clockify.me over the
#     internet (the gmail.py / todoist.py / notion.py / toggl.py surface).
#     network_egress_cloud is what the AST audit actually requires
#     (aiohttp/httpx call sites -> NETWORK_EGRESS_ANY,
#     call_site_capabilities.py:148-167).
#   - secrets_read is a DELIBERATE over-declaration -- the youtube.py /
#     gmail.py / todoist.py / notion.py / toggl.py posture-B precedent
#     (clone it, do NOT contrast it). The AST audit maps secrets_read
#     ONLY to keyring.get_password/set_password
#     (call_site_capabilities.py:187-188) and is per-file/intraprocedural.
#     This plugin calls provider.current() -- never keyring.* directly
#     (the static key is read from os.environ in cerebral/main.py, an
#     unscanned file), so the audit will NOT auto-require secrets_read
#     here. We declare it anyway because the plugin's job is to surface
#     the user's time entries behind an API credential, and handing
#     that a silent-class free pass is the wrong default (ADR-0005
#     threats T1/T4). Do not "tidy this away" -- over-declaration is
#     intentional and audit-safe (_inspect only fails on
#     *under*-declaration).
#   - external_data_write (Issue #145): clockify_create_time_entry and
#     clockify_stop_running_entry mutate an external account (they
#     create and stop time entries via POST / PATCH). This is the
#     correct *required* ask-class semantic class (ADR-0005 day-1 ACL,
#     line 34) -- NOT an over-declaration like secrets_read above. Like
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

_BASE = "https://api.clockify.me/api/v1"
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 200
_MIN_LIMIT = 1

_FACTORY_NOT_WIRED_MSG = "Clockify is not available -- token provider not wired"
_NO_TOKEN_MSG = "no Clockify API key configured"
_MISSING_LIST_ARG_MSG = (
    "missing required arg(s) for clockify_list_time_entries: {}"
)
_MISSING_CREATE_ARG_MSG = (
    "missing required arg(s) for clockify_create_time_entry: {}"
)
_MISSING_STOP_ARG_MSG = (
    "missing required arg(s) for clockify_stop_running_entry: {}"
)
_MISSING_LIST_PROJECTS_ARG_MSG = (
    "missing required arg(s) for clockify_list_projects: {}"
)
_USER_RESOLVE_BAD_RESP_MSG = "unexpected Clockify /user response"
_USER_RESOLVE_NO_ID_MSG = "could not resolve Clockify user id"


def _now_iso() -> str:
    """Current UTC time as second-precision RFC3339 / ISO 8601.

    Clockify accepts both second-precision and millisecond-precision in
    the stop body's ``end`` field; second-precision keeps log lines
    legible. Module-level (not bound to plugin instance) so tests can
    monkey-patch it via ``monkeypatch.setattr(plugins.clockify,
    "_now_iso", lambda: "2026-05-20T15:00:00Z")`` for deterministic
    stop-body assertions.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TokenProvider(Protocol):
    """Per-active-profile API-key handle wired from main.py.

    Carries ONLY ``current()`` because Clockify's API key is a STATIC
    user-rotated value (Clockify profile -> API key). There is no
    OAuth refresh capability to describe, so the Protocol does not
    pretend one exists. Mirrors plugins/toggl.py and plugins/todoist.py
    exactly.
    """

    def current(self) -> Optional[str]: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_clockify_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when
    ``CLOCKIFY_API_KEY`` is unset, a fresh handle otherwise.
    """
    global _token_provider_factory
    _token_provider_factory = fn


class ClockifyAPIError(RuntimeError):
    """Any transport/HTTP failure of a Clockify call. ``status`` is the
    HTTP status code when one is available, else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON
    or ``None`` for HTTP 204.

    Clockify v1's in-scope endpoints all return 200+body (including the
    stop endpoint); the 204 branch is carried for spine-symmetry with
    toggl.py / todoist.py and is a no-op for this plugin's call sites.
    HTTP errors are mapped to ClockifyAPIError carrying the status
    code; transport/other errors carry status=None. Deps lazy-imported
    here so module import stays stdlib-only (learning #12).
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
            raise ClockifyAPIError(str(exc), status=exc.status) from exc
        except ClockifyAPIError:
            raise
        except Exception as exc:
            raise ClockifyAPIError(str(exc), status=None) from exc

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
            raise ClockifyAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except ClockifyAPIError:
            raise
        except Exception as exc:
            raise ClockifyAPIError(str(exc), status=None) from exc

    raise ClockifyAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _shape_entry(e: Any) -> dict:
    """Flatten one Clockify time-entry response row.

    Clockify entries carry ``timeInterval`` containing start / end /
    duration; surface the ones an LLM benefits from echoing back. An
    empty ``end`` indicates a running entry (Clockify's "currently
    in-progress" marker, equivalent to Toggl's ``duration: -1``).
    """
    if not isinstance(e, dict):
        return {}
    interval = e.get("timeInterval") if isinstance(e.get("timeInterval"), dict) else {}
    return {
        "id": e.get("id", "") or "",
        "description": e.get("description", "") or "",
        "workspace_id": e.get("workspaceId", "") or "",
        "project_id": e.get("projectId", "") or "",
        "user_id": e.get("userId", "") or "",
        "start": interval.get("start", "") or "",
        "end": interval.get("end", "") or "",
        "duration": interval.get("duration", "") or "",
        "tag_ids": list(e.get("tagIds", []) or []),
        "billable": bool(e.get("billable", False)),
    }


def _shape_workspace(w: Any) -> dict:
    """Flatten one Clockify workspace row."""
    if not isinstance(w, dict):
        return {}
    return {
        "id": w.get("id", "") or "",
        "name": w.get("name", "") or "",
        "image_url": w.get("imageUrl", "") or "",
    }


def _shape_project(p: Any) -> dict:
    """Flatten one Clockify project row."""
    if not isinstance(p, dict):
        return {}
    return {
        "id": p.get("id", "") or "",
        "name": p.get("name", "") or "",
        "workspace_id": p.get("workspaceId", "") or "",
        "client_id": p.get("clientId", "") or "",
        "color": p.get("color", "") or "",
        "archived": bool(p.get("archived", False)),
    }


class ClockifyPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        # Constructor-injected provider wins over the module-level setter.
        # Tests pass directly; production leaves this None and main.py wires
        # the module-level _token_provider_factory at startup.
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        # Every token ever held this call, so _scrub strips it from any
        # log line / ToolResult. ONE form only (raw key) -- the X-Api-Key
        # header value IS the raw key (no encoding wrapper), so there's
        # no second form for an attacker to decode from a leaked header
        # value. This is the fourth learning-#15 transport shape -- the
        # first custom-header static-token plugin in the registry.
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="clockify_list_time_entries",
                description=(
                    "List the user's recent Clockify time entries from "
                    "the given workspace (default 10, max 200). Requires "
                    "wid (workspace_id); userId is resolved internally "
                    "via /user. Returns id, description, workspace_id, "
                    "project_id, user_id, start, end, duration, tag_ids "
                    "and billable for each entry. An empty end value "
                    "indicates a running entry."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "wid": {
                            "type": "string",
                            "description": (
                                "Clockify workspace id (MongoDB ObjectId "
                                "hex string) to list entries from."
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
                    "required": ["wid"],
                },
            ),
            Tool(
                name="clockify_create_time_entry",
                description=(
                    "Create a new time entry in the user's Clockify "
                    "account for the given workspace. wid (workspace "
                    "id) and start (ISO 8601 timestamp) are required. "
                    "Omit end to create a running entry (Clockify's "
                    "in-progress marker); pass end to create a closed "
                    "entry. The LLM should call clockify_list_workspaces "
                    "first to resolve wid. Returns the created entry's "
                    "id, description, workspace_id, project_id, "
                    "user_id, start, end, duration, tag_ids and "
                    "billable."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "wid": {
                            "type": "string",
                            "description": (
                                "Clockify workspace id to create the "
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
                        "end": {
                            "type": "string",
                            "description": (
                                "Optional ISO 8601 end timestamp. Omit "
                                "to create a running entry."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional entry description.",
                        },
                        "project_id": {
                            "type": "string",
                            "description": (
                                "Optional project id (resolve via "
                                "clockify_list_projects)."
                            ),
                        },
                        "task_id": {
                            "type": "string",
                            "description": (
                                "Optional task id (scoped to the "
                                "project)."
                            ),
                        },
                        "tag_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional list of tag ids to attach."
                            ),
                        },
                        "billable": {
                            "type": "boolean",
                            "description": (
                                "Optional billable flag."
                            ),
                        },
                    },
                    "required": ["wid", "start"],
                },
            ),
            Tool(
                name="clockify_stop_running_entry",
                description=(
                    "Stop the user's currently-running Clockify time "
                    "entry in the given workspace. Requires wid "
                    "(workspace id); userId is resolved internally via "
                    "/user. Clockify has at most one in-progress entry "
                    "per user, so this stops 'whatever is running' "
                    "without taking a time-entry id. Returns the "
                    "stopped entry's id, description, workspace_id, "
                    "project_id, user_id, start, end, duration, "
                    "tag_ids and billable."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "wid": {
                            "type": "string",
                            "description": (
                                "Clockify workspace id the running "
                                "entry belongs to."
                            ),
                        },
                    },
                    "required": ["wid"],
                },
            ),
            Tool(
                name="clockify_list_workspaces",
                description=(
                    "List the user's Clockify workspaces. Returns id, "
                    "name and image_url for each. Use this to resolve "
                    "a workspace_id before creating a time entry."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="clockify_list_projects",
                description=(
                    "List projects in a Clockify workspace. Returns id, "
                    "name, workspace_id, client_id, color and archived "
                    "for each. Use this to resolve a project_id before "
                    "creating a time entry."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "wid": {
                            "type": "string",
                            "description": (
                                "Clockify workspace id to list projects "
                                "from."
                            ),
                        },
                    },
                    "required": ["wid"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "clockify_list_time_entries":
            return await self._list_time_entries(args)
        if tool_name == "clockify_create_time_entry":
            return await self._create_time_entry(args)
        if tool_name == "clockify_stop_running_entry":
            return await self._stop_running_entry(args)
        if tool_name == "clockify_list_workspaces":
            return await self._list_workspaces(args)
        if tool_name == "clockify_list_projects":
            return await self._list_projects(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        ``error_string`` is a canonical message so callers can return
        ``ToolResult(content=err, is_error=True)`` directly (the
        toggl.py / todoist.py / notion.py posture)."""
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
        """Strip every token form seen this call from text bound for a
        log or ToolResult. ONE form only (the raw key) -- the X-Api-Key
        header is not encoded, so there's no second form to scrub."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider) -> str:
        """Get the static API key from the provider. Records ONE form
        (raw key) for scrubbing. Unlike toggl.py the transport does NOT
        encode the key, so no b64 form is recorded -- the X-Api-Key
        header value IS the raw key."""
        token = provider.current() or ""
        if token:
            self._seen_tokens.add(token)
        return token

    async def _request(self, method: str, endpoint: str, token: str, *,
                       params: dict | None = None,
                       json: dict | None = None) -> Any:
        return await self._fetch(
            method,
            f"{_BASE}/{endpoint}",
            headers={
                "X-Api-Key": token,
                "Content-Type": "application/json",
            },
            params=params,
            json=json,
        )

    async def _resolve_user_id(self, token: str) -> tuple[str | None, str | None]:
        """Resolve the current user's id via GET /user.

        Returns ``(user_id, error_msg)``. Exactly one is non-None.
        Used by ``_stop_running_entry`` AND ``_list_time_entries``
        because Clockify has no ``/user/me/`` shortcut on the
        user-scoped endpoints -- the only documented self-scoped list
        path is ``/workspaces/{wid}/user/{userId}/time-entries``, and
        the stop endpoint similarly requires path-encoded userId. One
        extra GET per invocation; no caching to keep the spine simple
        (and stops are rare in practice).
        """
        user_resp = await self._request("GET", "user", token)
        if not isinstance(user_resp, dict):
            return None, _USER_RESOLVE_BAD_RESP_MSG
        user_id = user_resp.get("id")
        if not isinstance(user_id, str) or not user_id:
            return None, _USER_RESOLVE_NO_ID_MSG
        return user_id, None

    def _clamp_max_results(self, raw: Any) -> int:
        try:
            value = int(raw) if raw is not None else _DEFAULT_LIMIT
        except (TypeError, ValueError):
            value = _DEFAULT_LIMIT
        if isinstance(raw, bool):
            value = _DEFAULT_LIMIT
        return max(_MIN_LIMIT, min(_MAX_LIMIT, value))

    @staticmethod
    def _coerce_id(raw: Any) -> str | None:
        """Coerce raw -> Clockify ObjectId string. Returns None on
        failure. Clockify IDs are MongoDB ObjectId hex strings (24-char
        hex), so we require a non-empty string -- not an integer (unlike
        Toggl's integer IDs)."""
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    async def _list_time_entries(self, args: dict) -> ToolResult:
        wid = self._coerce_id(args.get("wid"))
        if wid is None:
            return ToolResult(
                content=_MISSING_LIST_ARG_MSG.format("wid"),
                is_error=True,
            )
        max_results = self._clamp_max_results(args.get("max_results"))

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            user_id, user_err = await self._resolve_user_id(token)
            if user_err is not None:
                return ToolResult(content=user_err, is_error=True)
            entries = await self._request(
                "GET",
                f"workspaces/{wid}/user/{user_id}/time-entries",
                token,
                params={"page-size": max_results},
            )
        except Exception as exc:
            logger.error(
                "[clockify] clockify_list_time_entries failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Clockify request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(entries, list):
            return ToolResult(
                content="unexpected Clockify list response", is_error=True,
            )

        shaped = [_shape_entry(e) for e in entries[:max_results]]
        return ToolResult(content=json.dumps({"entries": shaped}))

    async def _create_time_entry(self, args: dict) -> ToolResult:
        # Required: wid, start. Validate before any provider touch so an
        # arg-error never reads a token.
        missing: list[str] = []
        wid = self._coerce_id(args.get("wid"))
        if wid is None:
            missing.append("wid")
        start = args.get("start")
        if not isinstance(start, str) or not start:
            missing.append("start")
        if missing:
            return ToolResult(
                content=_MISSING_CREATE_ARG_MSG.format(
                    ", ".join(missing)
                ),
                is_error=True,
            )

        body: dict = {"start": start}
        end = args.get("end")
        if isinstance(end, str) and end:
            body["end"] = end
        description = args.get("description")
        if isinstance(description, str) and description:
            body["description"] = description
        project_id = self._coerce_id(args.get("project_id"))
        if project_id is not None:
            body["projectId"] = project_id
        task_id = self._coerce_id(args.get("task_id"))
        if task_id is not None:
            body["taskId"] = task_id
        tag_ids = args.get("tag_ids")
        if isinstance(tag_ids, list):
            cleaned = [t.strip() for t in tag_ids
                       if isinstance(t, str) and t.strip()]
            if cleaned:
                body["tagIds"] = cleaned
        billable = args.get("billable")
        if isinstance(billable, bool):
            body["billable"] = billable

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request(
                "POST", f"workspaces/{wid}/time-entries", token, json=body,
            )
        except Exception as exc:
            logger.error(
                "[clockify] clockify_create_time_entry failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Clockify request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Clockify create response", is_error=True,
            )
        return ToolResult(content=json.dumps(_shape_entry(resp)))

    async def _stop_running_entry(self, args: dict) -> ToolResult:
        wid = self._coerce_id(args.get("wid"))
        if wid is None:
            return ToolResult(
                content=_MISSING_STOP_ARG_MSG.format("wid"),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            user_id, user_err = await self._resolve_user_id(token)
            if user_err is not None:
                return ToolResult(content=user_err, is_error=True)
            resp = await self._request(
                "PATCH",
                f"workspaces/{wid}/user/{user_id}/time-entries",
                token,
                json={"end": _now_iso()},
            )
        except Exception as exc:
            logger.error(
                "[clockify] clockify_stop_running_entry failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Clockify request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Clockify stop response", is_error=True,
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
                "[clockify] clockify_list_workspaces failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Clockify request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(workspaces, list):
            return ToolResult(
                content="unexpected Clockify workspaces response",
                is_error=True,
            )

        shaped = [_shape_workspace(w) for w in workspaces]
        return ToolResult(content=json.dumps({"workspaces": shaped}))

    async def _list_projects(self, args: dict) -> ToolResult:
        wid = self._coerce_id(args.get("wid"))
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
                "[clockify] clockify_list_projects failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Clockify request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(projects, list):
            return ToolResult(
                content="unexpected Clockify projects response", is_error=True,
            )

        shaped = [_shape_project(p) for p in projects]
        return ToolResult(content=json.dumps({"projects": shaped}))


def create(fetch_fn: FetchFn | None = None) -> ClockifyPlugin:
    return ClockifyPlugin(fetch_fn=fetch_fn)
