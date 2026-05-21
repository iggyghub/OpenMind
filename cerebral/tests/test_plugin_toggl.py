"""
Toggl Track MCP plugin tests -- Issue #142.

Tools (five):
  - toggl_list_time_entries (read, GET /me/time_entries)
  - toggl_create_time_entry (write, POST /workspaces/{wid}/time_entries)
  - toggl_stop_running_entry (write, PATCH
    /workspaces/{wid}/time_entries/{tid}/stop)
  - toggl_list_workspaces (read, GET /workspaces)
  - toggl_list_projects (read, GET /workspaces/{wid}/projects)

All hit the real Toggl Track v9 REST API with a static API token
from the TOGGL_API_TOKEN env var via the provider seam. HTTP is
injected via fetch_fn and the token via a stub provider, so tests
never read os.environ, hit the keyring, or touch the network.

Learning-#15 substitution case (THIRD transport shape: HTTP Basic
header, plugin-specific value = secret handling). The suite asserts
the Basic header IS attached AND the token (in BOTH the raw and
base64-encoded forms) never reaches a log / ToolResult, INSTEAD of a
fake-transport regression test. The b64 scrub is the genuinely-new
mechanics surface for this slice (the base64 form contains the raw
token in a decodable shape -- an attacker reading a log line could
decode it).

Protocol-narrowing divergence from gmail/calendar: Toggl's
TokenProvider carries only current() (no refresh, no OAuth). A 401
propagates straight through with NO refresh-and-retry -- the suite
asserts exactly ONE outbound request on both paths.
"""
import base64
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.toggl import (  # noqa: E402
    REQUIRED_CAPABILITIES,
    TogglAPIError,
    TogglPlugin,
    _basic_auth_header,
    create,
)

_BASE = "https://api.track.toggl.com/api/v9"


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


def _entry(eid, *, description="Coding", workspace_id=1234, project_id=42,
           start="2026-05-20T15:00:00+00:00", stop="2026-05-20T16:00:00+00:00",
           duration=3600, tags=None, billable=False):
    return {
        "id": eid,
        "description": description,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "start": start,
        "stop": stop,
        "duration": duration,
        "tags": list(tags) if tags else [],
        "billable": billable,
    }


def _workspace(wid, *, name="My Workspace", default=False, premium=False):
    return {
        "id": wid, "name": name, "default": default, "premium": premium,
    }


def _project(pid, *, name="Proj", workspace_id=1234, color="0b83d9",
             active=True):
    return {
        "id": pid, "name": name, "workspace_id": workspace_id,
        "color": color, "active": active,
    }


_CREATE_OK = _entry(99999, description="Created", workspace_id=1234,
                    project_id=42, start="2026-05-20T15:00:00+00:00",
                    stop="", duration=-1)
_STOP_OK = _entry(99999, description="Stopped", workspace_id=1234,
                  project_id=42, start="2026-05-20T15:00:00+00:00",
                  stop="2026-05-20T16:00:00+00:00", duration=3600)


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_five_tools(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "toggl_list_time_entries",
            "toggl_create_time_entry",
            "toggl_stop_running_entry",
            "toggl_list_workspaces",
            "toggl_list_projects",
        }

    def test_create_plugin_named_toggl(self):
        assert create().name == "toggl"

    def test_required_capabilities(self):
        # secrets_read is a DELIBERATE over-declaration (gmail.py /
        # youtube.py / todoist.py / notion.py posture-B);
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
            if t.name == "toggl_create_time_entry"
        )
        assert tool.schema.get("required", []) == ["wid", "start", "duration"]
        for opt in (
            "description", "project_id", "tags", "billable",
        ):
            assert opt in tool.schema["properties"]

    def test_stop_args_schema_required_wid_and_tid(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "toggl_stop_running_entry"
        )
        assert tool.schema.get("required", []) == ["wid", "tid"]

    def test_list_projects_schema_required_wid(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "toggl_list_projects"
        )
        assert tool.schema.get("required", []) == ["wid"]

    def test_list_workspaces_schema_no_required(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "toggl_list_workspaces"
        )
        assert tool.schema.get("required", []) == []

    def test_list_time_entries_schema_no_required(self):
        tool = next(
            t for t in create().list_tools()
            if t.name == "toggl_list_time_entries"
        )
        assert tool.schema.get("required", []) == []

    def test_no_toggl_tool_declares_irreversible(self):
        # #142 carries the #139 precedent unchanged: no toggl_* tool
        # is marked irreversible (writes are reversible via stop /
        # DELETE). Future marking would be a one-line edit per plugin
        # PLUS an explicit update to the
        # test_only_gmail_plugin_declares_irreversible_tool guard.
        for tool in create().list_tools():
            assert tool.irreversible is False, (
                f"{tool.name} declares irreversible=True; #142 scope "
                "does not mark any toggl_* tool."
            )


# ---------------------------------------------------------------------------
# Cycle 2 -- Basic auth header byte shape + b64 scrub (learning-#15 NEW case)
# ---------------------------------------------------------------------------

class TestBasicAuthHeader:
    def test_basic_auth_header_byte_shape(self):
        # Known input -> known b64. From the Toggl docs:
        #   token = "1971800d4d82861d8f2c1651fea4d212"
        #   raw   = "1971800d4d82861d8f2c1651fea4d212:api_token"
        #   b64   = "MTk3MTgwMGQ0ZDgyODYxZDhmMmMxNjUxZmVhNGQyMTI6YXBpX3Rva2Vu"
        token = "1971800d4d82861d8f2c1651fea4d212"
        expected_b64 = base64.b64encode(
            f"{token}:api_token".encode("ascii")
        ).decode("ascii")
        assert _basic_auth_header(token) == f"Basic {expected_b64}"
        # Sanity-check the expected b64 against the manually-computed
        # literal so a future b64 implementation drift would surface.
        assert expected_b64 == (
            "MTk3MTgwMGQ0ZDgyODYxZDhmMmMxNjUxZmVhNGQyMTI6YXBpX3Rva2Vu"
        )

    def test_basic_auth_header_ascii_safe(self):
        # The function builds an ASCII Authorization header value; a
        # non-ASCII byte would surface as a UnicodeEncodeError.
        with pytest.raises(UnicodeEncodeError):
            _basic_auth_header("tokén-with-non-ascii")

    @pytest.mark.asyncio
    async def test_b64_token_form_scrubbed_from_toolresult(self):
        # The b64 form contains the raw token in a decodable shape.
        # A log/ToolResult that leaked the header value would be just
        # as bad as leaking the raw token. Both forms MUST be
        # scrubbed.
        token = "SENTINEL_BASIC_TOKEN"
        b64 = base64.b64encode(
            f"{token}:api_token".encode("ascii")
        ).decode("ascii")
        # The leaked-error embeds the b64 form (a hypothetical log
        # line that printed the Authorization header value).
        exc = TogglAPIError(
            f"401 Client Error -- Authorization: Basic {b64}",
            status=401,
        )
        plugin = TogglPlugin(
            token_provider=_StubProvider(token),
            fetch_fn=_route_fetch({"/me/time_entries": exc}, []),
        )
        result = await plugin.call_tool("toggl_list_time_entries", {})
        assert result.is_error
        assert token not in result.content
        assert b64 not in result.content
        assert "***" in result.content


# ---------------------------------------------------------------------------
# Cycle 3 -- toggl_list_time_entries shaping + since + clamp
# ---------------------------------------------------------------------------

class TestListTimeEntries:
    @pytest.mark.asyncio
    async def test_list_shapes_entries(self):
        captured: list = []
        entries = [
            _entry(1, description="First", project_id=42,
                   start="2026-05-20T10:00:00+00:00",
                   stop="2026-05-20T11:00:00+00:00", duration=3600),
            _entry(2, description="Second", tags=["tag-a", "tag-b"]),
            _entry(3, description="Third", billable=True),
        ]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/me/time_entries": entries}, captured,
            ),
        )
        result = await plugin.call_tool("toggl_list_time_entries", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["entries"]) == 3
        assert data["entries"][0] == {
            "id": 1, "description": "First", "workspace_id": 1234,
            "project_id": 42, "start": "2026-05-20T10:00:00+00:00",
            "stop": "2026-05-20T11:00:00+00:00", "duration": 3600,
            "tags": [], "billable": False,
        }
        assert data["entries"][1]["tags"] == ["tag-a", "tag-b"]
        assert data["entries"][2]["billable"] is True
        call = captured[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{_BASE}/me/time_entries"
        assert call["params"] == {}

    @pytest.mark.asyncio
    async def test_since_param_forwarded(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/me/time_entries": []}, captured),
        )
        await plugin.call_tool(
            "toggl_list_time_entries", {"since": 1716220800},
        )
        assert captured[0]["params"] == {"since": 1716220800}

    @pytest.mark.asyncio
    async def test_blank_since_dropped(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/me/time_entries": []}, captured),
        )
        await plugin.call_tool(
            "toggl_list_time_entries", {"since": "not-a-number"},
        )
        assert captured[0]["params"] == {}

    @pytest.mark.asyncio
    async def test_max_results_clamped_low(self):
        entries = [_entry(i) for i in range(50)]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/me/time_entries": entries}, []),
        )
        result = await plugin.call_tool(
            "toggl_list_time_entries", {"max_results": 0},
        )
        assert len(json.loads(result.content)["entries"]) == 1
        result = await plugin.call_tool(
            "toggl_list_time_entries", {"max_results": -100},
        )
        assert len(json.loads(result.content)["entries"]) == 1

    @pytest.mark.asyncio
    async def test_max_results_clamped_high(self):
        entries = [_entry(i) for i in range(1500)]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/me/time_entries": entries}, []),
        )
        result = await plugin.call_tool(
            "toggl_list_time_entries", {"max_results": 5000},
        )
        assert len(json.loads(result.content)["entries"]) == 1000

    @pytest.mark.asyncio
    async def test_default_max_results_is_ten(self):
        entries = [_entry(i) for i in range(25)]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/me/time_entries": entries}, []),
        )
        result = await plugin.call_tool("toggl_list_time_entries", {})
        assert len(json.loads(result.content)["entries"]) == 10

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_entries(self):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/me/time_entries": []}, []),
        )
        result = await plugin.call_tool("toggl_list_time_entries", {})
        assert not result.is_error
        assert json.loads(result.content) == {"entries": []}

    @pytest.mark.asyncio
    async def test_non_list_response_is_error(self):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/me/time_entries": {"oops": True}}, [],
            ),
        )
        result = await plugin.call_tool("toggl_list_time_entries", {})
        assert result.is_error
        assert "unexpected Toggl list response" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- toggl_create_time_entry happy + required args + edge cases
# ---------------------------------------------------------------------------

class TestCreateTimeEntry:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": _CREATE_OK}, captured,
            ),
        )
        result = await plugin.call_tool("toggl_create_time_entry", {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
            "description": "Coding",
            "project_id": 42,
            "tags": ["focus"],
            "billable": False,
        })
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{_BASE}/workspaces/1234/time_entries"
        body = call["json"]
        assert body["wid"] == 1234
        assert body["start"] == "2026-05-20T15:00:00+00:00"
        assert body["duration"] == -1
        assert body["description"] == "Coding"
        assert body["project_id"] == 42
        assert body["tags"] == ["focus"]
        assert body["billable"] is False
        assert body["created_with"] == "cerebral/openmind"

    @pytest.mark.parametrize("missing_arg", ["wid", "start", "duration"])
    @pytest.mark.asyncio
    async def test_missing_required_arg_is_error(self, missing_arg):
        base_args = {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
        }
        del base_args[missing_arg]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool(
            "toggl_create_time_entry", base_args,
        )
        assert result.is_error
        assert missing_arg in result.content
        assert "toggl_create_time_entry" in result.content

    @pytest.mark.asyncio
    async def test_blank_string_args_dropped(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": _CREATE_OK}, captured,
            ),
        )
        await plugin.call_tool("toggl_create_time_entry", {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
            "description": "",
        })
        assert "description" not in captured[0]["json"]

    @pytest.mark.asyncio
    async def test_blank_tags_filtered(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": _CREATE_OK}, captured,
            ),
        )
        await plugin.call_tool("toggl_create_time_entry", {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
            "tags": ["", "real", None, "", 5],
        })
        assert captured[0]["json"]["tags"] == ["real"]

    @pytest.mark.asyncio
    async def test_all_blank_tags_drops_key(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": _CREATE_OK}, captured,
            ),
        )
        await plugin.call_tool("toggl_create_time_entry", {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
            "tags": ["", None],
        })
        assert "tags" not in captured[0]["json"]

    @pytest.mark.parametrize("bad_billable", [1, "yes", None, 0])
    @pytest.mark.asyncio
    async def test_non_bool_billable_dropped(self, bad_billable):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": _CREATE_OK}, captured,
            ),
        )
        await plugin.call_tool("toggl_create_time_entry", {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
            "billable": bad_billable,
        })
        assert "billable" not in captured[0]["json"]

    @pytest.mark.parametrize("bad_wid", ["", "not-a-number", None, True])
    @pytest.mark.asyncio
    async def test_invalid_wid_is_missing_arg_error(self, bad_wid):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("toggl_create_time_entry", {
            "wid": bad_wid,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
        })
        assert result.is_error
        assert "wid" in result.content

    @pytest.mark.asyncio
    async def test_string_wid_coerced_to_int(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": _CREATE_OK}, captured,
            ),
        )
        await plugin.call_tool("toggl_create_time_entry", {
            "wid": "1234",
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
        })
        assert captured[0]["json"]["wid"] == 1234
        assert "/workspaces/1234/" in captured[0]["url"]

    @pytest.mark.asyncio
    async def test_response_is_single_shaped_entry(self):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": _CREATE_OK}, [],
            ),
        )
        result = await plugin.call_tool("toggl_create_time_entry", {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == 99999
        assert data["description"] == "Created"
        assert data["duration"] == -1

    @pytest.mark.asyncio
    async def test_non_dict_response_is_error(self):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries": ["bogus"]}, [],
            ),
        )
        result = await plugin.call_tool("toggl_create_time_entry", {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
        })
        assert result.is_error
        assert "unexpected Toggl create response" in result.content


# ---------------------------------------------------------------------------
# Cycle 5 -- toggl_stop_running_entry happy + required args
# ---------------------------------------------------------------------------

class TestStopRunningEntry:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries/99999/stop": _STOP_OK},
                captured,
            ),
        )
        result = await plugin.call_tool("toggl_stop_running_entry", {
            "wid": 1234, "tid": 99999,
        })
        assert not result.is_error
        call = captured[0]
        assert call["method"] == "PATCH"
        assert call["url"] == (
            f"{_BASE}/workspaces/1234/time_entries/99999/stop"
        )
        data = json.loads(result.content)
        # Toggl stop returns 200+body (NOT 204 like Todoist), so the
        # plugin returns the shaped entry body.
        assert data["id"] == 99999
        assert data["duration"] == 3600

    @pytest.mark.parametrize("missing_arg", ["wid", "tid"])
    @pytest.mark.asyncio
    async def test_missing_required_arg_is_error(self, missing_arg):
        base_args = {"wid": 1234, "tid": 99999}
        del base_args[missing_arg]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool(
            "toggl_stop_running_entry", base_args,
        )
        assert result.is_error
        assert missing_arg in result.content
        assert "toggl_stop_running_entry" in result.content

    @pytest.mark.parametrize("bad_value", ["", "not-a-number", None, True])
    @pytest.mark.asyncio
    async def test_invalid_id_is_missing_arg_error(self, bad_value):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({}, []),
        )
        # tid is invalid -> "tid" in error
        result = await plugin.call_tool("toggl_stop_running_entry", {
            "wid": 1234, "tid": bad_value,
        })
        assert result.is_error
        assert "tid" in result.content

    @pytest.mark.asyncio
    async def test_non_dict_response_is_error(self):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/time_entries/99999/stop": ["bogus"]},
                [],
            ),
        )
        result = await plugin.call_tool("toggl_stop_running_entry", {
            "wid": 1234, "tid": 99999,
        })
        assert result.is_error
        assert "unexpected Toggl stop response" in result.content


# ---------------------------------------------------------------------------
# Cycle 6 -- toggl_list_workspaces + toggl_list_projects
# ---------------------------------------------------------------------------

class TestWorkspacesAndProjects:
    @pytest.mark.asyncio
    async def test_list_workspaces_happy(self):
        captured: list = []
        workspaces = [
            _workspace(1234, name="Main", default=True),
            _workspace(5678, name="Side"),
        ]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/workspaces": workspaces}, captured),
        )
        result = await plugin.call_tool("toggl_list_workspaces", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["workspaces"] == [
            {"id": 1234, "name": "Main", "default": True, "premium": False},
            {"id": 5678, "name": "Side", "default": False,
             "premium": False},
        ]
        call = captured[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{_BASE}/workspaces"

    @pytest.mark.asyncio
    async def test_list_workspaces_non_list_is_error(self):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/workspaces": {"oops": True}}, []),
        )
        result = await plugin.call_tool("toggl_list_workspaces", {})
        assert result.is_error
        assert "unexpected Toggl workspaces response" in result.content

    @pytest.mark.asyncio
    async def test_list_projects_happy(self):
        captured: list = []
        projects = [
            _project(42, name="Felix"),
            _project(43, name="OpenMind", color="ff0000"),
        ]
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/projects": projects}, captured,
            ),
        )
        result = await plugin.call_tool(
            "toggl_list_projects", {"wid": 1234},
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["projects"]) == 2
        assert data["projects"][0]["name"] == "Felix"
        assert data["projects"][1]["color"] == "ff0000"
        call = captured[0]
        assert call["method"] == "GET"
        assert call["url"] == f"{_BASE}/workspaces/1234/projects"

    @pytest.mark.parametrize("bad_wid", [None, "", "not-a-number", True])
    @pytest.mark.asyncio
    async def test_list_projects_missing_wid_is_error(self, bad_wid):
        args = {} if bad_wid is None else {"wid": bad_wid}
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("toggl_list_projects", args)
        assert result.is_error
        assert "wid" in result.content

    @pytest.mark.asyncio
    async def test_list_projects_non_list_is_error(self):
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/workspaces/1234/projects": {"oops": True}}, [],
            ),
        )
        result = await plugin.call_tool(
            "toggl_list_projects", {"wid": 1234},
        )
        assert result.is_error
        assert "unexpected Toggl projects response" in result.content


# ---------------------------------------------------------------------------
# Cycle 7 -- BASIC HEADER + token scrub (learning-#15 substitution, x5 tools)
# ---------------------------------------------------------------------------

_LIVE_ROUTES = {
    "toggl_list_time_entries": {
        "args": {},
        "route": "/me/time_entries",
        "resp": [],
    },
    "toggl_create_time_entry": {
        "args": {
            "wid": 1234,
            "start": "2026-05-20T15:00:00+00:00",
            "duration": -1,
        },
        "route": "/workspaces/1234/time_entries",
        "resp": _CREATE_OK,
    },
    "toggl_stop_running_entry": {
        "args": {"wid": 1234, "tid": 99999},
        "route": "/workspaces/1234/time_entries/99999/stop",
        "resp": _STOP_OK,
    },
    "toggl_list_workspaces": {
        "args": {},
        "route": "/workspaces",
        "resp": [],
    },
    "toggl_list_projects": {
        "args": {"wid": 1234},
        "route": "/workspaces/1234/projects",
        "resp": [],
    },
}


class TestBasicHeaderAndScrub:
    @pytest.mark.parametrize("tool_name", list(_LIVE_ROUTES.keys()))
    @pytest.mark.asyncio
    async def test_basic_header_attached_on_every_tool(self, tool_name):
        spec = _LIVE_ROUTES[tool_name]
        captured: list = []
        plugin = TogglPlugin(
            token_provider=_StubProvider("hdr-tok"),
            fetch_fn=_route_fetch({spec["route"]: spec["resp"]}, captured),
        )
        await plugin.call_tool(tool_name, dict(spec["args"]))
        expected = _basic_auth_header("hdr-tok")
        assert captured[0]["headers"]["Authorization"] == expected
        # Sanity: header value starts with 'Basic ' (not 'Bearer ')
        assert captured[0]["headers"]["Authorization"].startswith("Basic ")

    @pytest.mark.parametrize("tool_name", list(_LIVE_ROUTES.keys()))
    @pytest.mark.asyncio
    async def test_token_never_in_toolresult_on_error(self, tool_name):
        spec = _LIVE_ROUTES[tool_name]
        sentinel = "SENTINEL_TOOLRESULT_TOKEN"
        exc = TogglAPIError(
            f"401 Client Error -- Authorization: "
            f"Basic {_basic_auth_header(sentinel).split(' ', 1)[1]} "
            f"(raw token: {sentinel})",
            status=401,
        )
        plugin = TogglPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({spec["route"]: exc}, []),
        )
        result = await plugin.call_tool(tool_name, dict(spec["args"]))
        assert result.is_error
        # Both forms scrubbed: raw token AND base64 form
        assert sentinel not in result.content
        b64 = base64.b64encode(
            f"{sentinel}:api_token".encode("ascii")
        ).decode("ascii")
        assert b64 not in result.content
        assert "***" in result.content

    @pytest.mark.parametrize("tool_name", list(_LIVE_ROUTES.keys()))
    @pytest.mark.asyncio
    async def test_token_never_in_logs_on_error(self, tool_name, caplog):
        spec = _LIVE_ROUTES[tool_name]
        sentinel = "SENTINEL_LOG_TOKEN"
        b64 = base64.b64encode(
            f"{sentinel}:api_token".encode("ascii")
        ).decode("ascii")
        exc = TogglAPIError(f"boom Basic {b64} ({sentinel})", status=500)
        plugin = TogglPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({spec["route"]: exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool(tool_name, dict(spec["args"]))
        assert sentinel not in caplog.text
        assert b64 not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 8 -- no-token / factory-not-wired paths, parametrized x5 tools
# ---------------------------------------------------------------------------

class TestTokenWiring:
    @pytest.mark.parametrize("tool_name", list(_LIVE_ROUTES.keys()))
    @pytest.mark.asyncio
    async def test_factory_not_wired_returns_error(self, tool_name,
                                                     monkeypatch):
        import plugins.toggl as toggl_mod
        # Pretend production never wired the factory.
        monkeypatch.setattr(toggl_mod, "_token_provider_factory", None)
        spec = _LIVE_ROUTES[tool_name]
        plugin = TogglPlugin(
            token_provider=None,  # neither constructor- nor module-wired
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool(tool_name, dict(spec["args"]))
        assert result.is_error
        assert "token provider not wired" in result.content

    @pytest.mark.parametrize("tool_name", list(_LIVE_ROUTES.keys()))
    @pytest.mark.asyncio
    async def test_no_token_returns_error(self, tool_name, monkeypatch):
        import plugins.toggl as toggl_mod
        # Factory wired but returns None (env var unset).
        monkeypatch.setattr(
            toggl_mod, "_token_provider_factory", lambda: None,
        )
        spec = _LIVE_ROUTES[tool_name]
        plugin = TogglPlugin(
            token_provider=None,
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool(tool_name, dict(spec["args"]))
        assert result.is_error
        assert "no Toggl API token configured" in result.content


# ---------------------------------------------------------------------------
# Cycle 9 -- 401 propagates with NO refresh-and-retry (Protocol narrowing)
# ---------------------------------------------------------------------------

class TestNo401Retry:
    @pytest.mark.parametrize("tool_name", list(_LIVE_ROUTES.keys()))
    @pytest.mark.asyncio
    async def test_401_propagates_one_outbound_request(self, tool_name):
        spec = _LIVE_ROUTES[tool_name]
        captured: list = []
        exc = TogglAPIError("401 Unauthorized", status=401)
        plugin = TogglPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({spec["route"]: exc}, captured),
        )
        result = await plugin.call_tool(tool_name, dict(spec["args"]))
        assert result.is_error
        # EXACTLY one outbound request -- no refresh-and-retry path
        assert len(captured) == 1

    def test_token_provider_protocol_is_one_method(self):
        # The static-token Protocol carries ONLY current(). No
        # refresh() attr should exist on the stub used in production.
        from plugins.toggl import TokenProvider  # noqa: F401
        assert not hasattr(_StubProvider("x"), "refresh")


# ---------------------------------------------------------------------------
# Cycle 10 -- pure helpers (_shape_entry, _shape_workspace, _shape_project)
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_shape_entry_full_fields(self):
        from plugins.toggl import _shape_entry
        row = _entry(1, description="x", project_id=42, tags=["a"],
                     billable=True)
        out = _shape_entry(row)
        assert out["id"] == 1
        assert out["description"] == "x"
        assert out["project_id"] == 42
        assert out["tags"] == ["a"]
        assert out["billable"] is True

    def test_shape_entry_missing_keys_fallback_to_defaults(self):
        from plugins.toggl import _shape_entry
        out = _shape_entry({"id": 7})
        assert out["id"] == 7
        assert out["description"] == ""
        assert out["workspace_id"] == 0
        assert out["project_id"] == 0
        assert out["tags"] == []
        assert out["billable"] is False

    def test_shape_entry_non_dict_returns_empty(self):
        from plugins.toggl import _shape_entry
        assert _shape_entry("not-a-dict") == {}
        assert _shape_entry(None) == {}
        assert _shape_entry([1, 2, 3]) == {}

    def test_shape_workspace_non_dict_returns_empty(self):
        from plugins.toggl import _shape_workspace
        assert _shape_workspace("nope") == {}

    def test_shape_project_non_dict_returns_empty(self):
        from plugins.toggl import _shape_project
        assert _shape_project(None) == {}

    def test_shape_project_default_active_true(self):
        from plugins.toggl import _shape_project
        # Missing 'active' key defaults to True (Toggl's server-side
        # default; an active project is the common case).
        out = _shape_project({"id": 1, "name": "P"})
        assert out["active"] is True
