"""
Google Tasks MCP plugin -- Issue #227, ADR-0005.

Five tools, hitting the **real Google Tasks API**, authorized by the active
profile's OAuth **access token** read from the #112 credential store
(refreshed on demand via #113):

  - ``tasks_list_tasklists``  -- TaskLists API ``tasklists.list`` (read-only).
  - ``tasks_list``            -- Tasks API ``tasks.list`` from a tasklist
                                 (read-only).
  - ``tasks_create``          -- Tasks API ``tasks.insert`` (write,
                                 ask-class ``external_data_write``).
  - ``tasks_complete``        -- Tasks API ``tasks.update`` to mark task
                                 complete (write, ask-class
                                 ``external_data_write``).
  - ``tasks_delete``          -- Tasks API ``tasks.delete`` (write,
                                 ask-class ``external_data_write``).

The bearer token reaches the active profile via a module-level
``set_token_provider`` setter wired from ``cerebral/main.py`` -- the exact
``plugins/gmail.py`` / ``plugins/calendar.py`` / ``plugins/google_sheets.py``
precedent. Tests bypass it by passing a stub provider + stub ``fetch_fn`` to
the constructor: no real network, OAuth, keyring or browser in the suite.

**No live-verify in this slice.** Live verification is batched into slice
V.1 (#239) so the autonomous loop is never blocked on browser OAuth consent.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_tasks"

# ADR-0005 / Issue #227.
#   - external_data_read + network_egress_cloud: tasks_list_tasklists and
#     tasks_list fetch data from www.googleapis.com over the internet (the
#     gmail.py / calendar.py surface).
#   - secrets_read is a DELIBERATE over-declaration -- the gmail.py /
#     calendar.py posture-B precedent (clone it, do NOT contrast it).
#   - external_data_write (tasks_create, tasks_complete, tasks_delete): these
#     tools mutate an external account (the active profile's task lists).
#     Hand-declared: external_data_* is absent from the AST capability map
#     AND the bare-attr fallback.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://www.googleapis.com/tasks/v1"
_DEFAULT_LIMIT = 10

_FACTORY_NOT_WIRED_MSG = "Google Tasks is not available -- token provider not wired"
_NO_ACCOUNT_MSG = "no Google account connected"
_MISSING_LIST_ARGS_MSG = "missing required arg(s) for tasks_list: {}"
_MISSING_CREATE_ARGS_MSG = "missing required arg(s) for tasks_create: {}"
_MISSING_COMPLETE_ARGS_MSG = "missing required arg(s) for tasks_complete: {}"
_MISSING_DELETE_ARGS_MSG = "missing required arg(s) for tasks_delete: {}"


class TokenProvider(Protocol):
    """Per-active-profile bearer-token handle wired from main.py.

    ``current()`` returns the stored access token (no network) or ``None``;
    ``refresh()`` exchanges the stored refresh token for a fresh access
    token via #113 (the 401 path) and returns it.
    """

    def current(self) -> Optional[str]: ...
    def refresh(self) -> str: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_google_tasks_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin."""
    global _token_provider_factory
    _token_provider_factory = fn


class TasksAPIError(RuntimeError):
    """Any transport/HTTP failure of a Tasks call. ``status`` is the HTTP
    status code when available (so the 401 -> refresh -> retry path can
    branch on it), else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    HTTP errors are mapped to TasksAPIError carrying the status code so the
    caller can branch on 401; transport/other errors carry status=None.
    Deps lazy-imported here so module import stays stdlib-only.
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
            raise TasksAPIError(str(exc), status=exc.status) from exc
        except TasksAPIError:
            raise
        except Exception as exc:
            raise TasksAPIError(str(exc), status=None) from exc

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
            raise TasksAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except TasksAPIError:
            raise
        except Exception as exc:
            raise TasksAPIError(str(exc), status=None) from exc

    raise TasksAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _shape_tasklist(tl: Any) -> dict:
    """Flatten one tasklist response row."""
    if not isinstance(tl, dict):
        return {}
    return {
        "id": tl.get("id", ""),
        "title": tl.get("title", ""),
    }


def _shape_task(task: Any) -> dict:
    """Flatten one task response row."""
    if not isinstance(task, dict):
        return {}
    return {
        "id": task.get("id", ""),
        "title": task.get("title", ""),
        "status": task.get("status", ""),
        "notes": task.get("notes", ""),
        "due": task.get("due", ""),
    }


class GoogleTasksPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="tasks_list_tasklists",
                description=(
                    "List all task lists in the active profile's Google Tasks."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum task lists to return (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="tasks_list",
                description=(
                    "List all tasks in a specific task list. Returns task id, "
                    "title, status, notes and due date."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "tasklist_id": {
                            "type": "string",
                            "description": "The task list ID.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum tasks to return (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                    "required": ["tasklist_id"],
                },
            ),
            Tool(
                name="tasks_create",
                description=(
                    "Create a new task in a specific task list via the real "
                    "Google Tasks API."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "tasklist_id": {
                            "type": "string",
                            "description": "The task list ID.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Task title.",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Task notes (optional).",
                        },
                        "due": {
                            "type": "string",
                            "description": "Due date in RFC 3339 format (optional).",
                        },
                    },
                    "required": ["tasklist_id", "title"],
                },
            ),
            Tool(
                name="tasks_complete",
                description=(
                    "Mark a task as completed in a specific task list."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "tasklist_id": {
                            "type": "string",
                            "description": "The task list ID.",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "The task ID.",
                        },
                    },
                    "required": ["tasklist_id", "task_id"],
                },
            ),
            Tool(
                name="tasks_delete",
                description=(
                    "Delete a task from a specific task list."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "tasklist_id": {
                            "type": "string",
                            "description": "The task list ID.",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "The task ID.",
                        },
                    },
                    "required": ["tasklist_id", "task_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "tasks_list_tasklists":
            return await self._list_tasklists(args)
        if tool_name == "tasks_list":
            return await self._list(args)
        if tool_name == "tasks_create":
            return await self._create(args)
        if tool_name == "tasks_complete":
            return await self._complete(args)
        if tool_name == "tasks_delete":
            return await self._delete(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None."""
        factory = self._token_factory()
        if self._provider is not None:
            return self._provider, None
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        provider = factory()
        if provider is None:
            return None, _NO_ACCOUNT_MSG
        return provider, None

    @staticmethod
    def _token_factory() -> Optional[TokenProviderFactory]:
        return _token_provider_factory

    def _scrub(self, text: str) -> str:
        """Strip every bearer token seen this call from text bound for a log
        or ToolResult."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider, *, force: bool) -> str:
        """Get a bearer token: refresh on ``force`` (the 401 path) or when
        none is stored, else the stored token. Records it for scrubbing."""
        token = None if force else provider.current()
        if not token:
            token = provider.refresh()
        if token:
            self._seen_tokens.add(token)
        return token or ""

    async def _get_tasklists(self, token: str,
                             params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/users/me/lists",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _get_tasks(self, token: str, tasklist_id: str,
                         params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/lists/{tasklist_id}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _post_task(self, token: str, tasklist_id: str,
                         body: dict) -> Any:
        return await self._fetch(
            "POST",
            f"{_BASE}/lists/{tasklist_id}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _patch_task(self, token: str, tasklist_id: str,
                          task_id: str, body: dict) -> Any:
        return await self._fetch(
            "PATCH",
            f"{_BASE}/lists/{tasklist_id}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _delete_task(self, token: str, tasklist_id: str,
                           task_id: str) -> Any:
        return await self._fetch(
            "DELETE",
            f"{_BASE}/lists/{tasklist_id}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    async def _list_tasklists(self, args: dict) -> ToolResult:
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params = {"maxResults": max_results}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                listing = await self._get_tasklists(token, params=params)
            except TasksAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                listing = await self._get_tasklists(token, params=params)
        except Exception as exc:
            logger.error(
                "[google_tasks] tasks_list_tasklists failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Tasks list failed: {exc}"),
                is_error=True,
            )

        if not isinstance(listing, dict):
            return ToolResult(
                content="unexpected Tasks tasklists response", is_error=True
            )
        items = [
            tl for tl in (listing.get("items") or [])
            if isinstance(tl, dict)
        ][:max_results]
        return ToolResult(content=json.dumps({"tasklists": [
            _shape_tasklist(tl) for tl in items
        ]}))

    async def _list(self, args: dict) -> ToolResult:
        tasklist_id = args.get("tasklist_id")
        if not tasklist_id or not isinstance(tasklist_id, str):
            return ToolResult(
                content=_MISSING_LIST_ARGS_MSG.format("tasklist_id"),
                is_error=True,
            )

        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params = {"maxResults": max_results}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                listing = await self._get_tasks(token, tasklist_id,
                                                params=params)
            except TasksAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                listing = await self._get_tasks(token, tasklist_id,
                                                params=params)
        except Exception as exc:
            logger.error(
                "[google_tasks] tasks_list failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Tasks list failed: {exc}"),
                is_error=True,
            )

        if not isinstance(listing, dict):
            return ToolResult(
                content="unexpected Tasks list response", is_error=True
            )
        items = [
            t for t in (listing.get("items") or [])
            if isinstance(t, dict)
        ][:max_results]
        return ToolResult(content=json.dumps({"tasks": [
            _shape_task(t) for t in items
        ]}))

    async def _create(self, args: dict) -> ToolResult:
        tasklist_id = args.get("tasklist_id")
        title = args.get("title")
        missing = [
            name for name in ("tasklist_id", "title")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_CREATE_ARGS_MSG.format(", ".join(missing)),
                is_error=True,
            )

        body: dict = {"title": title}
        notes = args.get("notes")
        if isinstance(notes, str) and notes:
            body["notes"] = notes
        due = args.get("due")
        if isinstance(due, str) and due:
            body["due"] = due

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._post_task(token, tasklist_id, body)
            except TasksAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._post_task(token, tasklist_id, body)
        except Exception as exc:
            logger.error(
                "[google_tasks] tasks_create failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Tasks create failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Tasks create response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": resp.get("id", ""),
            "title": resp.get("title", ""),
            "status": "created",
        }))

    async def _complete(self, args: dict) -> ToolResult:
        tasklist_id = args.get("tasklist_id")
        task_id = args.get("task_id")
        missing = [
            name for name in ("tasklist_id", "task_id")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_COMPLETE_ARGS_MSG.format(", ".join(missing)),
                is_error=True,
            )

        body = {"status": "completed"}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._patch_task(token, tasklist_id, task_id,
                                              body)
            except TasksAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._patch_task(token, tasklist_id, task_id,
                                              body)
        except Exception as exc:
            logger.error(
                "[google_tasks] tasks_complete failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Tasks complete failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Tasks complete response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "id": resp.get("id", ""),
            "status": resp.get("status", ""),
        }))

    async def _delete(self, args: dict) -> ToolResult:
        tasklist_id = args.get("tasklist_id")
        task_id = args.get("task_id")
        missing = [
            name for name in ("tasklist_id", "task_id")
            if not args.get(name) or not isinstance(args.get(name), str)
        ]
        if missing:
            return ToolResult(
                content=_MISSING_DELETE_ARGS_MSG.format(", ".join(missing)),
                is_error=True,
            )

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                await self._delete_task(token, tasklist_id, task_id)
            except TasksAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                await self._delete_task(token, tasklist_id, task_id)
        except Exception as exc:
            logger.error(
                "[google_tasks] tasks_delete failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Tasks delete failed: {exc}"),
                is_error=True,
            )

        return ToolResult(content=json.dumps({"status": "deleted"}))


def create(fetch_fn: FetchFn | None = None) -> GoogleTasksPlugin:
    """Zero-arg-by-default factory the orchestrator discovers.

    ``fetch_fn`` is for tests; production leaves it None. The token
    provider is wired separately via ``set_token_provider`` from
    ``cerebral/main.py``."""
    return GoogleTasksPlugin(fetch_fn=fetch_fn)
