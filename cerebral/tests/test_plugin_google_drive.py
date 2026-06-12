"""
Google Drive MCP plugin tests -- Issue #228.

Tools: drive_list_files, drive_upload_file, drive_download_file,
drive_create_folder, drive_share_file (real Google Drive API v3, OAuth bearer
from the #112 store via #113). All HTTP is injected via fetch_fn and the
bearer token via a stub provider, so tests never read the keyring, run OAuth,
or hit the network.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.google_drive import (  # noqa: E402
    DriveAPIError,
    GoogleDrivePlugin,
    REQUIRED_CAPABILITIES,
    create,
)

_BASE = "https://www.googleapis.com/drive/v3"
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _StubProvider:
    """Bearer-token provider double."""

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
    """Build a fetch_fn that dispatches on a URL substring."""
    async def fake_fetch(method, url, *, headers=None, params=None,
                         json=None, data=None, content_type=None):
        captured.append({
            "method": method, "url": url,
            "headers": headers or {}, "params": params or {},
            "json": json or {}, "data": data, "content_type": content_type,
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


def _file_meta(fid, *, name="report.txt", mime="text/plain",
               size="1024", view="https://drive.example/view",
               download="https://drive.example/download"):
    return {
        "id": fid, "name": name, "mimeType": mime,
        "size": size, "webViewLink": view, "webContentLink": download,
    }


def _files_list_resp(*files):
    return {"files": list(files)}


_UPLOAD_OK = {"id": "file-1", "name": "notes.txt", "mimeType": "text/plain"}
_FOLDER_OK = {"id": "folder-1", "name": "MyFolder",
              "mimeType": "application/vnd.google-apps.folder"}
_PERMISSION_OK = {"id": "perm-1", "role": "reader"}


# ---------------------------------------------------------------------------
# Cycle 1 -- list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_all_tools(self):
        names = {t.name for t in create().list_tools()}
        assert names == {
            "drive_list_files",
            "drive_upload_file",
            "drive_download_file",
            "drive_create_folder",
            "drive_share_file",
        }

    def test_create_plugin_named_google_drive(self):
        assert create().name == "google_drive"

    def test_required_capabilities(self):
        assert REQUIRED_CAPABILITIES == frozenset({
            "secrets_read",
            "external_data_read",
            "external_data_write",
            "network_egress_cloud",
        })

    def test_list_files_schema_no_required(self):
        tool = next(t for t in create().list_tools() if t.name == "drive_list_files")
        assert tool.schema.get("required", []) == []
        assert "query" in tool.schema["properties"]
        assert "max_results" in tool.schema["properties"]

    def test_upload_file_schema_required(self):
        tool = next(t for t in create().list_tools() if t.name == "drive_upload_file")
        assert set(tool.schema.get("required", [])) == {"name", "content"}
        for opt in ("mime_type", "parent_id"):
            assert opt in tool.schema["properties"]

    def test_download_file_schema_required(self):
        tool = next(
            t for t in create().list_tools() if t.name == "drive_download_file"
        )
        assert set(tool.schema.get("required", [])) == {"file_id"}

    def test_create_folder_schema_required(self):
        tool = next(
            t for t in create().list_tools() if t.name == "drive_create_folder"
        )
        assert set(tool.schema.get("required", [])) == {"name"}
        assert "parent_id" in tool.schema["properties"]

    def test_share_file_schema_required(self):
        tool = next(t for t in create().list_tools() if t.name == "drive_share_file")
        assert set(tool.schema.get("required", [])) == {"file_id", "email"}
        assert "role" in tool.schema["properties"]


# ---------------------------------------------------------------------------
# Cycle 2 -- no provider / no connected account
# ---------------------------------------------------------------------------

class TestNoAccount:
    @pytest.mark.asyncio
    async def test_factory_not_wired_constructs_but_errors(self, monkeypatch):
        import plugins.google_drive as drive_mod
        monkeypatch.setattr(drive_mod, "_token_provider_factory", None)

        plugin = create()
        result = await plugin.call_tool("drive_list_files", {})
        assert result.is_error
        assert "not wired" in result.content

    @pytest.mark.asyncio
    async def test_factory_returns_none_is_no_account(self, monkeypatch):
        import plugins.google_drive as drive_mod
        monkeypatch.setattr(
            drive_mod, "_token_provider_factory", lambda: None
        )

        plugin = create()
        result = await plugin.call_tool("drive_list_files", {})
        assert result.is_error
        assert "no Google account connected" in result.content


# ---------------------------------------------------------------------------
# Cycle 3 -- required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_upload_missing_name_is_error(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("drive_upload_file", {"content": "hi"})
        assert result.is_error
        assert "name" in result.content

    @pytest.mark.asyncio
    async def test_upload_missing_content_is_error(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("drive_upload_file", {"name": "f.txt"})
        assert result.is_error
        assert "content" in result.content

    @pytest.mark.asyncio
    async def test_download_missing_file_id_is_error(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("drive_download_file", {})
        assert result.is_error
        assert "file_id" in result.content

    @pytest.mark.asyncio
    async def test_create_folder_missing_name_is_error(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("drive_create_folder", {})
        assert result.is_error
        assert "name" in result.content

    @pytest.mark.asyncio
    async def test_share_missing_email_is_error(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("drive_share_file", {"file_id": "f-1"})
        assert result.is_error
        assert "email" in result.content

    @pytest.mark.asyncio
    async def test_share_missing_file_id_is_error(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool(
            "drive_share_file", {"email": "a@b.com"}
        )
        assert result.is_error
        assert "file_id" in result.content


# ---------------------------------------------------------------------------
# Cycle 4 -- drive_list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    @pytest.mark.asyncio
    async def test_list_files_shapes_results(self):
        captured: list = []
        routes = {
            "/drive/v3/files": _files_list_resp(
                _file_meta("f1", name="report.txt"),
                _file_meta("f2", name="notes.md", mime="text/markdown"),
            ),
        }
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool("drive_list_files", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert len(data["files"]) == 2
        assert data["files"][0]["id"] == "f1"
        assert data["files"][0]["name"] == "report.txt"
        assert data["files"][1]["mimeType"] == "text/markdown"
        call = captured[0]
        assert call["method"] == "GET"
        assert "/drive/v3/files" in call["url"]
        assert call["params"]["pageSize"] == 10

    @pytest.mark.asyncio
    async def test_list_files_with_query(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/drive/v3/files": _files_list_resp()}, captured
            ),
        )
        await plugin.call_tool(
            "drive_list_files", {"query": "name contains 'report'"}
        )
        assert captured[0]["params"].get("q") == "name contains 'report'"

    @pytest.mark.asyncio
    async def test_list_files_custom_max_results(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/drive/v3/files": _files_list_resp()}, captured
            ),
        )
        await plugin.call_tool("drive_list_files", {"max_results": 5})
        assert captured[0]["params"]["pageSize"] == 5

    @pytest.mark.asyncio
    async def test_list_files_empty(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/drive/v3/files": {"files": []}}, []),
        )
        result = await plugin.call_tool("drive_list_files", {})
        assert not result.is_error
        assert json.loads(result.content) == {"files": []}

    @pytest.mark.asyncio
    async def test_list_files_respects_max_results_cap(self):
        files = [_file_meta(f"f{i}") for i in range(5)]
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/drive/v3/files": _files_list_resp(*files)}, []
            ),
        )
        result = await plugin.call_tool("drive_list_files", {"max_results": 2})
        assert len(json.loads(result.content)["files"]) == 2


# ---------------------------------------------------------------------------
# Cycle 5 -- drive_upload_file
# ---------------------------------------------------------------------------

class TestUploadFile:
    @pytest.mark.asyncio
    async def test_upload_posts_multipart(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/upload/drive/v3/files": _UPLOAD_OK}, captured
            ),
        )
        result = await plugin.call_tool("drive_upload_file", {
            "name": "notes.txt",
            "content": "Hello world",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "file-1"
        assert data["name"] == "notes.txt"
        assert data["status"] == "uploaded"
        call = captured[0]
        assert call["method"] == "POST"
        assert "/upload/drive/v3/files" in call["url"]
        assert call["params"]["uploadType"] == "multipart"
        assert call["content_type"] is not None
        assert "multipart/related" in call["content_type"]

    @pytest.mark.asyncio
    async def test_upload_with_parent_id(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/upload/drive/v3/files": _UPLOAD_OK}, captured
            ),
        )
        await plugin.call_tool("drive_upload_file", {
            "name": "doc.txt",
            "content": "content",
            "parent_id": "folder-99",
        })
        data_bytes = captured[0]["data"]
        assert b"folder-99" in data_bytes

    @pytest.mark.asyncio
    async def test_upload_custom_mime_type(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/upload/drive/v3/files": _UPLOAD_OK}, captured
            ),
        )
        await plugin.call_tool("drive_upload_file", {
            "name": "data.json",
            "content": "{}",
            "mime_type": "application/json",
        })
        assert b"application/json" in captured[0]["data"]


# ---------------------------------------------------------------------------
# Cycle 6 -- drive_download_file
# ---------------------------------------------------------------------------

class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_download_returns_metadata_and_links(self):
        captured: list = []
        routes = {
            "/drive/v3/files/file-1": _file_meta(
                "file-1",
                name="report.txt",
                mime="text/plain",
                size="2048",
                view="https://view.example",
                download="https://download.example",
            ),
        }
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(routes, captured),
        )
        result = await plugin.call_tool(
            "drive_download_file", {"file_id": "file-1"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "file-1"
        assert data["name"] == "report.txt"
        assert data["mimeType"] == "text/plain"
        assert data["webViewLink"] == "https://view.example"
        assert data["webContentLink"] == "https://download.example"
        call = captured[0]
        assert call["method"] == "GET"
        assert "/drive/v3/files/file-1" in call["url"]


# ---------------------------------------------------------------------------
# Cycle 7 -- drive_create_folder
# ---------------------------------------------------------------------------

class TestCreateFolder:
    @pytest.mark.asyncio
    async def test_create_folder_posts_correct_body(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/drive/v3/files": _FOLDER_OK}, captured),
        )
        result = await plugin.call_tool(
            "drive_create_folder", {"name": "MyFolder"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "folder-1"
        assert data["name"] == "MyFolder"
        assert data["status"] == "created"
        call = captured[0]
        assert call["method"] == "POST"
        assert call["json"]["mimeType"] == "application/vnd.google-apps.folder"
        assert call["json"]["name"] == "MyFolder"
        assert "parents" not in call["json"]

    @pytest.mark.asyncio
    async def test_create_folder_with_parent(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch({"/drive/v3/files": _FOLDER_OK}, captured),
        )
        await plugin.call_tool(
            "drive_create_folder", {"name": "Sub", "parent_id": "parent-1"}
        )
        assert captured[0]["json"]["parents"] == ["parent-1"]


# ---------------------------------------------------------------------------
# Cycle 8 -- drive_share_file
# ---------------------------------------------------------------------------

class TestShareFile:
    @pytest.mark.asyncio
    async def test_share_posts_permission(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/drive/v3/files/file-1/permissions": _PERMISSION_OK}, captured
            ),
        )
        result = await plugin.call_tool("drive_share_file", {
            "file_id": "file-1",
            "email": "user@example.com",
        })
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "perm-1"
        assert data["role"] == "reader"
        assert data["status"] == "shared"
        call = captured[0]
        assert call["method"] == "POST"
        assert "/files/file-1/permissions" in call["url"]
        assert call["json"]["type"] == "user"
        assert call["json"]["role"] == "reader"
        assert call["json"]["emailAddress"] == "user@example.com"

    @pytest.mark.asyncio
    async def test_share_custom_role(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("tok"),
            fetch_fn=_route_fetch(
                {"/drive/v3/files/f1/permissions": _PERMISSION_OK}, captured
            ),
        )
        await plugin.call_tool("drive_share_file", {
            "file_id": "f1",
            "email": "editor@example.com",
            "role": "writer",
        })
        assert captured[0]["json"]["role"] == "writer"


# ---------------------------------------------------------------------------
# Cycle 9 -- 401 -> refresh -> retry once
# ---------------------------------------------------------------------------

class TestRefreshRetry:
    @pytest.mark.asyncio
    async def test_list_files_401_triggers_refresh_and_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise DriveAPIError("401 Unauthorized", status=401)
            return _files_list_resp(_file_meta("f1"))

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/drive/v3/files": route}, captured),
        )
        result = await plugin.call_tool("drive_list_files", {})
        assert not result.is_error
        assert prov.refresh_calls == 1
        retry = [c for c in captured if "/drive/v3/files" in c["url"]][-1]
        assert retry["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_upload_401_triggers_refresh_and_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise DriveAPIError("401 Unauthorized", status=401)
            return _UPLOAD_OK

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/upload/drive/v3/files": route}, captured),
        )
        result = await plugin.call_tool("drive_upload_file", {
            "name": "f.txt", "content": "hello",
        })
        assert not result.is_error
        assert prov.refresh_calls == 1
        assert captured[-1]["headers"]["Authorization"] == "Bearer fresh"

    @pytest.mark.asyncio
    async def test_create_folder_401_triggers_refresh_and_succeeds(self):
        state = {"first": True}

        def route():
            if state["first"]:
                state["first"] = False
                raise DriveAPIError("401 Unauthorized", status=401)
            return _FOLDER_OK

        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=prov,
            fetch_fn=_route_fetch({"/drive/v3/files": route}, captured),
        )
        result = await plugin.call_tool(
            "drive_create_folder", {"name": "NewDir"}
        )
        assert not result.is_error
        assert prov.refresh_calls == 1

    @pytest.mark.asyncio
    async def test_persistent_401_refreshes_once_then_errors(self):
        prov = _StubProvider(current_token="stale", refresh_tokens=("fresh",))
        plugin = GoogleDrivePlugin(
            token_provider=prov,
            fetch_fn=_route_fetch(
                {"/drive/v3/files": DriveAPIError("401", status=401)}, []
            ),
        )
        result = await plugin.call_tool("drive_list_files", {})
        assert result.is_error
        assert prov.refresh_calls == 1


# ---------------------------------------------------------------------------
# Cycle 10 -- Bearer header + token scrub
# ---------------------------------------------------------------------------

class TestBearerHeaderAndScrub:
    @pytest.mark.asyncio
    async def test_bearer_header_attached_on_every_call(self):
        captured: list = []
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("abc123"),
            fetch_fn=_route_fetch(
                {"/drive/v3/files": _files_list_resp()}, captured
            ),
        )
        await plugin.call_tool("drive_list_files", {})
        assert captured[0]["headers"]["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_token_never_in_toolresult(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = DriveAPIError(
            f"401 Client Error (Authorization: Bearer {sentinel})", status=401
        )
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider(sentinel,
                                          refresh_tokens=(sentinel,)),
            fetch_fn=_route_fetch({"/drive/v3/files": exc}, []),
        )
        result = await plugin.call_tool("drive_list_files", {})
        assert result.is_error
        assert sentinel not in result.content
        assert "***" in result.content

    @pytest.mark.asyncio
    async def test_token_never_in_logs(self, caplog):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK"
        exc = DriveAPIError(f"boom Bearer {sentinel}", status=500)
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider(sentinel),
            fetch_fn=_route_fetch({"/drive/v3/files": exc}, []),
        )
        with caplog.at_level(logging.ERROR):
            await plugin.call_tool("drive_list_files", {})
        assert sentinel not in caplog.text
        assert "***" in caplog.text


# ---------------------------------------------------------------------------
# Cycle 11 -- dispatch + module-level factory
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        plugin = GoogleDrivePlugin(
            token_provider=_StubProvider("t"),
            fetch_fn=_route_fetch({}, []),
        )
        result = await plugin.call_tool("drive_bogus", {})
        assert result.is_error
        assert "Unknown tool" in result.content

    def test_create_is_module_level_factory(self):
        assert isinstance(create(), GoogleDrivePlugin)
