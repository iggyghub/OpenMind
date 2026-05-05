"""
Google Workspace MCP plugin tests — Issue #20.

TDD vertical slices for GoogleWorkspacePlugin:
  - gmail_send, gmail_search
  - calendar_create_event, calendar_list_events
  - drive_list_files, drive_upload_file
  - sheets_read_range, sheets_write_range

All HTTP calls go through an injected N8nPlugin so no live n8n or Google
calls are made during tests.

Delegation chain:
  GoogleWorkspacePlugin.call_tool(tool, args)
    → N8nPlugin.call_tool("trigger_workflow", {"name": WORKFLOW, "data": payload})
      → fake_fetch (injected at test time)
"""
import json
import sys
from pathlib import Path

import pytest

# Ensure plugins/ is importable from anywhere in the tree
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers — build a fake_fetch that mimics n8n for one workflow
# ---------------------------------------------------------------------------

def _make_fetch(workflow_name: str, *, captured: dict | None = None):
    """
    Return a fake_fetch that:
      - answers GET /api/v1/workflows with a single workflow whose name matches
      - answers POST /webhook/... with {"executionId": "exec-1"}
      - optionally records the posted JSON body into `captured`
    """
    async def fake_fetch(method, url, *, headers=None, json=None):
        if method == "GET" and "/api/v1/workflows" in url:
            return {"data": [{"id": "w-gws", "name": workflow_name, "active": True}]}
        if method == "POST":
            if captured is not None:
                captured.update(json or {})
            return {"executionId": "exec-1"}
        return {}
    return fake_fetch


def _make_error_fetch():
    """Returns a fetch that raises on every call (simulates n8n down)."""
    async def fake_fetch(method, url, *, headers=None, json=None):
        raise ConnectionError("n8n unreachable")
    return fake_fetch


# ---------------------------------------------------------------------------
# Cycle 1 — gmail_send: correct workflow name + payload fields
# ---------------------------------------------------------------------------

class TestGmailSend:
    @pytest.mark.asyncio
    async def test_gmail_send_calls_correct_workflow(self):
        """gmail_send triggers the 'Felix Gmail Send' n8n workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Gmail Send", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("gmail_send", {
            "to": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
        })

        assert not result.is_error

    @pytest.mark.asyncio
    async def test_gmail_send_payload_fields(self):
        """gmail_send passes to/subject/body in the workflow trigger payload."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Gmail Send", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("gmail_send", {
            "to": "bob@example.com",
            "subject": "Test subject",
            "body": "Test body text",
        })

        assert captured.get("to") == "bob@example.com"
        assert captured.get("subject") == "Test subject"
        assert captured.get("body") == "Test body text"

    @pytest.mark.asyncio
    async def test_gmail_send_missing_to_returns_error(self):
        """gmail_send returns an error when 'to' is missing."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Gmail Send"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("gmail_send", {"subject": "Hi", "body": "body"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 2 — gmail_search: correct workflow + query payload
# ---------------------------------------------------------------------------

class TestGmailSearch:
    @pytest.mark.asyncio
    async def test_gmail_search_calls_correct_workflow(self):
        """gmail_search triggers the 'Felix Gmail Search' n8n workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Gmail Search"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("gmail_search", {"query": "from:john this week"})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_gmail_search_passes_query_in_payload(self):
        """gmail_search forwards the 'query' string to the workflow payload."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Gmail Search", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("gmail_search", {"query": "from:john is:unread"})
        assert captured.get("query") == "from:john is:unread"

    @pytest.mark.asyncio
    async def test_gmail_search_missing_query_returns_error(self):
        """gmail_search returns an error when 'query' is missing."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Gmail Search"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("gmail_search", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — calendar_create_event: correct workflow + title/start/end
# ---------------------------------------------------------------------------

class TestCalendarCreateEvent:
    @pytest.mark.asyncio
    async def test_calendar_create_event_calls_correct_workflow(self):
        """calendar_create_event triggers 'Felix Calendar Create'."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Calendar Create"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("calendar_create_event", {
            "title": "Team standup",
            "start": "2026-05-10T10:00:00",
            "end": "2026-05-10T10:30:00",
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_calendar_create_event_payload_fields(self):
        """calendar_create_event forwards title/start/end to the workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Calendar Create", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("calendar_create_event", {
            "title": "Sprint review",
            "start": "2026-05-12T14:00:00",
            "end": "2026-05-12T15:00:00",
            "attendees": ["alice@example.com", "bob@example.com"],
        })

        assert captured.get("title") == "Sprint review"
        assert captured.get("start") == "2026-05-12T14:00:00"
        assert captured.get("end") == "2026-05-12T15:00:00"
        assert captured.get("attendees") == ["alice@example.com", "bob@example.com"]

    @pytest.mark.asyncio
    async def test_calendar_create_event_missing_title_returns_error(self):
        """calendar_create_event returns an error when 'title' is missing."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Calendar Create"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("calendar_create_event", {
            "start": "2026-05-12T14:00:00",
            "end": "2026-05-12T15:00:00",
        })
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — calendar_list_events: correct workflow + date range payload
# ---------------------------------------------------------------------------

class TestCalendarListEvents:
    @pytest.mark.asyncio
    async def test_calendar_list_events_calls_correct_workflow(self):
        """calendar_list_events triggers 'Felix Calendar List'."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Calendar List"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("calendar_list_events", {})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_calendar_list_events_passes_date_range(self):
        """calendar_list_events forwards from/to date range to the workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Calendar List", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("calendar_list_events", {
            "from": "2026-05-01",
            "to": "2026-05-07",
        })

        assert captured.get("from") == "2026-05-01"
        assert captured.get("to") == "2026-05-07"


# ---------------------------------------------------------------------------
# Cycle 5 — drive_list_files: correct workflow + folder/query payload
# ---------------------------------------------------------------------------

class TestDriveListFiles:
    @pytest.mark.asyncio
    async def test_drive_list_files_calls_correct_workflow(self):
        """drive_list_files triggers 'Felix Drive List Files'."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Drive List Files"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("drive_list_files", {})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_drive_list_files_passes_query_and_folder(self):
        """drive_list_files forwards query and folder_id to the workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Drive List Files", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("drive_list_files", {
            "query": "name contains 'report'",
            "folder_id": "folder-abc",
        })

        assert captured.get("query") == "name contains 'report'"
        assert captured.get("folder_id") == "folder-abc"


# ---------------------------------------------------------------------------
# Cycle 6 — drive_upload_file: correct workflow + filename/content/folder
# ---------------------------------------------------------------------------

class TestDriveUploadFile:
    @pytest.mark.asyncio
    async def test_drive_upload_calls_correct_workflow(self):
        """drive_upload_file triggers 'Felix Drive Upload'."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Drive Upload"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("drive_upload_file", {
            "filename": "notes.txt",
            "content": "Hello world",
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_drive_upload_payload_fields(self):
        """drive_upload_file forwards filename/content/folder_id to the workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Drive Upload", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("drive_upload_file", {
            "filename": "report.csv",
            "content": "a,b,c",
            "folder_id": "folder-xyz",
        })

        assert captured.get("filename") == "report.csv"
        assert captured.get("content") == "a,b,c"
        assert captured.get("folder_id") == "folder-xyz"

    @pytest.mark.asyncio
    async def test_drive_upload_missing_filename_returns_error(self):
        """drive_upload_file returns an error when 'filename' is missing."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Drive Upload"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("drive_upload_file", {"content": "data"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 7 — sheets_read_range: correct workflow + spreadsheet/range payload
# ---------------------------------------------------------------------------

class TestSheetsReadRange:
    @pytest.mark.asyncio
    async def test_sheets_read_calls_correct_workflow(self):
        """sheets_read_range triggers 'Felix Sheets Read'."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Sheets Read"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "sheet-123",
            "range": "Sheet1!A1:D10",
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_sheets_read_payload_fields(self):
        """sheets_read_range forwards spreadsheet_id and range to the workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Sheets Read", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "sheet-abc",
            "range": "Sales!B2:F100",
        })

        assert captured.get("spreadsheet_id") == "sheet-abc"
        assert captured.get("range") == "Sales!B2:F100"

    @pytest.mark.asyncio
    async def test_sheets_read_missing_spreadsheet_id_returns_error(self):
        """sheets_read_range returns an error when 'spreadsheet_id' is missing."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Sheets Read"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("sheets_read_range", {"range": "A1:B2"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 — sheets_write_range: correct workflow + spreadsheet/range/data
# ---------------------------------------------------------------------------

class TestSheetsWriteRange:
    @pytest.mark.asyncio
    async def test_sheets_write_calls_correct_workflow(self):
        """sheets_write_range triggers 'Felix Sheets Write'."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Sheets Write"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "sheet-123",
            "range": "Sheet1!A1",
            "data": [["Name", "Score"], ["Alice", "95"]],
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_sheets_write_payload_fields(self):
        """sheets_write_range forwards spreadsheet_id/range/data to the workflow."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        captured = {}
        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Sheets Write", captured=captured))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "sheet-xyz",
            "range": "Results!C3",
            "data": [["a", "b"], ["c", "d"]],
        })

        assert captured.get("spreadsheet_id") == "sheet-xyz"
        assert captured.get("range") == "Results!C3"
        assert captured.get("data") == [["a", "b"], ["c", "d"]]

    @pytest.mark.asyncio
    async def test_sheets_write_missing_data_returns_error(self):
        """sheets_write_range returns an error when 'data' is missing."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Sheets Write"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "sheet-123",
            "range": "A1",
        })
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 9 — n8n failure propagates as error ToolResult
# ---------------------------------------------------------------------------

class TestN8nFailurePropagation:
    @pytest.mark.asyncio
    async def test_n8n_down_returns_error(self):
        """When the underlying n8n call fails, the tool returns is_error=True."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_error_fetch())
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("gmail_send", {
            "to": "x@example.com",
            "subject": "Hi",
            "body": "body",
        })
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 10 — unknown tool name returns error
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """call_tool with an unknown tool name returns is_error=True."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import GoogleWorkspacePlugin

        n8n = N8nPlugin(fetch_fn=_make_fetch("Felix Gmail Send"))
        plugin = GoogleWorkspacePlugin(n8n_plugin=n8n)

        result = await plugin.call_tool("does_not_exist", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 11 — list_tools: 8 tools, correct plugin name, all schemas present
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_eight_tools(self):
        """list_tools returns exactly the 8 Google Workspace tools."""
        from plugins.google_workspace import GoogleWorkspacePlugin

        plugin = GoogleWorkspacePlugin()
        names = {t.name for t in plugin.list_tools()}
        assert names == {
            "gmail_send",
            "gmail_search",
            "calendar_create_event",
            "calendar_list_events",
            "drive_list_files",
            "drive_upload_file",
            "sheets_read_range",
            "sheets_write_range",
        }

    def test_list_tools_all_have_correct_plugin_name(self):
        """Every tool's plugin field is 'google_workspace'."""
        from plugins.google_workspace import GoogleWorkspacePlugin

        plugin = GoogleWorkspacePlugin()
        for tool in plugin.list_tools():
            assert tool.plugin == "google_workspace"

    def test_list_tools_all_have_descriptions(self):
        """Every tool has a non-empty description string."""
        from plugins.google_workspace import GoogleWorkspacePlugin

        plugin = GoogleWorkspacePlugin()
        for tool in plugin.list_tools():
            assert isinstance(tool.description, str) and len(tool.description) > 0

    def test_list_tools_all_have_schemas(self):
        """Every tool has a non-empty schema dict."""
        from plugins.google_workspace import GoogleWorkspacePlugin

        plugin = GoogleWorkspacePlugin()
        for tool in plugin.list_tools():
            assert isinstance(tool.schema, dict) and len(tool.schema) > 0


# ---------------------------------------------------------------------------
# Cycle 12 — create() factory
# ---------------------------------------------------------------------------

class TestCreateFactory:
    def test_create_returns_google_workspace_plugin(self):
        """create() returns a GoogleWorkspacePlugin instance."""
        from plugins.google_workspace import create, GoogleWorkspacePlugin

        plugin = create()
        assert isinstance(plugin, GoogleWorkspacePlugin)

    def test_create_plugin_name_is_google_workspace(self):
        """Plugin name is 'google_workspace' for MCP orchestrator auto-registration."""
        from plugins.google_workspace import create

        assert create().name == "google_workspace"

    def test_create_accepts_n8n_plugin(self):
        """create() accepts an optional n8n_plugin for testing."""
        from plugins.n8n import N8nPlugin
        from plugins.google_workspace import create

        n8n = N8nPlugin()
        plugin = create(n8n_plugin=n8n)
        assert plugin._n8n is n8n

    def test_create_accepts_fetch_fn(self):
        """create() accepts fetch_fn and wires it into the internal N8nPlugin."""
        from plugins.google_workspace import create

        sentinel = object()
        plugin = create(fetch_fn=sentinel)
        assert plugin._n8n._fetch is sentinel
