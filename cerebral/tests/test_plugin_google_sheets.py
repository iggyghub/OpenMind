"""
Google Sheets MCP plugin tests -- Issue #225.

Tools: sheets_list (read) + sheets_read_range (read) + sheets_write_range
(write) + sheets_append_row (write) + sheets_create (write). All HTTP is
injected via fetch_fn and the bearer token via a stub provider, so tests
never read the keyring, run OAuth, or hit the network.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.google_sheets import (  # noqa: E402
    SheetsAPIError,
    GoogleSheetsPlugin,
    REQUIRED_CAPABILITIES,
    create,
)

_SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _StubProvider:
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


def _file_resp(fid, name="My Sheet", modified="2026-01-01T00:00:00Z",
               link="https://docs.google.com/spreadsheets/d/abc"):
    return {"id": fid, "name": name, "modifiedTime": modified,
            "webViewLink": link}


def _list_resp(*files):
    return {"files": list(files)}


def _values_resp(spreadsheet_id, range_, values=None):
    return {
        "spreadsheetId": spreadsheet_id,
        "range": range_,
        "values": values or [],
    }


def _update_resp(spreadsheet_id, updated_range, updated_cells=4):
    return {
        "spreadsheetId": spreadsheet_id,
        "updatedRange": updated_range,
        "updatedCells": updated_cells,
    }


def _append_resp(spreadsheet_id, updated_range, updated_rows=1):
    return {
        "spreadsheetId": spreadsheet_id,
        "updates": {
            "updatedRange": updated_range,
            "updatedRows": updated_rows,
        },
    }


def _create_resp(spreadsheet_id, title="My Sheet"):
    return {
        "spreadsheetId": spreadsheet_id,
        "properties": {"title": title},
    }


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities (posture-B)
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_all_five(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "sheets_list",
            "sheets_read_range",
            "sheets_write_range",
            "sheets_append_row",
            "sheets_create",
        }

    def test_create_plugin_named_google_sheets(self):
        assert create().name == "google_sheets"

    def test_required_capabilities(self):
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_write_tools_are_irreversible(self):
        tools = {t.name: t for t in create().list_tools()}
        assert tools["sheets_write_range"].irreversible is True
        assert tools["sheets_append_row"].irreversible is True
        assert tools["sheets_create"].irreversible is True

    def test_read_tools_are_not_irreversible(self):
        tools = {t.name: t for t in create().list_tools()}
        assert tools["sheets_list"].irreversible is False
        assert tools["sheets_read_range"].irreversible is False

    def test_read_range_schema_required(self):
        tools = {t.name: t for t in create().list_tools()}
        req = set(tools["sheets_read_range"].schema.get("required", []))
        assert req == {"spreadsheet_id", "range"}

    def test_write_range_schema_required(self):
        tools = {t.name: t for t in create().list_tools()}
        req = set(tools["sheets_write_range"].schema.get("required", []))
        assert req == {"spreadsheet_id", "range", "values"}

    def test_append_row_schema_required(self):
        tools = {t.name: t for t in create().list_tools()}
        req = set(tools["sheets_append_row"].schema.get("required", []))
        assert req == {"spreadsheet_id", "range", "values"}

    def test_create_schema_required(self):
        tools = {t.name: t for t in create().list_tools()}
        req = tools["sheets_create"].schema.get("required", [])
        assert "title" in req


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no connected account
# ---------------------------------------------------------------------------

class TestNoAccount:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.google_sheets as sheets_mod
        monkeypatch.setattr(sheets_mod, "_token_provider_factory", None)

        plugin = create()
        result = await plugin.call_tool("sheets_list", {})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_account(self, monkeypatch):
        import plugins.google_sheets as sheets_mod
        monkeypatch.setattr(
            sheets_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("sheets_list", {})
        assert result.is_error
        assert "no Google account connected" in result.content

    @pytest.mark.asyncio
    async def test_module_setter_is_the_wiring_seam(self, monkeypatch):
        import plugins.google_sheets as sheets_mod
        prov = _StubProvider(current_token="tok")
        sheets_mod.set_token_provider(lambda: prov)
        try:
            captured: list = []
            plugin = GoogleSheetsPlugin(
                fetch_fn=_route_fetch(
                    {_DRIVE_FILES: _list_resp()}, captured
                )
            )
            result = await plugin.call_tool("sheets_list", {})
            assert not result.is_error
        finally:
            monkeypatch.setattr(
                sheets_mod, "_token_provider_factory", None
            )


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_missing_spreadsheet_id_for_read_range(self):
        plugin = GoogleSheetsPlugin(token_provider=_StubProvider("t"),
                                    fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("sheets_read_range",
                                        {"range": "Sheet1!A1:B2"})
        assert result.is_error
        assert "spreadsheet_id" in result.content

    @pytest.mark.asyncio
    async def test_missing_range_for_read_range(self):
        plugin = GoogleSheetsPlugin(token_provider=_StubProvider("t"),
                                    fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("sheets_read_range",
                                        {"spreadsheet_id": "s1"})
        assert result.is_error
        assert "range" in result.content

    @pytest.mark.asyncio
    async def test_missing_values_for_write_range(self):
        plugin = GoogleSheetsPlugin(token_provider=_StubProvider("t"),
                                    fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "s1", "range": "Sheet1!A1",
        })
        assert result.is_error
        assert "values" in result.content

    @pytest.mark.asyncio
    async def test_missing_values_for_append_row(self):
        plugin = GoogleSheetsPlugin(token_provider=_StubProvider("t"),
                                    fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("sheets_append_row", {
            "spreadsheet_id": "s1", "range": "Sheet1",
        })
        assert result.is_error
        assert "values" in result.content

    @pytest.mark.asyncio
    async def test_missing_title_for_create(self):
        plugin = GoogleSheetsPlugin(token_provider=_StubProvider("t"),
                                    fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("sheets_create", {})
        assert result.is_error
        assert "title" in result.content

    @pytest.mark.asyncio
    async def test_blank_spreadsheet_id_is_error(self):
        plugin = GoogleSheetsPlugin(token_provider=_StubProvider("t"),
                                    fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "", "range": "Sheet1!A1",
        })
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 -- response shaping
# ---------------------------------------------------------------------------

class TestShaping:
    @pytest.mark.asyncio
    async def test_sheets_list_shapes_files(self):
        f1 = _file_resp("s1", name="Budget", modified="2026-01-02T00:00:00Z",
                        link="https://docs.google.com/spreadsheets/d/s1")
        f2 = _file_resp("s2", name="Log", modified="2026-01-01T00:00:00Z",
                        link="https://docs.google.com/spreadsheets/d/s2")
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp(f1, f2)}, []),
        )
        result = await plugin.call_tool("sheets_list", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["spreadsheets"][0] == {
            "id": "s1", "name": "Budget",
            "modified_time": "2026-01-02T00:00:00Z",
            "web_view_link": "https://docs.google.com/spreadsheets/d/s1",
        }
        assert data["spreadsheets"][1]["id"] == "s2"

    @pytest.mark.asyncio
    async def test_sheets_list_default_max_results(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("sheets_list", {})
        assert captured[0]["params"]["pageSize"] == 10

    @pytest.mark.asyncio
    async def test_sheets_list_custom_max_results(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("sheets_list", {"max_results": 5})
        assert captured[0]["params"]["pageSize"] == 5

    @pytest.mark.asyncio
    async def test_sheets_list_filters_to_sheets_mime(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("sheets_list", {})
        q = captured[0]["params"]["q"]
        assert "application/vnd.google-apps.spreadsheet" in q

    @pytest.mark.asyncio
    async def test_sheets_list_empty_returns_empty(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, []),
        )
        result = await plugin.call_tool("sheets_list", {})
        assert not result.is_error
        assert json.loads(result.content) == {"spreadsheets": []}

    @pytest.mark.asyncio
    async def test_sheets_read_range_shapes_response(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/values/": _values_resp(
                    "s1", "Sheet1!A1:B2", [["a", "b"], ["c", "d"]]
                )},
                [],
            ),
        )
        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "s1", "range": "Sheet1!A1:B2",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["spreadsheet_id"] == "s1"
        assert data["values"] == [["a", "b"], ["c", "d"]]

    @pytest.mark.asyncio
    async def test_sheets_read_range_empty_values(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/values/": _values_resp("s1", "Sheet1!A1:A1")},
                [],
            ),
        )
        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "s1", "range": "Sheet1!A1:A1",
        })
        assert not result.is_error
        assert json.loads(result.content)["values"] == []

    @pytest.mark.asyncio
    async def test_sheets_write_range_shapes_response(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/values/": _update_resp("s1", "Sheet1!A1:B2", 4)},
                [],
            ),
        )
        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "s1",
            "range": "Sheet1!A1:B2",
            "values": [["x", "y"], ["1", "2"]],
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["spreadsheet_id"] == "s1"
        assert data["updated_cells"] == 4

    @pytest.mark.asyncio
    async def test_sheets_write_range_uses_put_with_raw(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/values/": _update_resp("s1", "Sheet1!A1:B2", 4)},
                captured,
            ),
        )
        await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "s1",
            "range": "Sheet1!A1:B2",
            "values": [["x"]],
        })
        call = captured[0]
        assert call["method"] == "PUT"
        assert call["params"].get("valueInputOption") == "RAW"

    @pytest.mark.asyncio
    async def test_sheets_append_row_shapes_response(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {":append": _append_resp("s1", "Sheet1!A5", 1)},
                [],
            ),
        )
        result = await plugin.call_tool("sheets_append_row", {
            "spreadsheet_id": "s1",
            "range": "Sheet1",
            "values": ["col1", "col2"],
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["spreadsheet_id"] == "s1"
        assert data["updated_rows"] == 1

    @pytest.mark.asyncio
    async def test_sheets_append_row_wraps_values_in_2d_array(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {":append": _append_resp("s1", "Sheet1!A5", 1)},
                captured,
            ),
        )
        await plugin.call_tool("sheets_append_row", {
            "spreadsheet_id": "s1",
            "range": "Sheet1",
            "values": ["a", "b", "c"],
        })
        call = next(c for c in captured if ":append" in c["url"])
        assert call["json"]["values"] == [["a", "b", "c"]]

    @pytest.mark.asyncio
    async def test_sheets_create_shapes_response(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {_SHEETS_BASE: _create_resp("new1", "My Sheet")},
                [],
            ),
        )
        result = await plugin.call_tool("sheets_create", {"title": "My Sheet"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data == {"spreadsheet_id": "new1", "title": "My Sheet"}

    @pytest.mark.asyncio
    async def test_sheets_create_posts_title_in_properties(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {_SHEETS_BASE: _create_resp("new1", "Foo")},
                captured,
            ),
        )
        await plugin.call_tool("sheets_create", {"title": "Foo"})
        call = next(c for c in captured if _SHEETS_BASE == c["url"])
        assert call["json"]["properties"]["title"] == "Foo"


# ---------------------------------------------------------------------------
# Cycle 5 -- 401 -> refresh -> retry once -> then error (no infinite loop)
# ---------------------------------------------------------------------------

class TestRefreshRetry:
    @pytest.mark.asyncio
    async def test_list_401_triggers_one_refresh_then_succeeds(self):
        state = {"first": True}

        def list_route():
            if state["first"]:
                state["first"] = False
                raise SheetsAPIError("401", status=401)
            return _list_resp(_file_resp("s1"))

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({_DRIVE_FILES: list_route}, captured),
        )
        result = await plugin.call_tool("sheets_list", {})
        assert not result.is_error
        assert prov.refresh_calls == 1
        retry = [c for c in captured if _DRIVE_FILES in c["url"]][-1]
        assert retry["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_persistent_401_refreshes_once_then_errors(self):
        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {_DRIVE_FILES: SheetsAPIError("401", status=401)}, []
            ),
        )
        result = await plugin.call_tool("sheets_list", {})
        assert result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_non_401_does_not_refresh(self):
        prov = _StubProvider(current_token="tok")
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {_DRIVE_FILES: SheetsAPIError("500", status=500)}, []
            ),
        )
        result = await plugin.call_tool("sheets_list", {})
        assert result.is_error
        assert prov.refresh_calls == 0

    @pytest.mark.asyncio
    async def test_no_stored_token_refreshes_before_first_call(self):
        prov = _StubProvider(current_token=None, refresh_tokens=("first",))
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("sheets_list", {})
        assert prov.refresh_calls == 1
        assert captured[0]["headers"]["Authorization"] == "Bearer first"

    @pytest.mark.asyncio
    async def test_read_range_401_triggers_refresh(self):
        state = {"first": True}

        def read_route():
            if state["first"]:
                state["first"] = False
                raise SheetsAPIError("401", status=401)
            return _values_resp("s1", "Sheet1!A1:A1")

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/values/": read_route}, []),
        )
        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "s1", "range": "Sheet1!A1:A1",
        })
        assert not result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_write_range_401_triggers_refresh(self):
        state = {"first": True}

        def write_route():
            if state["first"]:
                state["first"] = False
                raise SheetsAPIError("401", status=401)
            return _update_resp("s1", "Sheet1!A1", 1)

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/values/": write_route}, []),
        )
        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "s1", "range": "Sheet1!A1", "values": [["x"]],
        })
        assert not result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_append_row_401_triggers_refresh(self):
        state = {"first": True}

        def append_route():
            if state["first"]:
                state["first"] = False
                raise SheetsAPIError("401", status=401)
            return _append_resp("s1", "Sheet1!A5", 1)

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({":append": append_route}, []),
        )
        result = await plugin.call_tool("sheets_append_row", {
            "spreadsheet_id": "s1", "range": "Sheet1", "values": ["a"],
        })
        assert not result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_create_401_triggers_refresh(self):
        state = {"first": True}

        def create_route():
            if state["first"]:
                state["first"] = False
                raise SheetsAPIError("401", status=401)
            return _create_resp("new1")

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        plugin = GoogleSheetsPlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({_SHEETS_BASE: create_route}, []),
        )
        result = await plugin.call_tool("sheets_create", {"title": "T"})
        assert not result.is_error
        assert prov.refresh_calls == 1


# ---------------------------------------------------------------------------
# Cycle 6 -- Bearer header + token scrub
# ---------------------------------------------------------------------------

class TestBearerHeaderAndScrub:
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_list(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch({_DRIVE_FILES: _list_resp()}, captured),
        )
        await plugin.call_tool("sheets_list", {})
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_read_range(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(
                {"/values/": _values_resp("s1", "Sheet1!A1")}, captured
            ),
        )
        await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "s1", "range": "Sheet1!A1",
        })
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_create(self):
        captured: list = []
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(
                {_SHEETS_BASE: _create_resp("s1")}, captured
            ),
        )
        await plugin.call_tool("sheets_create", {"title": "T"})
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult(self):
        sentinel = "SENTINEL_SHEETS_TOKEN_DO_NOT_LEAK"
        exc = SheetsAPIError(
            f"401 Client Error for url: {_DRIVE_FILES} "
            f"(Authorization: Bearer {sentinel})",
            status=401,
        )
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider(sentinel,
                                         refresh_tokens=(sentinel,)),
            fetch_fn=_route_fetch({_DRIVE_FILES: exc}, []),
        )
        result = await plugin.call_tool("sheets_list", {})
        assert result.is_error
        assert sentinel not in result.content
        assert "***" in result.content

    @pytest.mark.asyncio
    async def test_token_never_in_logs(self, caplog):
        sentinel = "SENTINEL_SHEETS_TOKEN_DO_NOT_LEAK"
        exc = SheetsAPIError(f"boom Bearer {sentinel}", status=500)
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({_DRIVE_FILES: exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("sheets_list", {})
        assert sentinel not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 7 -- non-dict API response guards
# ---------------------------------------------------------------------------

class TestNonDictGuards:
    @pytest.mark.asyncio
    async def test_non_dict_list_response_is_error(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_DRIVE_FILES: [1, 2]}, []),
        )
        result = await plugin.call_tool("sheets_list", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_dict_read_range_response_is_error(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/values/": "bad"}, []),
        )
        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "s1", "range": "Sheet1!A1",
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_dict_write_range_response_is_error(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/values/": [1, 2]}, []),
        )
        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "s1", "range": "A1", "values": [["x"]],
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_dict_append_row_response_is_error(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({":append": "bad"}, []),
        )
        result = await plugin.call_tool("sheets_append_row", {
            "spreadsheet_id": "s1", "range": "Sheet1", "values": ["x"],
        })
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_dict_create_response_is_error(self):
        plugin = GoogleSheetsPlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({_SHEETS_BASE: [1, 2]}, []),
        )
        result = await plugin.call_tool("sheets_create", {"title": "T"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = GoogleSheetsPlugin(token_provider=_StubProvider("t"),
                                    fetch_fn=_route_fetch({}, []))
        result = await plugin.call_tool("sheets_bogus", {})
        assert result.is_error
        assert "Unknown tool: 'sheets_bogus'" in result.content

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), GoogleSheetsPlugin)

    @pytest.mark.asyncio
    async def test_no_account_is_lazy_error_for_create(self, monkeypatch):
        import plugins.google_sheets as sheets_mod
        monkeypatch.setattr(sheets_mod, "_token_provider_factory",
                            lambda: None)
        plugin = create()
        result = await plugin.call_tool("sheets_create", {"title": "T"})
        assert result.is_error
        assert "no Google account connected" in result.content

    @pytest.mark.asyncio
    async def test_no_account_is_lazy_error_for_write(self, monkeypatch):
        import plugins.google_sheets as sheets_mod
        monkeypatch.setattr(sheets_mod, "_token_provider_factory",
                            lambda: None)
        plugin = create()
        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "s1", "range": "A1", "values": [["x"]],
        })
        assert result.is_error
        assert "no Google account connected" in result.content
