"""
Google Workspace fallback plugin tests — Issue #21.

TDD vertical slices for GoogleWorkspaceFallbackPlugin:
  - Pass-through when primary (Google/n8n) succeeds
  - Automatic fallback on connectivity failure:
      gmail_send / gmail_search   → IMAP/SMTP (stdlib imaplib + smtplib)
      sheets_read / sheets_write  → Grist HTTP API
      drive_list / drive_upload   → Nextcloud WebDAV
  - Validation errors (missing required args) pass through unchanged
  - Calendar tools have no OSS fallback; primary error is returned
  - IMAP query builder: from:, subject:, is:unread translations
  - Grist range parser: "Sheet1!A1:D10" → table="Sheet1"
  - create() factory for orchestrator auto-registration

All I/O is injectable; no live services required.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_primary_success(tool_name: str, payload: dict | None = None):
    """Primary plugin stub that returns success for the given tool."""
    from cerebral.mcp.orchestrator import ToolResult

    class _Stub:
        name = "google_workspace"

        def list_tools(self):
            from plugins.google_workspace import GoogleWorkspacePlugin
            return GoogleWorkspacePlugin().list_tools()

        async def call_tool(self, name, args):
            if name == tool_name:
                return ToolResult(content=json.dumps(payload or {"ok": True}))
            return ToolResult(content=f"Unknown tool: '{name}'", is_error=True)

    return _Stub()


def _make_primary_fail(msg: str = "Failed to connect: Connection refused"):
    """Primary plugin stub that always returns a connectivity error."""
    from cerebral.mcp.orchestrator import ToolResult

    class _Stub:
        name = "google_workspace"

        def list_tools(self):
            from plugins.google_workspace import GoogleWorkspacePlugin
            return GoogleWorkspacePlugin().list_tools()

        async def call_tool(self, name, args):
            return ToolResult(content=msg, is_error=True)

    return _Stub()


def _make_primary_validation_error(tool_name: str, field: str):
    """Primary plugin stub that returns a validation error (missing required field)."""
    from cerebral.mcp.orchestrator import ToolResult

    class _Stub:
        name = "google_workspace"

        def list_tools(self):
            from plugins.google_workspace import GoogleWorkspacePlugin
            return GoogleWorkspacePlugin().list_tools()

        async def call_tool(self, name, args):
            return ToolResult(
                content=f"'{field}' is required for {tool_name}",
                is_error=True,
            )

    return _Stub()


def _make_smtp_stub(*, raises: Exception | None = None):
    """Returns an smtp_fn that produces a mock SMTP connection."""
    conn = MagicMock()
    if raises:
        conn.sendmail.side_effect = raises
    def smtp_fn(host, port):
        return conn
    smtp_fn._conn = conn
    return smtp_fn


def _make_imap_stub(*, messages: list[tuple[bytes, bytes]] | None = None,
                    raises: Exception | None = None):
    """
    Returns an imap_fn producing a mock IMAP4_SSL connection.

    messages: list of (flags_bytes, rfc822_bytes) tuples returned per fetch call.
    """
    conn = MagicMock()
    if raises:
        conn.search.side_effect = raises
    else:
        conn.search.return_value = ("OK", [b"1"])
        if messages is None:
            raw = (
                b"From: alice@example.com\r\n"
                b"Subject: Test email\r\n"
                b"Date: Mon, 01 Jan 2026 10:00:00 +0000\r\n"
                b"\r\n"
                b"Hello world"
            )
            messages = [(b"1 (RFC822 {100})", raw)]
        conn.fetch.return_value = ("OK", messages)
    def imap_fn(host, port):
        return conn
    imap_fn._conn = conn
    return imap_fn


def _make_grist_fetch(*, raises: Exception | None = None,
                      records: list | None = None):
    """Async fetch stub for Grist."""
    async def fetch(method, url, *, headers=None, json=None):
        if raises:
            raise raises
        if method == "GET":
            return {"records": records or [
                {"id": 1, "fields": {"A": "hello", "B": "world"}},
            ]}
        return {"records": [{"id": 1}]}
    return fetch


def _make_nextcloud_fetch(*, raises: Exception | None = None,
                          files: list | None = None):
    """Async fetch stub for Nextcloud."""
    async def fetch(method, url, *, headers=None, json=None, data=None):
        if raises:
            raise raises
        if method == "PROPFIND":
            return {"files": files or [{"name": "report.txt", "size": 1024}]}
        return {"status": "ok"}
    return fetch


# ===========================================================================
# Cycle 1 — Pass-through: primary succeeds → return primary result
# ===========================================================================

class TestPassThrough:
    @pytest.mark.asyncio
    async def test_gmail_send_passes_through_primary_success(self):
        """When primary gmail_send succeeds, result is returned unchanged."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_success("gmail_send", {"sent": True})
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary)

        result = await plugin.call_tool("gmail_send", {
            "to": "alice@example.com",
            "subject": "Hello",
            "body": "Hi there",
        })

        assert not result.is_error

    @pytest.mark.asyncio
    async def test_sheets_read_passes_through_primary_success(self):
        """When primary sheets_read_range succeeds, result is returned unchanged."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_success("sheets_read_range", {"rows": [["a", "b"]]})
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary)

        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "sheet-1",
            "range": "Sheet1!A1:B2",
        })

        assert not result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_primary_error(self):
        """Unknown tool name → primary error returned, no fallback attempted."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_success("gmail_send")
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary)

        result = await plugin.call_tool("nonexistent_tool", {})
        assert result.is_error


# ===========================================================================
# Cycle 2 — Validation errors bypass fallback
# ===========================================================================

class TestValidationBypass:
    @pytest.mark.asyncio
    async def test_missing_required_field_returns_validation_error(self):
        """Missing required arg → validation error from primary, not SMTP fallback."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_validation_error("gmail_send", "to")
        smtp_fn = _make_smtp_stub()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, smtp_fn=smtp_fn)

        result = await plugin.call_tool("gmail_send", {"subject": "Hi", "body": "body"})

        assert result.is_error
        assert "required" in result.content
        # SMTP must NOT have been called
        smtp_fn._conn.sendmail.assert_not_called()

    @pytest.mark.asyncio
    async def test_sheets_missing_spreadsheet_id_returns_validation_error(self):
        """sheets_read_range missing spreadsheet_id → validation error, not Grist."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_validation_error("sheets_read_range", "spreadsheet_id")
        grist_called = []

        async def grist_fetch(method, url, *, headers=None, json=None):
            grist_called.append(method)
            return {"records": []}

        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, fetch_fn=grist_fetch)

        result = await plugin.call_tool("sheets_read_range", {"range": "A1:B2"})
        assert result.is_error
        assert "required" in result.content
        assert not grist_called


# ===========================================================================
# Cycle 3 — Gmail → SMTP fallback (send)
# ===========================================================================

class TestGmailSmtpFallback:
    @pytest.mark.asyncio
    async def test_gmail_send_falls_back_to_smtp_on_primary_failure(self):
        """gmail_send falls back to SMTP when primary returns a connectivity error."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail("Failed to connect to n8n")
        smtp_fn = _make_smtp_stub()
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary,
            smtp_fn=smtp_fn,
        )

        result = await plugin.call_tool("gmail_send", {
            "to": "bob@example.com",
            "subject": "Test",
            "body": "Hello",
        })

        assert not result.is_error
        smtp_fn._conn.sendmail.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_send_smtp_includes_recipient_in_result(self):
        """gmail_send SMTP fallback result includes the recipient address."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        smtp_fn = _make_smtp_stub()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, smtp_fn=smtp_fn)

        result = await plugin.call_tool("gmail_send", {
            "to": "carol@example.com",
            "subject": "Hi",
            "body": "Body",
        })

        payload = json.loads(result.content)
        assert payload.get("to") == "carol@example.com"

    @pytest.mark.asyncio
    async def test_gmail_send_smtp_failure_returns_error(self):
        """SMTP failure (e.g. connection refused) returns ToolResult(is_error=True)."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        smtp_fn = _make_smtp_stub(raises=OSError("SMTP connection refused"))
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, smtp_fn=smtp_fn)

        result = await plugin.call_tool("gmail_send", {
            "to": "dave@example.com",
            "subject": "Hi",
            "body": "Body",
        })

        assert result.is_error

    @pytest.mark.asyncio
    async def test_gmail_send_smtp_result_includes_subject(self):
        """gmail_send SMTP fallback result includes the subject."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        smtp_fn = _make_smtp_stub()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, smtp_fn=smtp_fn)

        result = await plugin.call_tool("gmail_send", {
            "to": "eve@example.com",
            "subject": "Weekly digest",
            "body": "Here is the digest",
        })

        payload = json.loads(result.content)
        assert payload.get("subject") == "Weekly digest"


# ===========================================================================
# Cycle 4 — Gmail → IMAP fallback (search)
# ===========================================================================

class TestGmailImapFallback:
    @pytest.mark.asyncio
    async def test_gmail_search_falls_back_to_imap_on_primary_failure(self):
        """gmail_search falls back to IMAP when primary returns a connectivity error."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        imap_fn = _make_imap_stub()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, imap_fn=imap_fn)

        result = await plugin.call_tool("gmail_search", {"query": "from:alice"})

        assert not result.is_error
        imap_fn._conn.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_search_imap_result_has_messages_list(self):
        """gmail_search IMAP fallback returns a JSON payload with a 'messages' list."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        imap_fn = _make_imap_stub()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, imap_fn=imap_fn)

        result = await plugin.call_tool("gmail_search", {"query": "is:unread"})
        payload = json.loads(result.content)
        assert "messages" in payload
        assert isinstance(payload["messages"], list)

    @pytest.mark.asyncio
    async def test_gmail_search_message_has_from_and_subject(self):
        """Each message in the IMAP result has 'from' and 'subject' fields."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        imap_fn = _make_imap_stub()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, imap_fn=imap_fn)

        result = await plugin.call_tool("gmail_search", {"query": "from:alice"})
        payload = json.loads(result.content)
        msg = payload["messages"][0]
        assert "from" in msg
        assert "subject" in msg

    @pytest.mark.asyncio
    async def test_gmail_search_imap_failure_returns_error(self):
        """IMAP failure returns ToolResult(is_error=True)."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        imap_fn = _make_imap_stub(raises=OSError("IMAP connection refused"))
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, imap_fn=imap_fn)

        result = await plugin.call_tool("gmail_search", {"query": "subject:report"})
        assert result.is_error


# ===========================================================================
# Cycle 5 — Sheets → Grist fallback (read)
# ===========================================================================

class TestSheetsGristReadFallback:
    @pytest.mark.asyncio
    async def test_sheets_read_falls_back_to_grist_on_primary_failure(self):
        """sheets_read_range falls back to Grist when primary returns a connectivity error."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_grist_fetch()
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary,
            fetch_fn=fetch,
            grist_url="http://localhost:8484",
        )

        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "doc-abc",
            "range": "Sheet1!A1:B2",
        })

        assert not result.is_error

    @pytest.mark.asyncio
    async def test_sheets_read_grist_result_has_rows(self):
        """sheets_read_range Grist fallback returns a payload with 'rows'."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_grist_fetch(records=[{"id": 1, "fields": {"A": "val1", "B": "val2"}}])
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary, fetch_fn=fetch, grist_url="http://localhost:8484"
        )

        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "doc-123",
            "range": "Sheet1!A1:B2",
        })

        payload = json.loads(result.content)
        assert "rows" in payload
        assert len(payload["rows"]) == 1

    @pytest.mark.asyncio
    async def test_sheets_read_grist_failure_returns_error(self):
        """Grist HTTP failure returns ToolResult(is_error=True)."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_grist_fetch(raises=OSError("Grist unreachable"))
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, fetch_fn=fetch)

        result = await plugin.call_tool("sheets_read_range", {
            "spreadsheet_id": "doc-xyz",
            "range": "Sheet1!A1",
        })
        assert result.is_error


# ===========================================================================
# Cycle 6 — Sheets → Grist fallback (write)
# ===========================================================================

class TestSheetsGristWriteFallback:
    @pytest.mark.asyncio
    async def test_sheets_write_falls_back_to_grist_on_primary_failure(self):
        """sheets_write_range falls back to Grist when primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_grist_fetch()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, fetch_fn=fetch)

        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "doc-abc",
            "range": "Sheet1!A1",
            "data": [["Name", "Score"], ["Alice", "95"]],
        })

        assert not result.is_error

    @pytest.mark.asyncio
    async def test_sheets_write_result_reports_written_count(self):
        """sheets_write_range Grist result includes 'written' count."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_grist_fetch()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, fetch_fn=fetch)

        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "doc-1",
            "range": "A1",
            "data": [["a", "b"], ["c", "d"], ["e", "f"]],
        })

        payload = json.loads(result.content)
        assert payload.get("written") == 3

    @pytest.mark.asyncio
    async def test_sheets_write_grist_failure_returns_error(self):
        """Grist write failure returns ToolResult(is_error=True)."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_grist_fetch(raises=OSError("Grist down"))
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, fetch_fn=fetch)

        result = await plugin.call_tool("sheets_write_range", {
            "spreadsheet_id": "doc-2",
            "range": "A1",
            "data": [["x"]],
        })
        assert result.is_error


# ===========================================================================
# Cycle 7 — Drive → Nextcloud fallback (list)
# ===========================================================================

class TestDriveNextcloudListFallback:
    @pytest.mark.asyncio
    async def test_drive_list_falls_back_to_nextcloud_on_primary_failure(self):
        """drive_list_files falls back to Nextcloud when primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_nextcloud_fetch()
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary,
            fetch_fn=fetch,
            nextcloud_url="http://nextcloud.local",
        )

        result = await plugin.call_tool("drive_list_files", {})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_drive_list_result_has_files(self):
        """drive_list_files Nextcloud result contains a 'files' list."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_nextcloud_fetch(files=[
            {"name": "readme.txt", "size": 512},
            {"name": "data.csv", "size": 2048},
        ])
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary, fetch_fn=fetch, nextcloud_url="http://nc.local"
        )

        result = await plugin.call_tool("drive_list_files", {})
        payload = json.loads(result.content)
        assert len(payload["files"]) == 2

    @pytest.mark.asyncio
    async def test_drive_list_returns_error_when_nextcloud_not_configured(self):
        """drive_list_files returns a clear error when nextcloud_url is None."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, nextcloud_url=None)

        result = await plugin.call_tool("drive_list_files", {})
        assert result.is_error
        assert "Nextcloud" in result.content or "not configured" in result.content

    @pytest.mark.asyncio
    async def test_drive_list_nextcloud_failure_returns_error(self):
        """Nextcloud PROPFIND failure returns ToolResult(is_error=True)."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_nextcloud_fetch(raises=OSError("WebDAV unreachable"))
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary, fetch_fn=fetch, nextcloud_url="http://nc.local"
        )

        result = await plugin.call_tool("drive_list_files", {})
        assert result.is_error


# ===========================================================================
# Cycle 8 — Drive → Nextcloud fallback (upload)
# ===========================================================================

class TestDriveNextcloudUploadFallback:
    @pytest.mark.asyncio
    async def test_drive_upload_falls_back_to_nextcloud_on_primary_failure(self):
        """drive_upload_file falls back to Nextcloud when primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_nextcloud_fetch()
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary, fetch_fn=fetch, nextcloud_url="http://nc.local"
        )

        result = await plugin.call_tool("drive_upload_file", {
            "filename": "notes.txt",
            "content": "Hello world",
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_drive_upload_result_includes_filename(self):
        """drive_upload_file Nextcloud result includes the uploaded filename."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        fetch = _make_nextcloud_fetch()
        plugin = GoogleWorkspaceFallbackPlugin(
            primary=primary, fetch_fn=fetch, nextcloud_url="http://nc.local"
        )

        result = await plugin.call_tool("drive_upload_file", {
            "filename": "report.csv",
            "content": "a,b,c",
        })
        payload = json.loads(result.content)
        assert payload.get("filename") == "report.csv"

    @pytest.mark.asyncio
    async def test_drive_upload_returns_error_when_nextcloud_not_configured(self):
        """drive_upload_file returns a clear error when nextcloud_url is None."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, nextcloud_url=None)

        result = await plugin.call_tool("drive_upload_file", {
            "filename": "test.txt",
            "content": "data",
        })
        assert result.is_error
        assert "Nextcloud" in result.content or "not configured" in result.content


# ===========================================================================
# Cycle 9 — Calendar: no OSS fallback (pass through primary error)
# ===========================================================================

class TestCalendarNoFallback:
    @pytest.mark.asyncio
    async def test_calendar_create_returns_primary_error_on_failure(self):
        """calendar_create_event has no OSS fallback; primary error is returned."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        err_msg = "Failed to connect to n8n"
        primary = _make_primary_fail(err_msg)
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary)

        result = await plugin.call_tool("calendar_create_event", {
            "title": "Standup",
            "start": "2026-05-10T10:00:00",
        })
        assert result.is_error
        assert result.content == err_msg

    @pytest.mark.asyncio
    async def test_calendar_list_returns_primary_error_on_failure(self):
        """calendar_list_events has no OSS fallback; primary error is returned."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        err_msg = "n8n unreachable"
        primary = _make_primary_fail(err_msg)
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary)

        result = await plugin.call_tool("calendar_list_events", {})
        assert result.is_error
        assert result.content == err_msg


# ===========================================================================
# Cycle 10 — IMAP query builder
# ===========================================================================

class TestImapQueryBuilder:
    def test_from_prefix_translates_to_imap_from(self):
        """Gmail 'from:alice' query maps to IMAP FROM criterion."""
        from plugins.google_workspace_fallback import _parse_imap_query

        criteria = _parse_imap_query("from:alice@example.com")
        assert "FROM" in criteria
        idx = criteria.index("FROM")
        assert "alice@example.com" in criteria[idx + 1]

    def test_subject_prefix_translates_to_imap_subject(self):
        """Gmail 'subject:report' query maps to IMAP SUBJECT criterion."""
        from plugins.google_workspace_fallback import _parse_imap_query

        criteria = _parse_imap_query("subject:weekly report")
        assert "SUBJECT" in criteria

    def test_is_unread_translates_to_imap_unseen(self):
        """Gmail 'is:unread' maps to IMAP UNSEEN criterion."""
        from plugins.google_workspace_fallback import _parse_imap_query

        criteria = _parse_imap_query("is:unread")
        assert "UNSEEN" in criteria

    def test_is_read_translates_to_imap_seen(self):
        """Gmail 'is:read' maps to IMAP SEEN criterion."""
        from plugins.google_workspace_fallback import _parse_imap_query

        criteria = _parse_imap_query("is:read")
        assert "SEEN" in criteria

    def test_unrecognised_query_falls_back_to_text_search(self):
        """Unrecognised Gmail query falls back to IMAP TEXT search."""
        from plugins.google_workspace_fallback import _parse_imap_query

        criteria = _parse_imap_query("some random phrase")
        assert "TEXT" in criteria

    def test_empty_query_returns_all(self):
        """Empty query returns ['ALL']."""
        from plugins.google_workspace_fallback import _parse_imap_query

        criteria = _parse_imap_query("")
        assert criteria == ["ALL"]


# ===========================================================================
# Cycle 11 — Grist range parser
# ===========================================================================

class TestGristRangeParser:
    def test_sheet_exclamation_range_extracts_table_name(self):
        """'Sheet1!A1:D10' → table_id='Sheet1'."""
        from plugins.google_workspace_fallback import _parse_grist_range

        table_id, cell_range = _parse_grist_range("Sheet1!A1:D10")
        assert table_id == "Sheet1"

    def test_sheet_exclamation_range_extracts_cell_range(self):
        """'Sheet1!A1:D10' → cell_range='A1:D10'."""
        from plugins.google_workspace_fallback import _parse_grist_range

        table_id, cell_range = _parse_grist_range("Sheet1!A1:D10")
        assert cell_range == "A1:D10"

    def test_no_sheet_prefix_defaults_to_sheet1(self):
        """'A1:B2' (no sheet prefix) → default table_id='Sheet1'."""
        from plugins.google_workspace_fallback import _parse_grist_range

        table_id, cell_range = _parse_grist_range("A1:B2")
        assert table_id == "Sheet1"

    def test_named_sheet_preserved(self):
        """'Sales!B2:F100' → table_id='Sales'."""
        from plugins.google_workspace_fallback import _parse_grist_range

        table_id, _ = _parse_grist_range("Sales!B2:F100")
        assert table_id == "Sales"


# ===========================================================================
# Cycle 12 — list_tools: same 8 tools as primary
# ===========================================================================

class TestListTools:
    def test_list_tools_returns_eight_tools(self):
        """GoogleWorkspaceFallbackPlugin.list_tools() returns exactly 8 tools."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin, _StubPrimaryPlugin

        plugin = GoogleWorkspaceFallbackPlugin(primary=_StubPrimaryPlugin())
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

    def test_plugin_name_is_google_workspace(self):
        """Plugin name must be 'google_workspace' for MCP orchestrator compatibility."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin()
        assert plugin.name == "google_workspace"


# ===========================================================================
# Cycle 13 — create() factory
# ===========================================================================

class TestCreateFactory:
    def test_create_returns_fallback_plugin_instance(self):
        """create() returns a GoogleWorkspaceFallbackPlugin."""
        from plugins.google_workspace_fallback import create, GoogleWorkspaceFallbackPlugin

        plugin = create()
        assert isinstance(plugin, GoogleWorkspaceFallbackPlugin)

    def test_create_plugin_name_is_google_workspace(self):
        """create() result has name='google_workspace' for auto-registration."""
        from plugins.google_workspace_fallback import create

        assert create().name == "google_workspace"

    def test_create_accepts_primary_plugin(self):
        """create() accepts an optional primary plugin argument."""
        from plugins.google_workspace_fallback import create, _StubPrimaryPlugin

        gws = _StubPrimaryPlugin()
        plugin = create(primary=gws)
        assert plugin._primary is gws

    def test_create_accepts_injectable_fetch_fn(self):
        """create() accepts fetch_fn and wires it to Grist and Nextcloud backends."""
        from plugins.google_workspace_fallback import create

        sentinel = object()
        plugin = create(fetch_fn=sentinel)
        assert plugin._grist._fetch is sentinel
        assert plugin._nextcloud._fetch is sentinel
