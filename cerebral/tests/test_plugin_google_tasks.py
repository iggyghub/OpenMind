"""
Google Tasks MCP plugin tests -- Issue #227.

Tools: tasks_list_tasklists, tasks_list, tasks_create, tasks_complete,
tasks_delete (real Google Tasks API, OAuth bearer from the #112 store via
#113). All HTTP is injected via fetch_fn and the bearer token via a stub
provider, so tests never read the keyring, run OAuth, or hit the network.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.google_tasks import (  # noqa: E402
    GoogleTasksPlugin,
    REQUIRED_CAPABILITIES,
    TasksAPIError,
    create,
)

_BASE = "https://www.googleapis.com/tasks/v1"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _StubProvider:
    """Bearer-token provider double. ``current_token`` is the stored token
    (None to force a refresh on first use); ``refresh()`` hands out
    ``refresh_tokens`` in order and counts calls."""

    def __init__(self, current_token=None, refresh_tokens=("refreshed",)):
        self._current = current_token
        self._refresh_tokens = list(refresh_tokens)
        self.refresh_calls = 0

    def current(self):
        return self._current

    def refresh(self):
        self.refresh_calls += 1
        tok = self._refresh_tokens[
            min(self.refresh_calls - 1, len(self._refresh_tokens) - 1)
        ]
        self._current = tok
        return tok


def _route_fetch(routes, captured):
    """Build a fetch_fn that dispatches on a substring of the URL.

    ``routes`` maps a URL substring -> response | Exception | callable().
    Every call is appended to ``captured`` as a dict including the json
    body (POSTs).
    """
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        captured.append({
            "method": method, "url": url,
            "headers": headers or {}, "params": params or {},
            "json": json or {},
        })
        for needle, resp in routes.items():
            if needle in url:
                if callable(resp) and not isinstance(resp, BaseException):
                    resp = resp()
                if isinstance(resp, BaseException):
                    raise resp
                return resp
        raise AssertionError(f"no route for {url}")
    return fake_fetch


def _tasklist(tlid, *, title="My Tasks"):
    return {"id": tlid, "title": title}


def _task(tid, *, title="Task 1", status="needsAction", notes="", due=""):
    task = {"id": tid, "title": title, "status": status}
    if notes:
        task["notes"] = notes
    if due:
        task["due"] = due
    return task


def _tasklists_resp(*tasklists):
    return {"items": list(tasklists)}


def _tasks_resp(*tasks):
    return {"items": list(tasks)}


_CREATE_TASK_OK = {
    "id": "task-1",
    "title": "New Task",
    "status": "needsAction",
}

_COMPLETE_TASK_OK = {
    "id": "task-1",
    "status": "completed",
}


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_all_tools(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "tasks_list_tasklists",
            "tasks_list",
            "tasks_create",
            "tasks_complete",
            "tasks_delete",
        }

    def test_create_plugin_named_google_tasks(self):
        assert create().name == "google_tasks"

    def test_required_capabilities(self):
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_list_tasklists_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "tasks_list_tasklists"
        )
        assert tool.schema.get("required", []) == []
        assert "max_results" in tool.schema["properties"]

    def test_list_tasks_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "tasks_list"
        )
        assert tool.schema.get("required", []) == ["tasklist_id"]
        assert "max_results" in tool.schema["properties"]

    def test_create_task_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "tasks_create"
        )
        assert set(tool.schema.get("required", [])) == {"tasklist_id", "title"}
        for opt in ("notes", "due"):
            assert opt in tool.schema["properties"]

    def test_complete_task_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "tasks_complete"
        )
        assert set(tool.schema.get("required", [])) == {"tasklist_id", "task_id"}

    def test_delete_task_args_schema(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "tasks_delete"
        )
        assert set(tool.schema.get("required", [])) == {"tasklist_id", "task_id"}


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no connected account
# ---------------------------------------------------------------------------

class TestNoAccount:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.google_tasks as tasks_mod
        monkeypatch.setattr(tasks_mod, "_token_provider_factory", None)

        plugin = create()
        result = await plugin.call_tool("tasks_list_tasklists", {})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_account(self, monkeypatch):
        import plugins.google_tasks as tasks_mod
        monkeypatch.setattr(
            tasks_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("tasks_list_tasklists", {})
        assert result.is_error
        assert "no Google account connected" in result.content


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_tasks_list_missing_tasklist_id_is_error(self):
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("tasks_list", {})
        assert result.is_error
        assert "tasklist_id" in result.content

    @pytest.mark.asyncio
    async def test_tasks_create_missing_title_is_error(self):
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("tasks_create", {
            "tasklist_id": "list-1",
        })
        assert result.is_error
        assert "title" in result.content

    @pytest.mark.asyncio
    async def test_tasks_create_missing_tasklist_id_is_error(self):
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("tasks_create", {
            "title": "Task",
        })
        assert result.is_error
        assert "tasklist_id" in result.content

    @pytest.mark.asyncio
    async def test_tasks_complete_missing_args_is_error(self):
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("tasks_complete", {})
        assert result.is_error
        assert "tasklist_id" in result.content or "task_id" in result.content

    @pytest.mark.asyncio
    async def test_tasks_delete_missing_task_id_is_error(self):
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("tasks_delete", {
            "tasklist_id": "list-1",
        })
        assert result.is_error
        assert "task_id" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- tasks_list_tasklists
# ---------------------------------------------------------------------------

class TestListTasklists:
    @pytest.mark.asyncio
    async def test_list_tasklists_shapes_results(self):
        captured: list = []
        routes = {
            "/users/me/lists": _tasklists_resp(
                _tasklist("list-1", title="Personal"),
                _tasklist("list-2", title="Work"),
            ),
        }
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool("tasks_list_tasklists", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["tasklists"]) == 2
        assert data["tasklists"][0] == {"id": "list-1", "title": "Personal"}
        assert data["tasklists"][1] == {"id": "list-2", "title": "Work"}
        call = captured[0]
        assert call["method"] == "GET"
        assert "/users/me/lists" in call["url"]
        assert call["params"]["maxResults"] == 10

    @pytest.mark.asyncio
    async def test_list_tasklists_custom_max_results(self):
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/users/me/lists": _tasklists_resp()}, captured),
        )
        await plugin.call_tool("tasks_list_tasklists", {"max_results": 5})
        assert captured[0]["params"]["maxResults"] == 5

    @pytest.mark.asyncio
    async def test_list_tasklists_empty(self):
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/users/me/lists": {"items": []}}, []),
        )
        result = await plugin.call_tool("tasks_list_tasklists", {})
        assert not result.is_error
        assert json.loads(result.content) == {"tasklists": []}


# ---------------------------------------------------------------------------
# Cycle 5 -- tasks_list
# ---------------------------------------------------------------------------

class TestListTasks:
    @pytest.mark.asyncio
    async def test_list_tasks_shapes_results(self):
        captured: list = []
        routes = {
            "/lists/list-1/tasks": _tasks_resp(
                _task("t1", title="First", notes="Note 1"),
                _task("t2", title="Second", status="completed"),
            ),
        }
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool("tasks_list", {"tasklist_id": "list-1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["title"] == "First"
        assert data["tasks"][0]["notes"] == "Note 1"
        assert data["tasks"][1]["status"] == "completed"
        call = captured[0]
        assert call["method"] == "GET"
        assert "/lists/list-1/tasks" in call["url"]

    @pytest.mark.asyncio
    async def test_list_tasks_custom_max_results(self):
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/lists/list-1/tasks": _tasks_resp()}, captured),
        )
        await plugin.call_tool("tasks_list", {
            "tasklist_id": "list-1",
            "max_results": 3,
        })
        assert captured[0]["params"]["maxResults"] == 3

    @pytest.mark.asyncio
    async def test_list_tasks_respects_max_results_cap(self):
        tasks = [_task(f"t{i}") for i in range(5)]
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/lists/list-1/tasks": _tasks_resp(*tasks)}, []),
        )
        result = await plugin.call_tool("tasks_list", {
            "tasklist_id": "list-1",
            "max_results": 2,
        })
        assert len(json.loads(result.content)["tasks"]) == 2


# ---------------------------------------------------------------------------
# Cycle 6 -- tasks_create
# ---------------------------------------------------------------------------

class TestCreateTask:
    @pytest.mark.asyncio
    async def test_create_task_builds_body_and_posts(self):
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/lists/list-1/tasks": _CREATE_TASK_OK}, captured),
        )
        result = await plugin.call_tool("tasks_create", {
            "tasklist_id": "list-1",
            "title": "New Task",
            "notes": "Do this soon",
            "due": "2026-07-01T00:00:00Z",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "task-1"
        assert data["title"] == "New Task"
        assert data["status"] == "created"
        call = captured[0]
        assert call["method"] == "POST"
        assert "/lists/list-1/tasks" in call["url"]
        assert call["json"]["title"] == "New Task"
        assert call["json"]["notes"] == "Do this soon"
        assert call["json"]["due"] == "2026-07-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_create_task_without_optional_fields(self):
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/lists/list-1/tasks": _CREATE_TASK_OK}, captured),
        )
        result = await plugin.call_tool("tasks_create", {
            "tasklist_id": "list-1",
            "title": "Simple Task",
        })
        assert not result.is_error
        body = captured[0]["json"]
        assert body["title"] == "Simple Task"
        assert "notes" not in body
        assert "due" not in body


# ---------------------------------------------------------------------------
# Cycle 7 -- tasks_complete
# ---------------------------------------------------------------------------

class TestCompleteTask:
    @pytest.mark.asyncio
    async def test_complete_task_patches_status(self):
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/lists/list-1/tasks/task-1": _COMPLETE_TASK_OK}, captured
            ),
        )
        result = await plugin.call_tool("tasks_complete", {
            "tasklist_id": "list-1",
            "task_id": "task-1",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "task-1"
        assert data["status"] == "completed"
        call = captured[0]
        assert call["method"] == "PATCH"
        assert call["json"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Cycle 8 -- tasks_delete
# ---------------------------------------------------------------------------

class TestDeleteTask:
    @pytest.mark.asyncio
    async def test_delete_task_sends_delete_request(self):
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/lists/list-1/tasks/task-1": {}}, captured
            ),
        )
        result = await plugin.call_tool("tasks_delete", {
            "tasklist_id": "list-1",
            "task_id": "task-1",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "deleted"
        call = captured[0]
        assert call["method"] == "DELETE"
        assert "/lists/list-1/tasks/task-1" in call["url"]


# ---------------------------------------------------------------------------
# Cycle 9 -- 401 -> refresh -> retry once
# ---------------------------------------------------------------------------

class TestRefreshRetry:
    @pytest.mark.asyncio
    async def test_list_tasklists_401_triggers_refresh_and_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise TasksAPIError("401 Unauthorized", status=401)
            return _tasklists_resp(_tasklist("list-1"))

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/users/me/lists": route}, captured),
        )
        result = await plugin.call_tool("tasks_list_tasklists", {})
        assert not result.is_error
        assert prov.refresh_calls == 1
        retry = [c for c in captured if "/users/me/lists" in c["url"]][-1]
        assert retry["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_list_tasks_401_triggers_refresh_and_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise TasksAPIError("401 Unauthorized", status=401)
            return _tasks_resp(_task("t1"))

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/lists/list-1/tasks": route}, captured),
        )
        result = await plugin.call_tool("tasks_list", {"tasklist_id": "list-1"})
        assert not result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_create_401_triggers_refresh_and_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise TasksAPIError("401 Unauthorized", status=401)
            return _CREATE_TASK_OK

        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/lists/list-1/tasks": route}, captured),
        )
        result = await plugin.call_tool("tasks_create", {
            "tasklist_id": "list-1",
            "title": "Task",
        })
        assert not result.is_error
        assert prov.refresh_calls == 1
        assert captured[-1]["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_persistent_401_refreshes_once_then_errors(self):
        prov = _StubProvider(current_token="stale",
                             refresh_tokens=("fresh",))
        plugin = GoogleTasksPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/users/me/lists": TasksAPIError("401", status=401)}, []
            ),
        )
        result = await plugin.call_tool("tasks_list_tasklists", {})
        assert result.is_error
        assert prov.refresh_calls == 1


# ---------------------------------------------------------------------------
# Cycle 10 -- Bearer header + token scrub
# ---------------------------------------------------------------------------

class TestBearerHeaderAndScrub:
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_every_call(self):
        captured: list = []
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(
                {"/users/me/lists": _tasklists_resp(_tasklist("list-1"))}, captured
            ),
        )
        await plugin.call_tool("tasks_list_tasklists", {})
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = TasksAPIError(
            f"401 Client Error (Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider(sentinel,
                                         refresh_tokens=(sentinel,)),
            fetch_fn=_route_fetch({"/users/me/lists": exc}, []),
        )
        result = await plugin.call_tool("tasks_list_tasklists", {})
        assert result.is_error
        assert sentinel not in result.content
        assert "***" in result.content

    @pytest.mark.asyncio
    async def test_token_never_in_logs(self, caplog):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = TasksAPIError(f"boom Bearer {sentinel}", status=500)
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({"/users/me/lists": exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("tasks_list_tasklists", {})
        assert sentinel not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 11 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = GoogleTasksPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("tasks_bogus", {})
        assert result.is_error
        assert "Unknown tool" in result.content

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), GoogleTasksPlugin)
