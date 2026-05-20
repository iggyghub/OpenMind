"""
Todoist MCP plugin -- Issue #130, ADR-0005.

Two tools, both hitting the **real Todoist REST API v1**, authorized by
a STATIC API token read from the ``TODOIST_API_TOKEN`` environment
variable via the provider seam:

  - ``todoist_list_tasks`` -- ``GET /tasks`` with optional filter /
    project_id / label / max_results query params. Read-only.
  - ``todoist_create_task`` -- ``POST /tasks`` with a JSON body. Write
    (ask-class ``external_data_write``).

Clones the ``plugins/gmail.py`` spine: same ``Authorization: Bearer``
header transport, same injectable ``fetch_fn`` + module-level
``set_token_provider`` seam, same scrub. The one structural divergence
is in the ``TokenProvider`` Protocol -- Todoist's API token is STATIC
(user-rotated from the Todoist settings page) so the Protocol carries
**only** ``current()``. There is no OAuth refresh capability to
describe and no 401->refresh->retry path in the plugin: a 401
propagates straight through as an error and the user rotates the env
var manually.

The token reaches the plugin via a module-level ``set_token_provider``
setter wired from ``cerebral/main.py`` -- the exact ``plugins/gmail.py``
precedent (the orchestrator calls ``module.create()`` zero-arg, so a
real token can only arrive this way). Tests bypass it by passing a
stub provider + stub ``fetch_fn`` to the constructor: no real
network, OAuth, keyring or browser in the suite.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "todoist"

# ADR-0005 / Issue #130.
#   - external_data_read + network_egress_cloud: todoist_list_tasks fetches
#     the user's tasks from api.todoist.com over the internet (the
#     gmail.py/youtube.py/reddit.py surface). network_egress_cloud is what
#     the AST audit actually requires (aiohttp/httpx call sites ->
#     NETWORK_EGRESS_ANY, call_site_capabilities.py:148-167).
#   - secrets_read is a DELIBERATE over-declaration -- the youtube.py /
#     gmail.py posture-B precedent (clone it, do NOT contrast it). The AST
#     audit maps secrets_read ONLY to keyring.get_password/set_password
#     (call_site_capabilities.py:187-188) and is per-file/intraprocedural.
#     This plugin calls provider.current() -- never keyring.* directly
#     (the static token is read from os.environ in cerebral/main.py, an
#     unscanned file), so the audit will NOT auto-require secrets_read
#     here. We declare it anyway because the plugin's job is to surface
#     the user's tasks behind an API credential, and handing that a
#     silent-class free pass is the wrong default (ADR-0005 threats
#     T1/T4). Do not "tidy this away" -- over-declaration is intentional
#     and audit-safe (_inspect only fails on *under*-declaration).
#   - external_data_write (Issue #130): todoist_create_task mutates an
#     external account (it creates a task via POST /tasks). This is the
#     correct *required* ask-class semantic class (ADR-0005 day-1 ACL,
#     line 34) -- NOT an over-declaration like secrets_read above. Like
#     external_data_read it is hand-declared: external_data_* is absent
#     from the AST capability map AND the bare-attr fallback
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

_BASE = "https://api.todoist.com/api/v1"
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 200
_MIN_LIMIT = 1

_FACTORY_NOT_WIRED_MSG = "Todoist is not available -- token provider not wired"
_NO_TOKEN_MSG = "no Todoist API token configured"
_MISSING_CREATE_ARG_MSG = "missing required arg(s) for todoist_create_task: {}"


class TokenProvider(Protocol):
    """Per-active-profile API-token handle wired from main.py.

    Carries ONLY ``current()`` because Todoist's API token is a STATIC
    user-rotated value (Todoist settings -> Integrations -> Developer ->
    API token). There is no OAuth refresh capability to describe, so the
    Protocol does not pretend one exists. This is a deliberate
    divergence from ``plugins/gmail.py``'s ``TokenProvider`` (which
    carries both ``current()`` and ``refresh()`` for OAuth): the
    Protocol describes the actual capability so any AI or person
    reading it knows what's going on -- no dead-code puzzle, no
    accidental call to a non-existent refresh path.
    """

    def current(self) -> Optional[str]: ...


# Factory returns ``TokenProvider | None`` -- ``None`` when
# TODOIST_API_TOKEN is unset. Set once at startup by cerebral/main.py
# via set_token_provider(), mirroring plugins/gmail.py /
# plugins/calendar.py. Tests pass a provider directly to the
# constructor (constructor injection wins).
TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_todoist_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin.

    The factory must return ``TokenProvider | None``: ``None`` when
    ``TODOIST_API_TOKEN`` is unset, a fresh handle otherwise. (The env
    var is system-wide; future per-profile OAuth would be additive.)
    """
    global _token_provider_factory
    _token_provider_factory = fn


class TodoistAPIError(RuntimeError):
    """Any transport/HTTP failure of a Todoist call. ``status`` is the
    HTTP status code when one is available, else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    HTTP errors are mapped to TodoistAPIError carrying the status code;
    transport/other errors carry status=None. Deps lazy-imported here
    so module import stays stdlib-only (learning #12).
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
                    return await resp.json()
        except aiohttp.ClientResponseError as exc:  # type: ignore[attr-defined]
            raise TodoistAPIError(str(exc), status=exc.status) from exc
        except TodoistAPIError:
            raise
        except Exception as exc:
            raise TodoistAPIError(str(exc), status=None) from exc

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
                return resp.json()
        except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
            raise TodoistAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except TodoistAPIError:
            raise
        except Exception as exc:
            raise TodoistAPIError(str(exc), status=None) from exc

    raise TodoistAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _shape_task(t: Any) -> dict:
    """Flatten one Todoist task response row.

    ``due`` is a nested object that may be absent or carry any of
    ``date``, ``string``, ``datetime``, ``timezone``. We surface only
    the flat ``date`` + user-typed natural-language ``string`` (the
    fields the LLM benefits from echoing back); ``datetime`` and
    ``timezone`` are deliberately dropped.
    """
    if not isinstance(t, dict):
        return {}
    raw_due = t.get("due")
    due = raw_due if isinstance(raw_due, dict) else {}
    return {
        "id": t.get("id", ""),
        "content": t.get("content", ""),
        "description": t.get("description", "") or "",
        "priority": t.get("priority", 1),
        "due_date": due.get("date", "") or "",
        "due_string": due.get("string", "") or "",
        "labels": t.get("labels", []) or [],
        "project_id": t.get("project_id", "") or "",
        "url": t.get("url", "") or "",
    }


class TodoistPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        # Constructor-injected provider wins over the module-level setter.
        # Tests pass directly; production leaves this None and main.py wires
        # the module-level _token_provider_factory at startup.
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        # Every bearer token ever held this call, so _scrub strips it from
        # any log line / ToolResult.
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="todoist_list_tasks",
                description=(
                    "List active tasks from the user's Todoist account, "
                    "optionally narrowed by a Todoist natural-language "
                    "filter (e.g. 'today', '@home & p1', '#Work'), a "
                    "specific project_id, or a label. Returns id, "
                    "content, description, priority, due_date, "
                    "due_string, labels, project_id and url for each "
                    "task. Zero args = next 10 active tasks across "
                    "everything."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": (
                                "Todoist natural-language filter "
                                "(e.g. 'today', '@home & p1', '#Work')."
                            ),
                        },
                        "project_id": {
                            "type": "string",
                            "description": (
                                "Limit results to one project."
                            ),
                        },
                        "label": {
                            "type": "string",
                            "description": (
                                "Limit results to tasks with this label."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum tasks to return (default "
                                f"{_DEFAULT_LIMIT}, clamped to "
                                f"[{_MIN_LIMIT}, {_MAX_LIMIT}])."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="todoist_create_task",
                description=(
                    "Create a new active task in the user's Todoist "
                    "account via the real Todoist REST API. Returns the "
                    "created task's id, content, description, priority, "
                    "due_date, due_string, labels, project_id and url."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Task content / title.",
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Long-form task description (optional)."
                            ),
                        },
                        "due_string": {
                            "type": "string",
                            "description": (
                                "Natural-language due (e.g. 'tomorrow at "
                                "5pm') -- Todoist parses it server-side."
                            ),
                        },
                        "priority": {
                            "type": "integer",
                            "description": (
                                "Priority 1 (normal) to 4 (urgent)."
                            ),
                        },
                        "project_id": {
                            "type": "string",
                            "description": (
                                "Project to add the task to (optional)."
                            ),
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of labels to attach (optional)."
                            ),
                        },
                    },
                    "required": ["content"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "todoist_list_tasks":
            return await self._list_tasks(args)
        if tool_name == "todoist_create_task":
            return await self._create_task(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None.

        ``error_string`` is a canonical message so callers can return
        ``ToolResult(content=err, is_error=True)`` directly (the
        gmail.py / calendar.py posture)."""
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
        """Strip every bearer token seen this call from text bound for
        a log or ToolResult."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider) -> str:
        """Get the static API token from the provider. Records it for
        scrubbing. No refresh path -- Todoist's token is static."""
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
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json=json,
        )

    def _clamp_max_results(self, raw: Any) -> int:
        try:
            value = int(raw) if raw is not None else _DEFAULT_LIMIT
        except (TypeError, ValueError):
            value = _DEFAULT_LIMIT
        return max(_MIN_LIMIT, min(_MAX_LIMIT, value))

    async def _list_tasks(self, args: dict) -> ToolResult:
        max_results = self._clamp_max_results(args.get("max_results"))
        params: dict = {}
        for key in ("filter", "project_id", "label"):
            value = args.get(key)
            if isinstance(value, str) and value:
                params[key] = value

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            tasks = await self._request(
                "GET", "tasks", token, params=params,
            )
        except Exception as exc:
            logger.error(
                "[todoist] todoist_list_tasks failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Todoist request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(tasks, list):
            return ToolResult(
                content="unexpected Todoist list response", is_error=True,
            )

        shaped = [_shape_task(t) for t in tasks[:max_results]]
        return ToolResult(content=json.dumps({"tasks": shaped}))

    async def _create_task(self, args: dict) -> ToolResult:
        content = args.get("content")
        if not content or not isinstance(content, str):
            return ToolResult(
                content=_MISSING_CREATE_ARG_MSG.format("content"),
                is_error=True,
            )

        body: dict = {"content": content}
        for key in ("description", "due_string", "project_id"):
            value = args.get(key)
            if isinstance(value, str) and value:
                body[key] = value
        priority = args.get("priority")
        if isinstance(priority, int) and not isinstance(priority, bool) \
                and 1 <= priority <= 4:
            body["priority"] = priority
        labels = args.get("labels")
        if isinstance(labels, list):
            cleaned = [l for l in labels if isinstance(l, str) and l]
            if cleaned:
                body["labels"] = cleaned

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider)
            resp = await self._request("POST", "tasks", token, json=body)
        except Exception as exc:
            logger.error(
                "[todoist] todoist_create_task failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Todoist request failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Todoist create response", is_error=True,
            )
        return ToolResult(content=json.dumps(_shape_task(resp)))


def create(fetch_fn: FetchFn | None = None) -> TodoistPlugin:
    return TodoistPlugin(fetch_fn=fetch_fn)
