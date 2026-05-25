"""
Todoist MCP plugin tests -- Issue #130 + Issue #133.

Tools (six):
  - todoist_list_tasks (read, GET /tasks)
  - todoist_create_task (write, POST /tasks)
  - todoist_update_task (write, POST /tasks/{id}, PATCH-semantic)
  - todoist_complete_task (write, POST /tasks/{id}/close, 204 empty)
  - todoist_reopen_task (write, POST /tasks/{id}/reopen, 204 empty)
  - todoist_delete_task (write, DELETE /tasks/{id}, 204 empty)

All hit the real Todoist REST API v1 with a static API token from the
TODOIST_API_TOKEN env var via the provider seam. HTTP is injected via
fetch_fn and the token via a stub provider, so tests never read
os.environ, hit the keyring, or touch the network.

Learning-#15 substitution case (Bearer transport, plugin-specific
value = secret handling): the suite asserts the Bearer header IS
attached AND the token never reaches a log / ToolResult, INSTEAD of a
fake-transport regression test (the youtube/gmail/calendar precedent
when transport itself carries no plugin-specific value).

Protocol-narrowing divergence from gmail/calendar: Todoist's
TokenProvider carries only current() (no refresh, no OAuth). A 401
propagates straight through with NO refresh-and-retry -- the suite
asserts exactly ONE outbound request on both paths.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.todoist import (  # noqa: E402
    REQUIRED_CAPABILITIES,
    TodoistAPIError,
    TodoistPlugin,
    create,
)

_BASE = "https://api.todoist.com/api/v1"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _StubProvider:
    """Static-token provider double. ``current()`` returns ``token`` (which
    can be ``None`` to model the never-set case). No refresh() -- the
    Protocol is one-method by design."""

    def __init__(self, token=None):
        self._token = token
        self.current_calls = 0

    def current(self):
        self.current_calls += 1
        return self._token


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


def _task(tid, *, content="Buy milk", description="", priority=1,
          due=None, labels=None, project_id="proj-1",
          url="https://todoist.com/showTask?id=1"):
    out = {
        "id": tid,
        "content": content,
        "description": description,
        "priority": priority,
        "labels": list(labels) if labels else [],
        "project_id": project_id,
        "url": url,
    }
    if due is not None:
        out["due"] = due
    return out


_CREATE_OK = _task("task-1", content="Created task", priority=2,
                   labels=["home"], due={
                       "date": "2026-05-20", "string": "tomorrow",
                       "datetime": "2026-05-20T17:00:00Z", "timezone": "UTC",
                   })


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_six_tools(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "todoist_list_tasks",
            "todoist_create_task",
            "todoist_update_task",
            "todoist_complete_task",
            "todoist_reopen_task",
            "todoist_delete_task",
        }

    def test_create_plugin_named_todoist(self):
        assert create().name == "todoist"

    def test_required_capabilities(self):
        # secrets_read is a DELIBERATE over-declaration (gmail.py /
        # youtube.py posture-B); external_data_write is the correct
        # *required* ask-class semantic class for the create / update /
        # complete / reopen / delete tools. Both external_data_* are
        # hand-declared -- the per-file AST audit maps neither.
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_create_args_schema_required(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "todoist_create_task"
        )
        assert tool.schema.get("required", []) == ["content"]
        for opt in (
            "description", "due_string", "priority", "project_id", "labels"
        ):
            assert opt in tool.schema["properties"]

    def test_list_has_no_required_args(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "todoist_list_tasks"
        )
        assert tool.schema.get("required", []) == []
        for opt in ("filter", "project_id", "label", "max_results"):
            assert opt in tool.schema["properties"]

    def test_update_args_schema_required_id_others_optional(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "todoist_update_task"
        )
        assert tool.schema.get("required", []) == ["id"]
        for opt in (
            "content", "description", "due_string", "priority",
            "project_id", "labels",
        ):
            assert opt in tool.schema["properties"]

    @pytest.mark.parametrize("tool_name", [
        "todoist_complete_task",
        "todoist_reopen_task",
        "todoist_delete_task",
    ])
    def test_lifecycle_tools_require_only_id(self, tool_name):
        tool = next(
            t for t in create().list_tools() if t.name == tool_name
        )
        assert tool.schema.get("required", []) == ["id"]
        assert "id" in tool.schema["properties"]


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no token (constructs, lazy error) + setter seam
# ---------------------------------------------------------------------------

class TestNoToken:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.todoist as td_mod
        monkeypatch.setattr(td_mod, "_token_provider_factory", None)

        plugin = create()  # must not raise
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_token(self, monkeypatch):
        import plugins.todoist as td_mod
        monkeypatch.setattr(
            td_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert "no Todoist API token configured" in result.content

    @pytest.mark.asyncio
    async def test_no_token_lazy_error_on_create_too(self, monkeypatch):
        import plugins.todoist as td_mod
        monkeypatch.setattr(
            td_mod, "_token_provider_factory", lambda: None
        )
        plugin = create()
        result = await plugin.call_tool("todoist_create_task", {
            "content": "Hi",
        })
        assert result.is_error
        assert "no Todoist API token configured" in result.content

    @pytest.mark.parametrize("tool_name,args", [
        ("todoist_update_task", {"id": "t1", "content": "x"}),
        ("todoist_complete_task", {"id": "t1"}),
        ("todoist_reopen_task", {"id": "t1"}),
        ("todoist_delete_task", {"id": "t1"}),
    ])
    @pytest.mark.asyncio
    async def test_no_token_lazy_error_across_lifecycle_tools(
            self, monkeypatch, tool_name, args):
        import plugins.todoist as td_mod
        monkeypatch.setattr(
            td_mod, "_token_provider_factory", lambda: None
        )
        plugin = create()
        result = await plugin.call_tool(tool_name, args)
        assert result.is_error
        assert "no Todoist API token configured" in result.content

    @pytest.mark.asyncio
    async def test_module_setter_is_the_wiring_seam(self, monkeypatch):
        import plugins.todoist as td_mod
        prov = _StubProvider(token="tok")
        td_mod.set_token_provider(lambda: prov)
        try:
            captured: list = []
            plugin = TodoistPlugin(
                fetch_fn=_route_fetch({"/tasks": []}, captured),
            )
            result = await plugin.call_tool("todoist_list_tasks", {})
            assert not result.is_error
        finally:
            monkeypatch.setattr(
                td_mod, "_token_provider_factory", None
            )


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation (todoist_create_task)
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_missing_content_is_error(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("todoist_create_task", {})
        assert result.is_error
        assert "content" in result.content

    @pytest.mark.asyncio
    async def test_blank_content_is_error(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("todoist_create_task", {
            "content": "",
        })
        assert result.is_error
        assert "content" in result.content

    @pytest.mark.asyncio
    async def test_non_string_content_is_error(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("todoist_create_task", {
            "content": 123,
        })
        assert result.is_error
        assert "content" in result.content

    @pytest.mark.parametrize("tool_name", [
        "todoist_update_task",
        "todoist_complete_task",
        "todoist_reopen_task",
        "todoist_delete_task",
    ])
    @pytest.mark.parametrize("bad_args", [
        {},  # id missing
        {"id": ""},  # id blank
        {"id": 12345},  # id non-string
    ])
    @pytest.mark.asyncio
    async def test_missing_id_is_error(self, tool_name, bad_args):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool(tool_name, dict(bad_args))
        assert result.is_error
        assert "id" in result.content
        assert tool_name in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- todoist_list_tasks shaping + filters + clamp
# ---------------------------------------------------------------------------

class TestList:
    @pytest.mark.asyncio
    async def test_list_shapes_tasks(self):
        captured: list = []
        tasks = [
            _task("t1", content="First", priority=4,
                  due={"date": "2026-05-20", "string": "tomorrow"}),
            _task("t2", content="Second", labels=["home", "errands"]),
            _task("t3", content="Third", description="long form"),
        ]
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": tasks}, captured),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["tasks"]) == 3
        assert data["tasks"][0] == {
            "id": "t1", "content": "First", "description": "",
            "priority": 4, "due_date": "2026-05-20",
            "due_string": "tomorrow", "labels": [], "project_id": "proj-1",
            "url": "https://todoist.com/showTask?id=1",
        }
        assert data["tasks"][1]["labels"] == ["home", "errands"]
        assert data["tasks"][2]["description"] == "long form"
        call = captured[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{_BASE}/tasks"
        # No filter args -> no filter params
        assert call["params"] == {}

    @pytest.mark.asyncio
    async def test_due_timed_drops_datetime_and_timezone(self):
        task = _task("t-timed", due={
            "date": "2026-05-20", "string": "tomorrow at 5pm",
            "datetime": "2026-05-20T17:00:00Z", "timezone": "UTC",
        })
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": [task]}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        row = json.loads(result.content)["tasks"][0]
        assert row["due_date"] == "2026-05-20"
        assert row["due_string"] == "tomorrow at 5pm"
        # datetime and timezone are intentionally dropped
        assert "datetime" not in row
        assert "timezone" not in row

    @pytest.mark.asyncio
    async def test_due_all_day_works(self):
        task = _task("t-allday", due={
            "date": "2026-05-21", "string": "Wednesday",
        })
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": [task]}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        row = json.loads(result.content)["tasks"][0]
        assert row["due_date"] == "2026-05-21"
        assert row["due_string"] == "Wednesday"

    @pytest.mark.asyncio
    async def test_due_missing_is_empty_strings(self):
        task = _task("t-nodue")  # no `due` key
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": [task]}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        row = json.loads(result.content)["tasks"][0]
        assert row["due_date"] == ""
        assert row["due_string"] == ""

    @pytest.mark.asyncio
    async def test_filter_project_label_passthrough(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": []}, captured),
        )
        await plugin.call_tool("todoist_list_tasks", {
            "filter": "today",
            "project_id": "proj-x",
            "label": "home",
        })
        params = captured[0]["params"]
        assert params["filter"] == "today"
        assert params["project_id"] == "proj-x"
        assert params["label"] == "home"

    @pytest.mark.asyncio
    async def test_blank_filter_args_dropped(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": []}, captured),
        )
        await plugin.call_tool("todoist_list_tasks", {
            "filter": "",
            "project_id": "",
            "label": "",
        })
        assert captured[0]["params"] == {}

    @pytest.mark.asyncio
    async def test_max_results_clamped_low(self):
        tasks = [_task(f"t{i}") for i in range(50)]
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": tasks}, []),
        )
        # 0 clamps to 1
        result = await plugin.call_tool(
            "todoist_list_tasks", {"max_results": 0}
        )
        assert len(json.loads(result.content)["tasks"]) == 1
        # negative clamps to 1
        result = await plugin.call_tool(
            "todoist_list_tasks", {"max_results": -100}
        )
        assert len(json.loads(result.content)["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_max_results_clamped_high(self):
        # 250 tasks server-side, max_results=500 caps to 200
        tasks = [_task(f"t{i}") for i in range(250)]
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": tasks}, []),
        )
        result = await plugin.call_tool(
            "todoist_list_tasks", {"max_results": 500}
        )
        assert len(json.loads(result.content)["tasks"]) == 200

    @pytest.mark.asyncio
    async def test_default_max_results_is_ten(self):
        tasks = [_task(f"t{i}") for i in range(25)]
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": tasks}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert len(json.loads(result.content)["tasks"]) == 10

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_tasks(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": []}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert not result.is_error
        assert json.loads(result.content) == {"tasks": []}

    @pytest.mark.asyncio
    async def test_non_list_response_is_error(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": {"oops": True}}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert "unexpected Todoist list response" in result.content

    @pytest.mark.asyncio
    async def test_list_accepts_v2_envelope(self):
        # Issue #155: Todoist REST returns GET /tasks wrapped in a
        # ``{"results": [...], "next_cursor": ...}`` envelope. The
        # plugin must round-trip the envelope shape identically to the
        # legacy bare-array shape.
        tasks = [
            _task("t1", content="First", priority=4,
                  due={"date": "2026-05-20", "string": "tomorrow"}),
            _task("t2", content="Second", labels=["home"]),
        ]
        envelope = {"results": tasks, "next_cursor": None}
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": envelope}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["tasks"]) == 2
        assert data["tasks"][0]["id"] == "t1"
        assert data["tasks"][0]["content"] == "First"
        assert data["tasks"][1]["labels"] == ["home"]

    @pytest.mark.asyncio
    async def test_list_bare_array_legacy_still_works(self):
        # Regression guard for #155: the fix must keep the legacy bare-
        # array shape working (older Todoist instances + the
        # gmail/youtube-style transport stubs in this suite).
        tasks = [_task("t1", content="Legacy")]
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": tasks}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["tasks"][0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_envelope_with_non_list_results_is_error(self):
        # An envelope whose ``results`` is not a list should still error
        # cleanly — the unwrap helper only accepts list-shaped results.
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/tasks": {"results": {"oops": True}}}, [],
            ),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert "unexpected Todoist list response" in result.content

    @pytest.mark.asyncio
    async def test_unexpected_shape_logs_type_and_snippet(self, caplog):
        # Per issue #155: when the response shape doesn't match either
        # the bare array or the envelope, the WARN log must carry the
        # raw type + a snippet so future drifts diagnose in minutes.
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/tasks": {"unexpected": "shape"}}, [],
            ),
        )
        with caplog.at_level(logging.WARNING):
            result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert "type=dict" in caplog.text
        assert "unexpected" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 5 -- todoist_create_task: POST body shape + response
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.asyncio
    async def test_create_minimal_posts_content_only(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": _CREATE_OK}, captured),
        )
        result = await plugin.call_tool("todoist_create_task", {
            "content": "Buy milk",
        })
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/tasks"
        assert call["headers"]["Authorization"] == "Bearer tok"
        # Minimal body -- no optional keys
        assert call["json"] == {"content": "Buy milk"}

    @pytest.mark.asyncio
    async def test_create_all_optionals_passthrough(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": _CREATE_OK}, captured),
        )
        await plugin.call_tool("todoist_create_task", {
            "content": "Plan trip",
            "description": "research dates and venue",
            "due_string": "next monday at 9am",
            "priority": 3,
            "project_id": "proj-42",
            "labels": ["travel", "personal"],
        })
        body = captured[0]["json"]
        assert body["content"] == "Plan trip"
        assert body["description"] == "research dates and venue"
        assert body["due_string"] == "next monday at 9am"
        assert body["priority"] == 3
        assert body["project_id"] == "proj-42"
        assert body["labels"] == ["travel", "personal"]

    @pytest.mark.asyncio
    async def test_create_response_is_single_shaped_task(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": _CREATE_OK}, []),
        )
        result = await plugin.call_tool("todoist_create_task", {
            "content": "Created task",
        })
        assert not result.is_error
        data = json.loads(result.content)
        # Single shaped dict, NOT wrapped in {"tasks": [...]}
        assert "tasks" not in data
        assert data["id"] == "task-1"
        assert data["content"] == "Created task"
        assert data["priority"] == 2
        assert data["due_date"] == "2026-05-20"
        assert data["due_string"] == "tomorrow"
        assert data["labels"] == ["home"]

    @pytest.mark.asyncio
    async def test_create_blank_labels_filtered(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": _CREATE_OK}, captured),
        )
        await plugin.call_tool("todoist_create_task", {
            "content": "Mixed",
            "labels": ["one", "", "two"],
        })
        body = captured[0]["json"]
        assert body["labels"] == ["one", "two"]

    @pytest.mark.asyncio
    async def test_create_invalid_priority_dropped(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": _CREATE_OK}, captured),
        )
        # 0 / 5 / "high" / True all out of [1, 4] for int -- dropped
        for bad in (0, 5, "high", True):
            captured.clear()
            await plugin.call_tool("todoist_create_task", {
                "content": "x", "priority": bad,
            })
            assert "priority" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_create_blank_optionals_dropped(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": _CREATE_OK}, captured),
        )
        await plugin.call_tool("todoist_create_task", {
            "content": "x",
            "description": "", "due_string": "",
            "project_id": "", "labels": [],
        })
        body = captured[0]["json"]
        assert body == {"content": "x"}

    @pytest.mark.asyncio
    async def test_create_non_dict_response_is_error(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks": [1, 2]}, []),
        )
        result = await plugin.call_tool("todoist_create_task", {
            "content": "x",
        })
        assert result.is_error
        assert "unexpected Todoist create response" in result.content


# ---------------------------------------------------------------------------
# Cycle 5b -- todoist_update_task (PATCH-semantic, full-shape response)
# ---------------------------------------------------------------------------

_UPDATE_OK = _task("t-upd", content="Updated content", priority=3,
                   labels=["work"], due={
                       "date": "2026-05-25", "string": "next monday",
                   })


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_minimal_id_only_posts_empty_body(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": _UPDATE_OK}, captured),
        )
        result = await plugin.call_tool("todoist_update_task", {
            "id": "t-upd",
        })
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/tasks/t-upd"
        # Empty body -- no other args supplied
        assert call["json"] == {}

    @pytest.mark.asyncio
    async def test_update_all_optionals_passthrough(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": _UPDATE_OK}, captured),
        )
        await plugin.call_tool("todoist_update_task", {
            "id": "t-upd",
            "content": "New title",
            "description": "Long form",
            "due_string": "next monday",
            "priority": 3,
            "project_id": "proj-x",
            "labels": ["a", "b"],
        })
        body = captured[0]["json"]
        assert body == {
            "content": "New title",
            "description": "Long form",
            "due_string": "next monday",
            "priority": 3,
            "project_id": "proj-x",
            "labels": ["a", "b"],
        }

    @pytest.mark.asyncio
    async def test_update_blank_strings_dropped(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": _UPDATE_OK}, captured),
        )
        await plugin.call_tool("todoist_update_task", {
            "id": "t-upd",
            "content": "",
            "description": "",
            "due_string": "",
            "project_id": "",
        })
        # PATCH-semantic: blank-string optionals must NOT be forwarded.
        # The LLM "omit" vs "clear" distinction is conservatively "omit".
        assert captured[0]["json"] == {}

    @pytest.mark.parametrize("bad_priority", [0, 5, "high", True])
    @pytest.mark.asyncio
    async def test_update_invalid_priority_dropped(self, bad_priority):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": _UPDATE_OK}, captured),
        )
        await plugin.call_tool("todoist_update_task", {
            "id": "t-upd",
            "priority": bad_priority,
        })
        assert "priority" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_update_blank_labels_filtered(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": _UPDATE_OK}, captured),
        )
        await plugin.call_tool("todoist_update_task", {
            "id": "t-upd",
            "labels": ["", "home", None, "", "work", 5],
        })
        # blanks/non-strings dropped, real strings preserved in order
        assert captured[0]["json"]["labels"] == ["home", "work"]

    @pytest.mark.asyncio
    async def test_update_empty_labels_list_dropped(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": _UPDATE_OK}, captured),
        )
        await plugin.call_tool("todoist_update_task", {
            "id": "t-upd",
            "labels": ["", None],
        })
        # All blanks filter out -> no labels key in body
        assert "labels" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_update_response_is_single_shaped_task(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": _UPDATE_OK}, []),
        )
        result = await plugin.call_tool("todoist_update_task", {
            "id": "t-upd",
            "content": "x",
        })
        assert not result.is_error
        data = json.loads(result.content)
        # Mirrors _shape_task -- the same flat shape create returns.
        assert data["id"] == "t-upd"
        assert data["content"] == "Updated content"
        assert data["priority"] == 3
        assert data["labels"] == ["work"]
        assert data["due_date"] == "2026-05-25"
        assert data["due_string"] == "next monday"

    @pytest.mark.asyncio
    async def test_update_non_dict_response_is_error(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-upd": ["bogus"]}, []),
        )
        result = await plugin.call_tool("todoist_update_task", {
            "id": "t-upd", "content": "x",
        })
        assert result.is_error
        assert "unexpected Todoist update response" in result.content


# ---------------------------------------------------------------------------
# Cycle 5c -- todoist_complete / reopen / delete (204 synthesized response)
# ---------------------------------------------------------------------------

class TestLifecycleTools:
    @pytest.mark.parametrize("tool_name,suffix,status", [
        ("todoist_complete_task", "/close", "completed"),
        ("todoist_reopen_task", "/reopen", "reopened"),
    ])
    @pytest.mark.asyncio
    async def test_close_reopen_endpoint_and_synthesized_body(
            self, tool_name, suffix, status):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({f"/tasks/t-1{suffix}": None}, captured),
        )
        result = await plugin.call_tool(tool_name, {"id": "t-1"})
        assert not result.is_error
        # URL + method correct
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/tasks/t-1{suffix}"
        # Plugin synthesizes the summary dict (Todoist returns 204 empty)
        assert json.loads(result.content) == {"id": "t-1", "status": status}

    @pytest.mark.asyncio
    async def test_delete_endpoint_and_synthesized_body(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/tasks/t-1": None}, captured),
        )
        result = await plugin.call_tool("todoist_delete_task", {"id": "t-1"})
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "DELETE"
        assert call["url"] == f"{_BASE}/tasks/t-1"
        assert json.loads(result.content) == {
            "id": "t-1", "status": "deleted",
        }

    @pytest.mark.parametrize("tool_name,suffix,error", [
        ("todoist_complete_task", "/close", TodoistAPIError(
            "404 Not Found", status=404)),
        ("todoist_reopen_task", "/reopen", TodoistAPIError(
            "404 Not Found", status=404)),
        ("todoist_delete_task", "", TodoistAPIError(
            "400 Invalid", status=400)),
    ])
    @pytest.mark.asyncio
    async def test_api_errors_surface_with_scrubbed_message(
            self, tool_name, suffix, error):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({f"/tasks/t-1{suffix}": error}, []),
        )
        result = await plugin.call_tool(tool_name, {"id": "t-1"})
        assert result.is_error
        assert "Todoist request failed" in result.content


# ---------------------------------------------------------------------------
# Cycle 5d -- _default_fetch 204 handling (transport-level)
# ---------------------------------------------------------------------------

class TestTransport204:
    @pytest.mark.asyncio
    async def test_aiohttp_204_returns_none(self, monkeypatch):
        """_default_fetch must return None on HTTP 204 (close/reopen/
        delete return an empty body). Stubs aiohttp at module scope."""
        from plugins.todoist import _default_fetch
        import plugins.todoist as td_mod

        class _Resp:
            status = 204
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def raise_for_status(self): pass
            async def json(self):  # pragma: no cover -- must NOT be called
                raise AssertionError("json() should not be called for 204")

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            def request(self, *a, **k): return _Resp()

        class _StubAiohttp:
            ClientSession = _Session

            class ClientResponseError(Exception): pass

        monkeypatch.setitem(sys.modules, "aiohttp", _StubAiohttp)
        result = await _default_fetch(
            "POST", "https://x/y", headers={}, params=None, json=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_httpx_204_returns_none(self, monkeypatch):
        from plugins.todoist import _default_fetch

        # Force the aiohttp branch to fall through.
        monkeypatch.setitem(sys.modules, "aiohttp", None)

        class _Resp:
            status_code = 204
            def raise_for_status(self): pass
            def json(self):  # pragma: no cover -- must NOT be called
                raise AssertionError("json() should not be called for 204")

        class _Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, *a, **k): return _Resp()

        class _StubHttpx:
            AsyncClient = _Client

            class HTTPStatusError(Exception): pass

        monkeypatch.setitem(sys.modules, "httpx", _StubHttpx)
        result = await _default_fetch(
            "POST", "https://x/y", headers={}, params=None, json=None,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Cycle 6 -- BEARER HEADER + token scrub (learning-#15 substitution)
# ---------------------------------------------------------------------------

class TestBearerHeaderAndScrub:
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_list(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch({"/tasks": []}, captured),
        )
        await plugin.call_tool("todoist_list_tasks", {})
        assert len(captured) == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_create(self):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("xyz789"),
            fetch_fn=_route_fetch({"/tasks": _CREATE_OK}, captured),
        )
        await plugin.call_tool("todoist_create_task", {"content": "x"})
        assert captured[0]["headers"]["Authorization"] == "Bearer xyz789"

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult_on_list(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = TodoistAPIError(
            "401 Client Error for url: "
            f"{_BASE}/tasks (Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = TodoistPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({"/tasks": exc}, []),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert sentinel not in result.content
        assert "***" in result.content

    @pytest.mark.asyncio
    async def test_token_never_in_logs_on_list(self, caplog):
        sentinel = "SENTINEL_LOG_TOKEN"
        exc = TodoistAPIError(f"boom Bearer {sentinel}", status=500)
        plugin = TodoistPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({"/tasks": exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("todoist_list_tasks", {})
        assert sentinel not in caplog.text
        assert "***" in caplog.text

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult_or_logs_on_create(self, caplog):
        sentinel = "SENTINEL_CREATE_TOKEN"
        exc = TodoistAPIError(
            f"401 for {_BASE}/tasks "
            f"(Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = TodoistPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({"/tasks": exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            result = await plugin.call_tool("todoist_create_task", {
                "content": "x",
            })
        assert result.is_error
        assert sentinel not in result.content
        assert sentinel not in caplog.text
        assert "***" in result.content

    @pytest.mark.parametrize("tool_name,args,route_needle,success_resp", [
        ("todoist_update_task", {"id": "t-1", "content": "x"},
         "/tasks/t-1", _UPDATE_OK),
        ("todoist_complete_task", {"id": "t-1"},
         "/tasks/t-1/close", None),
        ("todoist_reopen_task", {"id": "t-1"},
         "/tasks/t-1/reopen", None),
        ("todoist_delete_task", {"id": "t-1"},
         "/tasks/t-1", None),
    ])
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_lifecycle_tools(
            self, tool_name, args, route_needle, success_resp):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("life-tok"),
            fetch_fn=_route_fetch({route_needle: success_resp}, captured),
        )
        await plugin.call_tool(tool_name, dict(args))
        assert captured[0]["headers"]["Authorization"] == "Bearer life-tok"

    @pytest.mark.parametrize("tool_name,args,route_needle", [
        ("todoist_update_task", {"id": "t-1", "content": "x"}, "/tasks/t-1"),
        ("todoist_complete_task", {"id": "t-1"}, "/tasks/t-1/close"),
        ("todoist_reopen_task", {"id": "t-1"}, "/tasks/t-1/reopen"),
        ("todoist_delete_task", {"id": "t-1"}, "/tasks/t-1"),
    ])
    @pytest.mark.asyncio
    async def test_token_never_in_toolresult_or_logs_on_lifecycle_tools(
            self, caplog, tool_name, args, route_needle):
        sentinel = f"SENTINEL_{tool_name.upper()}_TOKEN"
        exc = TodoistAPIError(
            f"500 boom Bearer {sentinel}", status=500,
        )
        plugin = TodoistPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({route_needle: exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            result = await plugin.call_tool(tool_name, dict(args))
        assert result.is_error
        assert sentinel not in result.content
        assert sentinel not in caplog.text
        assert "***" in result.content


# ---------------------------------------------------------------------------
# Cycle 7 -- 401 propagates with NO retry (Protocol narrowing)
# ---------------------------------------------------------------------------

class TestNoRetryOn401:
    @pytest.mark.asyncio
    async def test_list_401_propagates_no_retry(self):
        captured: list = []
        prov = _StubProvider("tok")
        plugin = TodoistPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/tasks": TodoistAPIError("401 Unauthorized", status=401)},
                captured,
            ),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert "Todoist request failed" in result.content
        # Exactly ONE outbound request -- no refresh, no retry
        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_create_401_propagates_no_retry(self):
        captured: list = []
        prov = _StubProvider("tok")
        plugin = TodoistPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/tasks": TodoistAPIError("401 Unauthorized", status=401)},
                captured,
            ),
        )
        result = await plugin.call_tool("todoist_create_task", {
            "content": "x",
        })
        assert result.is_error
        assert "Todoist request failed" in result.content
        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_provider_has_no_refresh_method(self):
        # Protocol structural check: the stub provider has only current(),
        # mirroring the production TokenProvider Protocol. This pins the
        # divergence from gmail/calendar.
        prov = _StubProvider("tok")
        assert hasattr(prov, "current")
        assert not hasattr(prov, "refresh")

    @pytest.mark.asyncio
    async def test_non_401_also_propagates_no_retry(self):
        captured: list = []
        prov = _StubProvider("tok")
        plugin = TodoistPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/tasks": TodoistAPIError("500 Server Error", status=500)},
                captured,
            ),
        )
        result = await plugin.call_tool("todoist_list_tasks", {})
        assert result.is_error
        assert len(captured) == 1

    @pytest.mark.parametrize("tool_name,args,route_needle", [
        ("todoist_update_task", {"id": "t-1", "content": "x"}, "/tasks/t-1"),
        ("todoist_complete_task", {"id": "t-1"}, "/tasks/t-1/close"),
        ("todoist_reopen_task", {"id": "t-1"}, "/tasks/t-1/reopen"),
        ("todoist_delete_task", {"id": "t-1"}, "/tasks/t-1"),
    ])
    @pytest.mark.asyncio
    async def test_lifecycle_tool_401_propagates_no_retry(
            self, tool_name, args, route_needle):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {route_needle: TodoistAPIError(
                    "401 Unauthorized", status=401)},
                captured,
            ),
        )
        result = await plugin.call_tool(tool_name, dict(args))
        assert result.is_error
        assert "Todoist request failed" in result.content
        # Exactly ONE outbound request -- no refresh, no retry
        assert len(captured) == 1


# ---------------------------------------------------------------------------
# Cycle 8 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = TodoistPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("todoist_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'todoist_bogus'" in result.content

    @pytest.mark.parametrize("tool_name,args,route_needle,resp", [
        ("todoist_update_task", {"id": "t-1", "content": "x"},
         "/tasks/t-1", _UPDATE_OK),
        ("todoist_complete_task", {"id": "t-1"},
         "/tasks/t-1/close", None),
        ("todoist_reopen_task", {"id": "t-1"},
         "/tasks/t-1/reopen", None),
        ("todoist_delete_task", {"id": "t-1"},
         "/tasks/t-1", None),
    ])
    @pytest.mark.asyncio
    async def test_dispatch_routes_each_lifecycle_tool(
            self, tool_name, args, route_needle, resp):
        captured: list = []
        plugin = TodoistPlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({route_needle: resp}, captured),
        )
        result = await plugin.call_tool(tool_name, dict(args))
        assert not result.is_error
        # Exactly one outbound request -- proves dispatch reached the method
        assert len(captured) == 1

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), TodoistPlugin)

    def test_create_accepts_fetch_fn(self):
        async def stub(*a, **k):  # pragma: no cover - shape only
            return None
        plugin = create(fetch_fn=stub)
        assert plugin._fetch is stub
