"""
Clockify MCP plugin tests -- Issue #145.

Tools (five):
  - clockify_list_time_entries (read, GET
    /workspaces/{wid}/user/{userId}/time-entries)
  - clockify_create_time_entry (write, POST
    /workspaces/{wid}/time-entries)
  - clockify_stop_running_entry (write, PATCH
    /workspaces/{wid}/user/{userId}/time-entries with body
    {"end": "<ISO>"})
  - clockify_list_workspaces (read, GET /workspaces)
  - clockify_list_projects (read, GET /workspaces/{wid}/projects)

All hit the real Clockify v1 REST API with a static API key from the
CLOCKIFY_API_KEY env var via the provider seam. HTTP is injected via
fetch_fn and the token via a stub provider, so tests never read
os.environ, hit the keyring, or touch the network.

Learning-#15 substitution case (FOURTH transport shape: X-Api-Key
custom header, plugin-specific value = secret handling -- the FIRST
custom-header static-token plugin in the registry). The suite asserts
the X-Api-Key header IS attached AND the token never reaches a log /
ToolResult, INSTEAD of a fake-transport regression test. UNLIKE the
#142 Toggl Basic-auth substitution case which scrubbed BOTH the raw
and base64-encoded forms, Clockify only needs ONE scrub form (raw
key) because the header value IS the raw key -- no encoding wrapper.

Protocol-narrowing divergence from gmail/calendar: Clockify's
TokenProvider carries only current() (no refresh, no OAuth). A 401
propagates straight through with NO refresh-and-retry -- the suite
asserts exactly ONE outbound request on both paths.

Genuinely-new mechanics (vs Toggl): both clockify_list_time_entries
AND clockify_stop_running_entry resolve userId via an internal GET
/user call before the tool's main request -- Clockify's only
self-scoped list and stop endpoints are path-encoded by userId, with
no /user/me/ shortcut. The TestUserIdResolve class parametrizes the
resolve-error paths across both tools.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import plugins.clockify as _clockify_mod  # noqa: E402
from plugins.clockify import (  # noqa: E402
    REQUIRED_CAPABILITIES,
    ClockifyAPIError,
    ClockifyPlugin,
    _now_iso,
    _shape_entry,
    _shape_project,
    _shape_workspace,
    create,
)

_BASE = "https://api.clockify.me/api/v1"
_USER_URL = f"{_BASE}/user"
_WID = "5e7f7b9c1234567890abcdef"
_UID = "5e7f7b9caaaaaaaaaaaaaaab"


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
    body (POSTs / PATCHes).
    """
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        captured.append({
            "method": method, "url": url,
            "headers": headers or {}, "params": params or {},
            "json": json or {},
        })
        # Longest-match-wins: iterate routes sorted by needle length
        # DESC. Defends against substring collisions like ``/user`` (5
        # chars) appearing inside ``/workspaces/{wid}/user/{uid}/time-
        # entries`` (~80 chars). Without this, the shorter key would
        # match first and routes resolve ambiguously.
        for needle, resp in sorted(routes.items(), key=lambda kv: -len(kv[0])):
            if needle in url:
                if callable(resp) and not isinstance(resp, BaseException):
                    resp = resp()
                if isinstance(resp, BaseException):
                    raise resp
                return resp
        raise AssertionError(f"no route for {url}")
    return fake_fetch


def _user_obj(uid=_UID):
    return {"id": uid, "name": "Stub User", "email": "stub@example.com"}


def _entry(eid, *, description="Coding", workspace_id=_WID, project_id="proj-1",
           user_id=_UID, start="2026-05-20T15:00:00Z",
           end="2026-05-20T16:00:00Z", duration="PT1H", tag_ids=None,
           billable=False):
    return {
        "id": eid,
        "description": description,
        "workspaceId": workspace_id,
        "projectId": project_id,
        "userId": user_id,
        "timeInterval": {
            "start": start,
            "end": end,
            "duration": duration,
        },
        "tagIds": list(tag_ids) if tag_ids else [],
        "billable": billable,
    }


def _workspace(wid, *, name="My Workspace", image_url=""):
    return {"id": wid, "name": name, "imageUrl": image_url}


def _project(pid, *, name="Proj", workspace_id=_WID, client_id="",
             color="#0b83d9", archived=False):
    return {
        "id": pid, "name": name, "workspaceId": workspace_id,
        "clientId": client_id, "color": color, "archived": archived,
    }


_CREATE_OK = _entry("entry-1", description="Created", start="2026-05-20T15:00:00Z",
                    end="", duration="")
_STOP_OK = _entry("entry-1", description="Stopped", start="2026-05-20T15:00:00Z",
                  end="2026-05-20T16:00:00Z", duration="PT1H")


def _make_plugin(token="tok", routes=None, captured=None):
    """Convenience: build a plugin pre-wired with the default GET /user
    route + the caller's extra routes. ``captured`` defaults to a fresh
    list. The base /user route uses the FULL URL (not the bare ``/user``
    substring) to avoid colliding with the ``/workspaces/{wid}/user/{uid}/
    time-entries`` list/stop paths -- both contain ``/user`` as a
    substring, so any route key that's a shorter substring of the other
    would route ambiguously."""
    if captured is None:
        captured = []
    base_routes = {f"{_BASE}/user": _user_obj()}
    if routes:
        # Caller-supplied routes win on overlap (e.g. a test that wants
        # GET /user to fail). Both /user (base) and caller routes use
        # disjoint, unambiguous URL prefixes -- the list URL contains
        # ``/workspaces/.../user/.../time-entries`` (caller route),
        # the user URL ends at ``/api/v1/user`` (base route).
        merged = {**base_routes, **routes}
    else:
        merged = base_routes
    plugin = ClockifyPlugin(
        token_provider=_StubProvider(token),
        fetch_fn=_route_fetch(merged, captured),
    )
    return plugin, captured


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_five_tools(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "clockify_list_time_entries",
            "clockify_create_time_entry",
            "clockify_stop_running_entry",
            "clockify_list_workspaces",
            "clockify_list_projects",
        }

    def test_create_plugin_named_clockify(self):
        assert create().name == "clockify"

    def test_required_capabilities(self):
        # secrets_read is a DELIBERATE over-declaration (gmail.py /
        # youtube.py / todoist.py / notion.py / toggl.py posture-B);
        # external_data_write is the correct *required* ask-class
        # semantic class for create / stop. Both external_data_* are
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
            if t.name == "clockify_create_time_entry"
        )
        assert tool.schema.get("required", []) == ["wid", "start"]
        for opt in (
            "end", "description", "project_id", "task_id", "tag_ids",
            "billable",
        ):
            assert opt in tool.schema["properties"]

    def test_stop_args_schema_required_wid_only(self):
        # Divergence from #142 Toggl (which required wid+tid). Clockify's
        # documented stop endpoint is no-tid -- there's at most one
        # in-progress entry per user, so userId-resolve via /user is
        # sufficient to pin which entry gets stopped.
        tool = next(
            t for t in create().list_tools()
            if t.name == "clockify_stop_running_entry"
        )
        assert tool.schema.get("required", []) == ["wid"]
        # And NO tid field exists.
        assert "tid" not in tool.schema["properties"]

    def test_list_projects_schema_required_wid(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "clockify_list_projects"
        )
        assert tool.schema.get("required", []) == ["wid"]

    def test_list_time_entries_schema_required_wid(self):
        # Divergence from #142 Toggl (which had no required arg for
        # list_time_entries -- Toggl's /me/time_entries is self-scoped).
        # Clockify's only self-scoped list endpoint is workspace+user
        # scoped, so wid is required.
        tool = next(
            t for t in create().list_tools()
            if t.name == "clockify_list_time_entries"
        )
        assert tool.schema.get("required", []) == ["wid"]

    def test_list_workspaces_schema_no_required(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "clockify_list_workspaces"
        )
        assert tool.schema.get("required", []) == []

    def test_all_tool_ids_are_strings_not_integers(self):
        # Clockify IDs are MongoDB ObjectId hex strings (24-char hex),
        # not integers like Toggl. The schemas MUST declare string IDs.
        tools = {t.name: t for t in create().list_tools()}
        for name in (
            "clockify_list_time_entries", "clockify_create_time_entry",
            "clockify_stop_running_entry", "clockify_list_projects",
        ):
            assert tools[name].schema["properties"]["wid"]["type"] == "string"
        create_tool = tools["clockify_create_time_entry"]
        assert create_tool.schema["properties"]["project_id"]["type"] == "string"
        assert create_tool.schema["properties"]["task_id"]["type"] == "string"

    def test_no_clockify_tool_declares_irreversible(self):
        # #145 carries the #139 precedent / #142 precedent unchanged:
        # no clockify_* tool is marked irreversible (writes are
        # reversible via DELETE). Future marking would be a one-line
        # edit per plugin PLUS an explicit update to the
        # test_only_gmail_plugin_declares_irreversible_tool guard.
        for tool in create().list_tools():
            assert tool.irreversible is False, (
                f"{tool.name} declares irreversible=True; #145 scope "
                "does not mark any clockify_* tool."
            )


# ---------------------------------------------------------------------------
# Cycle 2 -- X-Api-Key header byte shape + raw-only scrub (learning-#15 NEW)
# ---------------------------------------------------------------------------

class TestXApiKeyHeader:
    @pytest.mark.asyncio
    async def test_x_api_key_header_is_raw_token(self):
        # The header value IS the raw key (no encoding wrapper, no
        # Bearer prefix, no Basic encoding). FOURTH learning-#15
        # transport shape -- FIRST custom-header static-token plugin.
        token = "SENTINEL_RAW_KEY_VALUE"
        captured: list = []
        plugin = ClockifyPlugin(
            token_provider=_StubProvider(token),
            fetch_fn=_route_fetch(
                {"/user": _user_obj(), "/workspaces": []}, captured,
            ),
        )
        await plugin.call_tool("clockify_list_workspaces", {})
        assert captured, "fetch_fn was never invoked"
        for call in captured:
            assert call["headers"].get("X-Api-Key") == token, (
                f"header X-Api-Key != raw token: {call['headers']}"
            )
            # No Authorization header at all -- this is custom-header
            # transport, not Bearer / Basic.
            assert "Authorization" not in call["headers"]

    @pytest.mark.asyncio
    async def test_no_b64_form_recorded_in_seen_tokens(self):
        # UNLIKE #142 Toggl, the X-Api-Key transport does NOT encode the
        # key, so there's no second form for an attacker to decode from
        # a leaked header value. Only ONE scrub form (raw key) is
        # tracked.
        token = "1971800d4d82861d8f2c1651fea4d212"
        plugin = ClockifyPlugin(
            token_provider=_StubProvider(token),
            fetch_fn=_route_fetch(
                {"/user": _user_obj(), "/workspaces": []}, [],
            ),
        )
        await plugin.call_tool("clockify_list_workspaces", {})
        # The plugin's internal _seen_tokens should contain ONLY the raw.
        assert plugin._seen_tokens == {token}

    @pytest.mark.asyncio
    async def test_raw_token_scrubbed_from_toolresult(self):
        token = "SENTINEL_KEY_LEAK"
        exc = ClockifyAPIError(
            f"500 Server Error -- X-Api-Key: {token}", status=500,
        )
        plugin = ClockifyPlugin(
            token_provider=_StubProvider(token),
            fetch_fn=_route_fetch(
                {"/user": _user_obj(), "/workspaces": exc}, [],
            ),
        )
        result = await plugin.call_tool("clockify_list_workspaces", {})
        assert result.is_error
        assert token not in result.content
        assert "***" in result.content


# ---------------------------------------------------------------------------
# Cycle 3 -- clockify_list_time_entries shaping + page-size + clamp
# ---------------------------------------------------------------------------

class TestListTimeEntries:
    @pytest.mark.asyncio
    async def test_list_shapes_entries(self):
        entries = [
            _entry("e1", description="First", project_id="proj-a",
                   start="2026-05-20T10:00:00Z",
                   end="2026-05-20T11:00:00Z", duration="PT1H"),
            _entry("e2", description="Second", tag_ids=["tag-a", "tag-b"]),
            _entry("e3", description="Third", billable=True),
        ]
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/user/{_UID}/time-entries": entries},
        )
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID},
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["entries"]) == 3
        assert data["entries"][0] == {
            "id": "e1", "description": "First", "workspace_id": _WID,
            "project_id": "proj-a", "user_id": _UID,
            "start": "2026-05-20T10:00:00Z",
            "end": "2026-05-20T11:00:00Z", "duration": "PT1H",
            "tag_ids": [], "billable": False,
        }
        assert data["entries"][1]["tag_ids"] == ["tag-a", "tag-b"]
        assert data["entries"][2]["billable"] is True
        # Two calls: GET /user, then GET /workspaces/{wid}/user/{uid}/time-entries
        assert len(captured) == 2
        assert captured[0]["method"] == "GET"
        assert captured[0]["url"] == f"{_BASE}/user"
        assert captured[1]["method"] == "GET"
        assert captured[1]["url"] == (
            f"{_BASE}/workspaces/{_WID}/user/{_UID}/time-entries"
        )

    @pytest.mark.asyncio
    async def test_page_size_param_forwarded(self):
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/user/{_UID}/time-entries": []},
        )
        await plugin.call_tool(
            "clockify_list_time_entries",
            {"wid": _WID, "max_results": 25},
        )
        # second captured call is the list (first is GET /user)
        assert captured[1]["params"] == {"page-size": 25}

    @pytest.mark.asyncio
    async def test_max_results_clamped_low(self):
        entries = [_entry(f"e{i}") for i in range(50)]
        plugin, _ = _make_plugin(
            routes={f"/workspaces/{_WID}/user/{_UID}/time-entries": entries},
        )
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID, "max_results": 0},
        )
        assert len(json.loads(result.content)["entries"]) == 1
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID, "max_results": -100},
        )
        assert len(json.loads(result.content)["entries"]) == 1

    @pytest.mark.asyncio
    async def test_max_results_clamped_high(self):
        entries = [_entry(f"e{i}") for i in range(500)]
        plugin, _ = _make_plugin(
            routes={f"/workspaces/{_WID}/user/{_UID}/time-entries": entries},
        )
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID, "max_results": 5000},
        )
        # Clockify max page-size is 200; default _MAX_LIMIT is 200.
        assert len(json.loads(result.content)["entries"]) == 200

    @pytest.mark.asyncio
    async def test_default_max_results_is_ten(self):
        entries = [_entry(f"e{i}") for i in range(25)]
        plugin, _ = _make_plugin(
            routes={f"/workspaces/{_WID}/user/{_UID}/time-entries": entries},
        )
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID},
        )
        assert len(json.loads(result.content)["entries"]) == 10

    @pytest.mark.asyncio
    async def test_missing_wid_is_arg_error(self):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool("clockify_list_time_entries", {})
        assert result.is_error
        assert "wid" in result.content
        # No fetch happened (arg validation runs before provider/user-resolve).
        assert captured == []

    @pytest.mark.parametrize("bad_wid", ["", "   ", None, 123, True, False, [], {}])
    @pytest.mark.asyncio
    async def test_invalid_wid_is_arg_error(self, bad_wid):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": bad_wid},
        )
        assert result.is_error
        assert "wid" in result.content
        assert captured == []

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_entries(self):
        plugin, _ = _make_plugin(
            routes={f"/workspaces/{_WID}/user/{_UID}/time-entries": []},
        )
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID},
        )
        assert not result.is_error
        assert json.loads(result.content) == {"entries": []}

    @pytest.mark.asyncio
    async def test_non_list_response_is_error(self):
        plugin, _ = _make_plugin(
            routes={
                f"/workspaces/{_WID}/user/{_UID}/time-entries": {"oops": True},
            },
        )
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID},
        )
        assert result.is_error
        assert "unexpected Clockify list response" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- clockify_create_time_entry happy + required args + body shape
# ---------------------------------------------------------------------------

class TestCreateTimeEntry:
    @pytest.mark.asyncio
    async def test_happy_path_with_end(self):
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/time-entries": _CREATE_OK},
        )
        result = await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID,
            "start": "2026-05-20T15:00:00Z",
            "end": "2026-05-20T16:00:00Z",
            "description": "Coding",
            "project_id": "proj-a",
            "task_id": "task-x",
            "tag_ids": ["tag-focus"],
            "billable": True,
        })
        assert not result.is_error
        # GET /user is NOT called for create -- POST is current-user-scoped
        # via the X-Api-Key context. So captured has only ONE call.
        assert len(captured) == 1
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/workspaces/{_WID}/time-entries"
        body = call["json"]
        assert body == {
            "start": "2026-05-20T15:00:00Z",
            "end": "2026-05-20T16:00:00Z",
            "description": "Coding",
            "projectId": "proj-a",
            "taskId": "task-x",
            "tagIds": ["tag-focus"],
            "billable": True,
        }

    @pytest.mark.asyncio
    async def test_running_entry_omits_end(self):
        # Clockify's "still running" marker is absence-of-end (different
        # from Toggl's duration: -1). Omitting end on create yields a
        # running entry.
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/time-entries": _CREATE_OK},
        )
        await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID,
            "start": "2026-05-20T15:00:00Z",
        })
        body = captured[0]["json"]
        assert "end" not in body
        assert body == {"start": "2026-05-20T15:00:00Z"}

    @pytest.mark.parametrize("missing_arg", ["wid", "start"])
    @pytest.mark.asyncio
    async def test_missing_required_arg_is_error(self, missing_arg):
        args = {"wid": _WID, "start": "2026-05-20T15:00:00Z"}
        del args[missing_arg]
        plugin, captured = _make_plugin()
        result = await plugin.call_tool("clockify_create_time_entry", args)
        assert result.is_error
        assert missing_arg in result.content
        assert captured == []

    @pytest.mark.parametrize("bad_wid", ["", None, 1234, True, [], {}])
    @pytest.mark.asyncio
    async def test_bad_wid_is_arg_error(self, bad_wid):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool("clockify_create_time_entry", {
            "wid": bad_wid, "start": "2026-05-20T15:00:00Z",
        })
        assert result.is_error
        assert "wid" in result.content
        assert captured == []

    @pytest.mark.parametrize("bad_start", ["", None, 12345, True, [], {}])
    @pytest.mark.asyncio
    async def test_bad_start_is_arg_error(self, bad_start):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID, "start": bad_start,
        })
        assert result.is_error
        assert "start" in result.content
        assert captured == []

    @pytest.mark.asyncio
    async def test_optional_fields_omitted_when_absent(self):
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/time-entries": _CREATE_OK},
        )
        await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID, "start": "2026-05-20T15:00:00Z",
        })
        body = captured[0]["json"]
        for opt in ("end", "description", "projectId", "taskId", "tagIds", "billable"):
            assert opt not in body, f"unexpected field in body: {opt}"

    @pytest.mark.asyncio
    async def test_blank_description_dropped(self):
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/time-entries": _CREATE_OK},
        )
        await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID, "start": "2026-05-20T15:00:00Z",
            "description": "",
        })
        assert "description" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_empty_tag_ids_dropped(self):
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/time-entries": _CREATE_OK},
        )
        await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID, "start": "2026-05-20T15:00:00Z",
            "tag_ids": ["", "  ", None, 123],
        })
        # All entries non-string-or-empty -> tagIds dropped entirely.
        body = captured[0]["json"]
        assert "tagIds" not in body

    @pytest.mark.parametrize("bad_billable", ["yes", 1, 0, "true", "false", None])
    @pytest.mark.asyncio
    async def test_bad_billable_dropped(self, bad_billable):
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/time-entries": _CREATE_OK},
        )
        await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID, "start": "2026-05-20T15:00:00Z",
            "billable": bad_billable,
        })
        assert "billable" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_non_dict_response_is_error(self):
        plugin, _ = _make_plugin(
            routes={f"/workspaces/{_WID}/time-entries": ["not", "a", "dict"]},
        )
        result = await plugin.call_tool("clockify_create_time_entry", {
            "wid": _WID, "start": "2026-05-20T15:00:00Z",
        })
        assert result.is_error
        assert "unexpected Clockify create response" in result.content


# ---------------------------------------------------------------------------
# Cycle 5 -- clockify_stop_running_entry happy + arg + body shape
# ---------------------------------------------------------------------------

class TestStopRunningEntry:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(
            _clockify_mod, "_now_iso", lambda: "2026-05-20T16:00:00Z",
        )
        plugin, captured = _make_plugin(
            routes={
                f"/workspaces/{_WID}/user/{_UID}/time-entries": _STOP_OK,
            },
        )
        result = await plugin.call_tool(
            "clockify_stop_running_entry", {"wid": _WID},
        )
        assert not result.is_error
        # Two calls: GET /user, then PATCH stop.
        assert len(captured) == 2
        assert captured[0]["url"] == f"{_BASE}/user"
        stop_call = captured[1]
        assert stop_call["method"] == "PATCH"
        assert stop_call["url"] == (
            f"{_BASE}/workspaces/{_WID}/user/{_UID}/time-entries"
        )
        assert stop_call["json"] == {"end": "2026-05-20T16:00:00Z"}

    @pytest.mark.asyncio
    async def test_missing_wid_is_arg_error(self):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool("clockify_stop_running_entry", {})
        assert result.is_error
        assert "wid" in result.content
        assert captured == []

    @pytest.mark.parametrize("bad_wid", ["", "  ", None, 1234, True, [], {}])
    @pytest.mark.asyncio
    async def test_invalid_wid_is_arg_error(self, bad_wid):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool(
            "clockify_stop_running_entry", {"wid": bad_wid},
        )
        assert result.is_error
        assert "wid" in result.content
        assert captured == []

    @pytest.mark.asyncio
    async def test_no_tid_in_signature(self):
        # Sanity-check the divergence from #142 -- the stop tool takes
        # NO tid argument. A caller passing a tid should not affect the
        # call (it's silently dropped by the schema layer; the
        # implementation never reads it).
        plugin, captured = _make_plugin(
            routes={
                f"/workspaces/{_WID}/user/{_UID}/time-entries": _STOP_OK,
            },
        )
        result = await plugin.call_tool(
            "clockify_stop_running_entry",
            {"wid": _WID, "tid": "ignored-tid-1234"},
        )
        assert not result.is_error
        # The PATCH URL does NOT include any tid path segment.
        assert "ignored-tid-1234" not in captured[1]["url"]
        assert captured[1]["url"].endswith("/time-entries")

    @pytest.mark.asyncio
    async def test_non_dict_response_is_error(self):
        plugin, _ = _make_plugin(
            routes={
                f"/workspaces/{_WID}/user/{_UID}/time-entries": ["nope"],
            },
        )
        result = await plugin.call_tool(
            "clockify_stop_running_entry", {"wid": _WID},
        )
        assert result.is_error
        assert "unexpected Clockify stop response" in result.content


# ---------------------------------------------------------------------------
# Cycle 6 -- list_workspaces + list_projects
# ---------------------------------------------------------------------------

class TestWorkspacesAndProjects:
    @pytest.mark.asyncio
    async def test_list_workspaces_happy(self):
        workspaces = [
            _workspace("w1", name="Personal"),
            _workspace("w2", name="Acme", image_url="https://x/y.png"),
        ]
        plugin, captured = _make_plugin(
            routes={"/workspaces": workspaces},
        )
        result = await plugin.call_tool("clockify_list_workspaces", {})
        assert not result.is_error
        # No GET /user for workspaces (workspaces are scoped to the API
        # key context).
        assert len(captured) == 1
        assert captured[0]["url"] == f"{_BASE}/workspaces"
        data = json.loads(result.content)
        assert data == {
            "workspaces": [
                {"id": "w1", "name": "Personal", "image_url": ""},
                {"id": "w2", "name": "Acme", "image_url": "https://x/y.png"},
            ],
        }

    @pytest.mark.asyncio
    async def test_list_projects_happy(self):
        projects = [
            _project("p1", name="Alpha", workspace_id=_WID),
            _project("p2", name="Beta", archived=True),
        ]
        plugin, captured = _make_plugin(
            routes={f"/workspaces/{_WID}/projects": projects},
        )
        result = await plugin.call_tool(
            "clockify_list_projects", {"wid": _WID},
        )
        assert not result.is_error
        # No GET /user for projects.
        assert len(captured) == 1
        assert captured[0]["url"] == f"{_BASE}/workspaces/{_WID}/projects"
        data = json.loads(result.content)
        assert len(data["projects"]) == 2
        assert data["projects"][0]["id"] == "p1"
        assert data["projects"][1]["archived"] is True

    @pytest.mark.asyncio
    async def test_list_workspaces_non_list_is_error(self):
        plugin, _ = _make_plugin(routes={"/workspaces": {"oops": True}})
        result = await plugin.call_tool("clockify_list_workspaces", {})
        assert result.is_error
        assert "unexpected Clockify workspaces response" in result.content

    @pytest.mark.asyncio
    async def test_list_projects_missing_wid_is_arg_error(self):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool("clockify_list_projects", {})
        assert result.is_error
        assert "wid" in result.content
        assert captured == []

    @pytest.mark.parametrize("bad_wid", ["", None, 1234, True, [], {}])
    @pytest.mark.asyncio
    async def test_list_projects_bad_wid_is_arg_error(self, bad_wid):
        plugin, captured = _make_plugin()
        result = await plugin.call_tool(
            "clockify_list_projects", {"wid": bad_wid},
        )
        assert result.is_error
        assert "wid" in result.content
        assert captured == []

    @pytest.mark.asyncio
    async def test_list_projects_non_list_is_error(self):
        plugin, _ = _make_plugin(
            routes={f"/workspaces/{_WID}/projects": {"oops": True}},
        )
        result = await plugin.call_tool(
            "clockify_list_projects", {"wid": _WID},
        )
        assert result.is_error
        assert "unexpected Clockify projects response" in result.content


# ---------------------------------------------------------------------------
# Cycle 7 -- _resolve_user_id shared by list AND stop
# ---------------------------------------------------------------------------

_RESOLVE_USERS = [
    pytest.param(
        ("clockify_list_time_entries", {"wid": _WID}),
        id="list_time_entries",
    ),
    pytest.param(
        ("clockify_stop_running_entry", {"wid": _WID}),
        id="stop_running_entry",
    ),
]


class TestUserIdResolve:
    @pytest.mark.parametrize("tool_args", _RESOLVE_USERS)
    @pytest.mark.asyncio
    async def test_user_resolve_bad_resp_is_error(self, tool_args):
        tool_name, args = tool_args
        plugin, captured = _make_plugin(
            routes={_USER_URL: ["not", "a", "dict"]},
        )
        result = await plugin.call_tool(tool_name, args)
        assert result.is_error
        assert "unexpected Clockify /user response" in result.content
        # Only the GET /user happened -- the main tool request never fires.
        assert len(captured) == 1
        assert captured[0]["url"] == _USER_URL

    @pytest.mark.parametrize("tool_args", _RESOLVE_USERS)
    @pytest.mark.asyncio
    async def test_user_resolve_no_id_is_error(self, tool_args):
        tool_name, args = tool_args
        plugin, captured = _make_plugin(
            routes={_USER_URL: {"name": "no id field"}},
        )
        result = await plugin.call_tool(tool_name, args)
        assert result.is_error
        assert "could not resolve Clockify user id" in result.content
        assert len(captured) == 1

    @pytest.mark.parametrize("tool_args", _RESOLVE_USERS)
    @pytest.mark.asyncio
    async def test_user_resolve_propagates_get_user_exception(self, tool_args):
        tool_name, args = tool_args
        exc = ClockifyAPIError("503 Service Unavailable", status=503)
        plugin, captured = _make_plugin(routes={_USER_URL: exc})
        result = await plugin.call_tool(tool_name, args)
        assert result.is_error
        assert "Clockify request failed" in result.content
        # The /user GET happened and raised; main request never fired.
        assert len(captured) == 1

    @pytest.mark.parametrize("tool_args", _RESOLVE_USERS)
    @pytest.mark.asyncio
    async def test_user_resolve_empty_id_string_is_error(self, tool_args):
        tool_name, args = tool_args
        plugin, captured = _make_plugin(routes={_USER_URL: {"id": ""}})
        result = await plugin.call_tool(tool_name, args)
        assert result.is_error
        assert "could not resolve Clockify user id" in result.content
        assert len(captured) == 1

    @pytest.mark.parametrize("tool_args", _RESOLVE_USERS)
    @pytest.mark.asyncio
    async def test_user_resolve_non_string_id_is_error(self, tool_args):
        tool_name, args = tool_args
        plugin, captured = _make_plugin(routes={_USER_URL: {"id": 12345}})
        result = await plugin.call_tool(tool_name, args)
        assert result.is_error
        assert "could not resolve Clockify user id" in result.content
        assert len(captured) == 1


# ---------------------------------------------------------------------------
# Cycle 8 -- header + scrub regression across all five tools
# ---------------------------------------------------------------------------

_TOOL_INVOCATIONS = [
    pytest.param(
        ("clockify_list_time_entries", {"wid": _WID},
         {f"/workspaces/{_WID}/user/{_UID}/time-entries": []}),
        id="list_time_entries",
    ),
    pytest.param(
        ("clockify_create_time_entry",
         {"wid": _WID, "start": "2026-05-20T15:00:00Z"},
         {f"/workspaces/{_WID}/time-entries": _CREATE_OK}),
        id="create_time_entry",
    ),
    pytest.param(
        ("clockify_stop_running_entry", {"wid": _WID},
         {f"/workspaces/{_WID}/user/{_UID}/time-entries": _STOP_OK}),
        id="stop_running_entry",
    ),
    pytest.param(
        ("clockify_list_workspaces", {}, {"/workspaces": []}),
        id="list_workspaces",
    ),
    pytest.param(
        ("clockify_list_projects", {"wid": _WID},
         {f"/workspaces/{_WID}/projects": []}),
        id="list_projects",
    ),
]


class TestHeaderAndScrub:
    @pytest.mark.parametrize("invocation", _TOOL_INVOCATIONS)
    @pytest.mark.asyncio
    async def test_x_api_key_header_on_every_call(self, invocation):
        tool_name, args, routes = invocation
        plugin, captured = _make_plugin(token="HEADER_PROBE_TOK", routes=routes)
        await plugin.call_tool(tool_name, args)
        assert captured, f"{tool_name}: fetch never invoked"
        for call in captured:
            assert call["headers"].get("X-Api-Key") == "HEADER_PROBE_TOK"
            assert call["headers"].get("Content-Type") == "application/json"

    @pytest.mark.parametrize("invocation", _TOOL_INVOCATIONS)
    @pytest.mark.asyncio
    async def test_token_never_in_toolresult_on_error(self, invocation):
        tool_name, args, routes = invocation
        token = f"SECRET_{tool_name.upper()}"
        # Replace the main-request route with an exception whose
        # message embeds the token.
        boom_routes = {
            url: ClockifyAPIError(
                f"500 boom -- X-Api-Key: {token}", status=500,
            )
            for url in routes
        }
        # For tools that need /user resolve, the /user call still
        # succeeds; the boom happens on the main request.
        if tool_name in ("clockify_list_time_entries",
                         "clockify_stop_running_entry"):
            plugin, _ = _make_plugin(
                token=token, routes=boom_routes,
            )
        else:
            # No /user call for these tools.
            plugin = ClockifyPlugin(
                token_provider=_StubProvider(token),
                fetch_fn=_route_fetch(boom_routes, []),
            )
        result = await plugin.call_tool(tool_name, args)
        assert result.is_error
        assert token not in result.content
        assert "***" in result.content

    @pytest.mark.parametrize("invocation", _TOOL_INVOCATIONS)
    @pytest.mark.asyncio
    async def test_token_never_in_logs_on_error(self, invocation, caplog):
        tool_name, args, routes = invocation
        token = f"SECRET_LOG_{tool_name.upper()}"
        boom_routes = {
            url: ClockifyAPIError(
                f"500 boom -- X-Api-Key: {token}", status=500,
            )
            for url in routes
        }
        if tool_name in ("clockify_list_time_entries",
                         "clockify_stop_running_entry"):
            plugin, _ = _make_plugin(
                token=token, routes=boom_routes,
            )
        else:
            plugin = ClockifyPlugin(
                token_provider=_StubProvider(token),
                fetch_fn=_route_fetch(boom_routes, []),
            )
        with caplog.at_level(logging.ERROR, logger="plugins.clockify"):
            await plugin.call_tool(tool_name, args)
        log_blob = " ".join(rec.getMessage() for rec in caplog.records)
        assert token not in log_blob, (
            f"raw token leaked into log: {log_blob}"
        )
        assert "***" in log_blob


# ---------------------------------------------------------------------------
# Cycle 9 -- TokenWiring: factory-not-wired + no-token + module-level setter
# ---------------------------------------------------------------------------

_TOOL_ONLY_NAMES = [
    "clockify_list_time_entries",
    "clockify_create_time_entry",
    "clockify_stop_running_entry",
    "clockify_list_workspaces",
    "clockify_list_projects",
]


def _minimal_args(name):
    return {
        "clockify_list_time_entries": {"wid": _WID},
        "clockify_create_time_entry": {"wid": _WID, "start": "2026-05-20T15:00:00Z"},
        "clockify_stop_running_entry": {"wid": _WID},
        "clockify_list_workspaces": {},
        "clockify_list_projects": {"wid": _WID},
    }[name]


class TestTokenWiring:
    @pytest.fixture(autouse=True)
    def _reset_factory(self):
        original = _clockify_mod._token_provider_factory
        _clockify_mod._token_provider_factory = None
        yield
        _clockify_mod._token_provider_factory = original

    @pytest.mark.parametrize("tool_name", _TOOL_ONLY_NAMES)
    @pytest.mark.asyncio
    async def test_factory_not_wired_is_error(self, tool_name):
        # No provider injected via constructor, no module-level factory.
        plugin = ClockifyPlugin(fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool(tool_name, _minimal_args(tool_name))
        assert result.is_error
        assert "token provider not wired" in result.content

    @pytest.mark.parametrize("tool_name", _TOOL_ONLY_NAMES)
    @pytest.mark.asyncio
    async def test_no_token_is_error(self, tool_name):
        # Factory wired but returns None (env var unset).
        _clockify_mod.set_token_provider(lambda: None)
        plugin = ClockifyPlugin(fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool(tool_name, _minimal_args(tool_name))
        assert result.is_error
        assert "no Clockify API key configured" in result.content

    @pytest.mark.parametrize("tool_name", _TOOL_ONLY_NAMES)
    @pytest.mark.asyncio
    async def test_module_setter_provider_used(self, tool_name):
        # Factory returns a real provider; constructor leaves token_provider None.
        _clockify_mod.set_token_provider(lambda: _StubProvider("wired-tok"))
        # Provide routes for all five tool paths.
        routes = {
            _USER_URL: _user_obj(),
            f"/workspaces/{_WID}/user/{_UID}/time-entries":
                _STOP_OK if tool_name == "clockify_stop_running_entry" else [],
            f"/workspaces/{_WID}/time-entries": _CREATE_OK,
            "/workspaces": [],
            f"/workspaces/{_WID}/projects": [],
        }
        captured: list = []
        plugin = ClockifyPlugin(fetch_fn=_route_fetch(routes, captured))
        result = await plugin.call_tool(tool_name, _minimal_args(tool_name))
        assert not result.is_error, f"{tool_name}: {result.content}"
        # Header was attached using the factory-resolved token.
        assert captured[-1]["headers"]["X-Api-Key"] == "wired-tok"


# ---------------------------------------------------------------------------
# Cycle 10 -- No401Retry: 401 propagates straight through (no refresh-and-retry)
# ---------------------------------------------------------------------------

class TestNo401Retry:
    @pytest.mark.parametrize("invocation", _TOOL_INVOCATIONS)
    @pytest.mark.asyncio
    async def test_401_propagates_exactly_one_request(self, invocation):
        tool_name, args, routes = invocation
        boom_routes = {
            url: ClockifyAPIError("401 Unauthorized", status=401)
            for url in routes
        }
        # For /user-resolve tools, the /user call succeeds and the
        # main-request fails with 401.
        if tool_name in ("clockify_list_time_entries",
                         "clockify_stop_running_entry"):
            plugin, captured = _make_plugin(routes=boom_routes)
            result = await plugin.call_tool(tool_name, args)
            assert result.is_error
            # Two outbound: GET /user OK + main 401. NO retry.
            assert len(captured) == 2
            assert captured[0]["url"].endswith("/user")
        else:
            captured: list = []
            plugin = ClockifyPlugin(
                token_provider=_StubProvider("tok"),
                fetch_fn=_route_fetch(boom_routes, captured),
            )
            result = await plugin.call_tool(tool_name, args)
            assert result.is_error
            # Exactly ONE outbound -- no refresh-and-retry.
            assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_401_on_user_resolve_propagates(self):
        # /user itself returns 401 -- the main tool never gets to run.
        plugin, captured = _make_plugin(routes={
            _USER_URL: ClockifyAPIError("401 Unauthorized", status=401),
        })
        result = await plugin.call_tool(
            "clockify_list_time_entries", {"wid": _WID},
        )
        assert result.is_error
        # ONLY ONE outbound -- the /user call. No retry. No follow-up
        # list call.
        assert len(captured) == 1
        assert captured[0]["url"] == _USER_URL


# ---------------------------------------------------------------------------
# Cycle 11 -- pure helpers: _shape_entry, _shape_workspace, _shape_project, _coerce_id, _now_iso
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_shape_entry_full(self):
        raw = _entry(
            "eX", description="Pure-helper", project_id="pp",
            user_id="uu", start="2026-05-20T10:00:00Z",
            end="2026-05-20T11:00:00Z", duration="PT1H",
            tag_ids=["a", "b"], billable=True,
        )
        assert _shape_entry(raw) == {
            "id": "eX", "description": "Pure-helper",
            "workspace_id": _WID, "project_id": "pp", "user_id": "uu",
            "start": "2026-05-20T10:00:00Z",
            "end": "2026-05-20T11:00:00Z", "duration": "PT1H",
            "tag_ids": ["a", "b"], "billable": True,
        }

    def test_shape_entry_running_has_empty_end(self):
        # An in-progress Clockify entry has timeInterval.end == None (or
        # absent). The shape helper coerces it to "".
        raw = {
            "id": "running-1", "description": "WIP",
            "workspaceId": _WID, "projectId": None, "userId": _UID,
            "timeInterval": {
                "start": "2026-05-20T15:00:00Z", "end": None,
                "duration": None,
            },
            "tagIds": [], "billable": False,
        }
        shaped = _shape_entry(raw)
        assert shaped["end"] == ""
        assert shaped["duration"] == ""
        assert shaped["project_id"] == ""

    def test_shape_entry_missing_time_interval(self):
        # No timeInterval at all -- defaults all-empty.
        raw = {"id": "x", "description": "no interval"}
        shaped = _shape_entry(raw)
        assert shaped["start"] == ""
        assert shaped["end"] == ""
        assert shaped["duration"] == ""

    def test_shape_entry_non_dict_returns_empty(self):
        assert _shape_entry(None) == {}
        assert _shape_entry("string") == {}
        assert _shape_entry(42) == {}

    def test_shape_workspace_full(self):
        raw = _workspace("ws-1", name="Acme", image_url="https://x.png")
        assert _shape_workspace(raw) == {
            "id": "ws-1", "name": "Acme", "image_url": "https://x.png",
        }

    def test_shape_workspace_non_dict_returns_empty(self):
        assert _shape_workspace([]) == {}

    def test_shape_project_full(self):
        raw = _project("p1", name="Alpha", workspace_id="ws-x",
                       client_id="cli", color="#abc", archived=True)
        assert _shape_project(raw) == {
            "id": "p1", "name": "Alpha", "workspace_id": "ws-x",
            "client_id": "cli", "color": "#abc", "archived": True,
        }

    def test_shape_project_defaults(self):
        # Sparse Clockify response -- all but id absent.
        raw = {"id": "p-bare"}
        shaped = _shape_project(raw)
        assert shaped["id"] == "p-bare"
        assert shaped["name"] == ""
        assert shaped["archived"] is False
        assert shaped["color"] == ""

    def test_shape_project_non_dict_returns_empty(self):
        assert _shape_project(0) == {}

    @pytest.mark.parametrize("raw,expected", [
        ("good-id", "good-id"),
        ("  with-space  ", "with-space"),
        ("", None),
        ("   ", None),
        (None, None),
        (1234, None),
        (True, None),
        (False, None),
        ([], None),
        ({}, None),
    ])
    def test_coerce_id(self, raw, expected):
        # _coerce_id is a staticmethod on the plugin; reach through an
        # instance for the test.
        plugin = ClockifyPlugin()
        assert plugin._coerce_id(raw) == expected

    def test_now_iso_shape(self):
        # _now_iso returns a second-precision RFC3339 string. We don't
        # assert the exact instant (system clock); just the shape.
        s = _now_iso()
        assert isinstance(s, str)
        assert s.endswith("Z")
        # ``YYYY-MM-DDTHH:MM:SSZ`` -- 20 characters exactly.
        assert len(s) == 20

    def test_clamp_max_results_bool_falls_to_default(self):
        # Bool is an int subclass in Python -- True == 1, False == 0.
        # _clamp_max_results explicitly drops booleans to the default.
        plugin = ClockifyPlugin()
        assert plugin._clamp_max_results(True) == 10
        assert plugin._clamp_max_results(False) == 10

    def test_clamp_max_results_string_parses(self):
        plugin = ClockifyPlugin()
        assert plugin._clamp_max_results("25") == 25

    def test_clamp_max_results_garbage_falls_to_default(self):
        plugin = ClockifyPlugin()
        assert plugin._clamp_max_results("not-a-number") == 10
        assert plugin._clamp_max_results([]) == 10


# ---------------------------------------------------------------------------
# Cycle 12 -- one final cross-check: _seen_tokens grows on token reads
# ---------------------------------------------------------------------------

class TestSeenTokens:
    @pytest.mark.asyncio
    async def test_seen_tokens_records_raw_only_after_first_call(self):
        token = "ONCE_PRIMED_TOK"
        plugin, _ = _make_plugin(
            token=token, routes={"/workspaces": []},
        )
        assert plugin._seen_tokens == set()
        await plugin.call_tool("clockify_list_workspaces", {})
        assert plugin._seen_tokens == {token}

    @pytest.mark.asyncio
    async def test_seen_tokens_empty_when_provider_returns_none(self):
        # Provider returns None -- _take_token records nothing.
        plugin = ClockifyPlugin(
            token_provider=_StubProvider(None),
            fetch_fn=_route_fetch({"/workspaces": []}, []),
        )
        # The factory_not_wired path doesn't apply since we passed a
        # provider explicitly; the empty-string token path applies.
        await plugin.call_tool("clockify_list_workspaces", {})
        assert plugin._seen_tokens == set()
