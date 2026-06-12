"""
Google Workspace fallback plugin tests — Issue #21 + Issue #232.

TDD vertical slices for GoogleWorkspaceFallbackPlugin:
  - Pass-through when primary (Google/n8n) succeeds
  - Automatic fallback on connectivity failure:
      gmail_send / gmail_search   → IMAP/SMTP (stdlib imaplib + smtplib)
      sheets_read / sheets_write  → Grist HTTP API
      drive_list / drive_upload   → Nextcloud WebDAV
      calendar_* (4 tools)        → local SQLite (Issue #232)
  - Validation errors (missing required args) pass through unchanged
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
# Cycle 9 — Calendar → SQLite fallback (Issue #232)
# ===========================================================================

class TestCalendarSQLiteFallback:
    @pytest.mark.asyncio
    async def test_calendar_create_falls_back_to_sqlite_on_primary_failure(self):
        """calendar_create_event falls back to SQLite when primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail("Failed to connect to Google Calendar")
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, db_path=":memory:")

        result = await plugin.call_tool("calendar_create_event", {
            "title": "Standup",
            "start": "2026-05-10T10:00:00",
        })
        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["title"] == "Standup"

    @pytest.mark.asyncio
    async def test_calendar_list_falls_back_to_sqlite_on_primary_failure(self):
        """calendar_list_events falls back to SQLite when primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail("Google Calendar unreachable")
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, db_path=":memory:")

        result = await plugin.call_tool("calendar_list_events", {})
        assert not result.is_error
        payload = json.loads(result.content)
        assert "events" in payload

    @pytest.mark.asyncio
    async def test_calendar_create_list_roundtrip(self):
        """Create an event then list it back — proves SQLite persistence."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, db_path=":memory:")

        await plugin.call_tool("calendar_create_event", {
            "title": "Weekly sync",
            "start": "2026-06-01T09:00:00",
            "end": "2026-06-01T10:00:00",
        })
        result = await plugin.call_tool("calendar_list_events", {})
        payload = json.loads(result.content)
        titles = [e["title"] for e in payload["events"]]
        assert "Weekly sync" in titles

    @pytest.mark.asyncio
    async def test_calendar_update_falls_back_to_sqlite_on_primary_failure(self):
        """calendar_update_event falls back to SQLite when primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, db_path=":memory:")

        create_result = await plugin.call_tool("calendar_create_event", {
            "title": "Old title",
            "start": "2026-06-10T08:00:00",
        })
        event_id = json.loads(create_result.content)["id"]

        update_result = await plugin.call_tool("calendar_update_event", {
            "id": event_id,
            "title": "New title",
        })
        assert not update_result.is_error
        payload = json.loads(update_result.content)
        assert "title" in payload["updated"]

    @pytest.mark.asyncio
    async def test_calendar_delete_falls_back_to_sqlite_on_primary_failure(self):
        """calendar_delete_event falls back to SQLite when primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, db_path=":memory:")

        create_result = await plugin.call_tool("calendar_create_event", {
            "title": "Doomed meeting",
            "start": "2026-06-15T14:00:00",
        })
        event_id = json.loads(create_result.content)["id"]

        delete_result = await plugin.call_tool("calendar_delete_event", {"id": event_id})
        assert not delete_result.is_error
        payload = json.loads(delete_result.content)
        assert payload.get("deleted") is True

    @pytest.mark.asyncio
    async def test_calendar_list_filtered_by_date_range(self):
        """calendar_list_events respects from/to date filters via SQLite."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        primary = _make_primary_fail()
        plugin = GoogleWorkspaceFallbackPlugin(primary=primary, db_path=":memory:")

        for title, start in [
            ("Early event", "2026-01-01T09:00:00"),
            ("Mid event", "2026-06-01T09:00:00"),
            ("Late event", "2026-12-01T09:00:00"),
        ]:
            await plugin.call_tool("calendar_create_event", {"title": title, "start": start})

        result = await plugin.call_tool("calendar_list_events", {
            "from": "2026-05-01T00:00:00",
            "to": "2026-07-01T00:00:00",
        })
        payload = json.loads(result.content)
        titles = [e["title"] for e in payload["events"]]
        assert "Mid event" in titles
        assert "Early event" not in titles
        assert "Late event" not in titles


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
    def test_list_tools_returns_ten_workspace_tools(self):
        """With docs_primary=None and maps_primary=None, list_tools() returns exactly 10 workspace tools."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin, _StubPrimaryPlugin

        plugin = GoogleWorkspaceFallbackPlugin(primary=_StubPrimaryPlugin(), docs_primary=None, maps_primary=None)
        names = {t.name for t in plugin.list_tools()}
        assert names == {
            "gmail_send",
            "gmail_search",
            "calendar_create_event",
            "calendar_list_events",
            "calendar_update_event",
            "calendar_delete_event",
            "drive_list_files",
            "drive_upload_file",
            "sheets_read_range",
            "sheets_write_range",
        }

    def test_list_tools_includes_docs_tools_when_docs_primary_set(self):
        """With a docs_primary, list_tools() includes all docs_* tools."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin, _StubPrimaryPlugin
        from plugins.google_docs import GoogleDocsPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_StubPrimaryPlugin(), docs_primary=GoogleDocsPlugin()
        )
        names = {t.name for t in plugin.list_tools()}
        assert "docs_create" in names
        assert "docs_read" in names
        assert "docs_append" in names

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


# ===========================================================================
# Cycle 14 — DocsODTFallback unit tests (Issue #233)
# ===========================================================================

class TestDocsODTFallback:
    def test_create_returns_doc_id_and_title(self, tmp_path):
        """docs_create returns a payload with 'id' and 'title'."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        result = fallback.create(title="My Report", body="Hello world")
        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["title"] == "My Report"
        assert "id" in payload

    def test_create_writes_odt_file_to_docs_dir(self, tmp_path):
        """docs_create writes a .odt file to the configured docs directory."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        result = fallback.create(title="Test Doc", body="content")
        doc_id = json.loads(result.content)["id"]
        assert (tmp_path / (doc_id + ".odt")).exists()

    def test_read_returns_body_text(self, tmp_path):
        """docs_read returns the body text that was written at create time."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        doc_id = json.loads(fallback.create(title="Read Test", body="Sample text").content)["id"]

        result = fallback.read(doc_id)
        assert not result.is_error
        assert "Sample text" in json.loads(result.content)["body"]

    def test_read_returns_title_from_sidecar(self, tmp_path):
        """docs_read returns the original title stored in the .meta.json sidecar."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        doc_id = json.loads(fallback.create(title="Stored Title", body="body").content)["id"]

        payload = json.loads(fallback.read(doc_id).content)
        assert payload["title"] == "Stored Title"

    def test_read_missing_doc_returns_error(self, tmp_path):
        """docs_read on a non-existent document_id returns is_error=True."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        result = fallback.read("nonexistent-id")
        assert result.is_error

    def test_append_adds_text_to_existing_doc(self, tmp_path):
        """docs_append adds new text that is visible after a subsequent read."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        doc_id = json.loads(fallback.create(title="Append Test", body="First line").content)["id"]

        fallback.append(doc_id, "Second line")
        body = json.loads(fallback.read(doc_id).content)["body"]
        assert "First line" in body
        assert "Second line" in body

    def test_append_missing_doc_returns_error(self, tmp_path):
        """docs_append on a non-existent document_id returns is_error=True."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        result = fallback.append("nonexistent-id", "some text")
        assert result.is_error

    def test_append_result_includes_appended_flag(self, tmp_path):
        """docs_append result payload has appended=True on success."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        doc_id = json.loads(fallback.create(title="Flag Test", body="body").content)["id"]

        result = fallback.append(doc_id, "more text")
        assert json.loads(result.content)["appended"] is True

    def test_create_read_append_roundtrip(self, tmp_path):
        """Full create -> append -> read roundtrip preserves all content."""
        from plugins.google_workspace_fallback import DocsODTFallback

        fallback = DocsODTFallback(docs_dir=str(tmp_path))
        doc_id = json.loads(fallback.create(title="Roundtrip", body="Initial").content)["id"]
        fallback.append(doc_id, "Added later")
        body = json.loads(fallback.read(doc_id).content)["body"]
        assert "Initial" in body
        assert "Added later" in body


# ===========================================================================
# Cycle 15 — Docs → ODF fallback via GoogleWorkspaceFallbackPlugin (Issue #233)
# ===========================================================================

def _make_docs_primary_fail(msg: str = "Google Docs API unreachable"):
    """Docs primary stub that always returns a connectivity error."""
    from cerebral.mcp.orchestrator import ToolResult

    class _Stub:
        async def call_tool(self, name, args):
            return ToolResult(content=msg, is_error=True)

    return _Stub()


class TestDocsODTFallbackIntegration:
    @pytest.mark.asyncio
    async def test_docs_create_falls_back_to_odf_on_primary_failure(self, tmp_path):
        """docs_create routes to ODF fallback when docs primary returns a connectivity error."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            docs_primary=_make_docs_primary_fail(),
            docs_dir=str(tmp_path),
        )
        result = await plugin.call_tool("docs_create", {"title": "Offline Doc"})
        assert not result.is_error
        assert json.loads(result.content)["title"] == "Offline Doc"

    @pytest.mark.asyncio
    async def test_docs_read_falls_back_to_odf_on_primary_failure(self, tmp_path):
        """docs_read routes to ODF fallback when docs primary returns a connectivity error."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            docs_primary=_make_docs_primary_fail(),
            docs_dir=str(tmp_path),
        )
        doc_id = json.loads(
            (await plugin.call_tool("docs_create", {"title": "Read Test"})).content
        )["id"]
        result = await plugin.call_tool("docs_read", {"document_id": doc_id})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_docs_append_falls_back_to_odf_on_primary_failure(self, tmp_path):
        """docs_append routes to ODF fallback when docs primary returns a connectivity error."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            docs_primary=_make_docs_primary_fail(),
            docs_dir=str(tmp_path),
        )
        doc_id = json.loads(
            (await plugin.call_tool("docs_create", {"title": "Append Test"})).content
        )["id"]
        result = await plugin.call_tool("docs_append", {
            "document_id": doc_id, "text": "Extra content",
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_docs_create_read_append_roundtrip_via_plugin(self, tmp_path):
        """Full create → append → read roundtrip through the fallback plugin."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            docs_primary=_make_docs_primary_fail(),
            docs_dir=str(tmp_path),
        )
        doc_id = json.loads(
            (await plugin.call_tool("docs_create", {"title": "My Doc"})).content
        )["id"]
        await plugin.call_tool("docs_append", {"document_id": doc_id, "text": "Appended text"})
        body = json.loads(
            (await plugin.call_tool("docs_read", {"document_id": doc_id})).content
        )["body"]
        assert "Appended text" in body

    @pytest.mark.asyncio
    async def test_docs_create_directly_when_no_docs_primary(self, tmp_path):
        """When docs_primary=None, docs_create routes directly to ODF fallback."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            docs_primary=None,
            docs_dir=str(tmp_path),
        )
        result = await plugin.call_tool("docs_create", {"title": "Direct ODF"})
        assert not result.is_error
        assert json.loads(result.content)["title"] == "Direct ODF"

    @pytest.mark.asyncio
    async def test_workspace_tools_still_work_alongside_docs_fallback(self, tmp_path):
        """Adding docs fallback does not break existing workspace (calendar) fallback."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail("Google Calendar unreachable"),
            docs_primary=_make_docs_primary_fail(),
            docs_dir=str(tmp_path),
            db_path=":memory:",
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "Standup", "start": "2026-07-01T09:00:00",
        })
        assert not result.is_error


# ===========================================================================
# Nominatim fetch stubs
# ===========================================================================

_NOMINATIM_GEOCODE_SAMPLE = [
    {
        "place_id": 12345,
        "display_name": "10 Downing Street, Westminster, London, UK",
        "lat": "51.5034070",
        "lon": "-0.1276248",
        "type": "house",
    }
]

_NOMINATIM_REVERSE_SAMPLE = {
    "place_id": 12345,
    "display_name": "10 Downing Street, Westminster, London, UK",
    "lat": "51.5034070",
    "lon": "-0.1276248",
    "type": "house",
}


def _make_nominatim_geocode_fetch(*, raises=None, results=None):
    """Async fetch stub returning a Nominatim /search-style response."""
    captured = {}

    async def fetch(method, url, *, headers=None, params=None, json=None):
        captured["headers"] = headers or {}
        captured["params"] = params or {}
        captured["url"] = url
        if raises:
            raise raises
        return results if results is not None else _NOMINATIM_GEOCODE_SAMPLE

    fetch._captured = captured
    return fetch


def _make_nominatim_reverse_fetch(*, raises=None, result=None, error=None):
    """Async fetch stub returning a Nominatim /reverse-style response."""
    async def fetch(method, url, *, headers=None, params=None, json=None):
        if raises:
            raise raises
        if error:
            return {"error": error}
        return result if result is not None else _NOMINATIM_REVERSE_SAMPLE

    return fetch


def _make_maps_primary_fail(msg: str = "Google Maps API unreachable"):
    """Maps primary stub that always returns a connectivity error."""
    from cerebral.mcp.orchestrator import ToolResult

    class _Stub:
        def list_tools(self):
            from plugins.google_maps import GoogleMapsPlugin
            return GoogleMapsPlugin().list_tools()

        async def call_tool(self, name, args):
            return ToolResult(content=msg, is_error=True)

    return _Stub()


def _make_maps_primary_success(tool_name: str, payload: dict | None = None):
    """Maps primary stub that returns success for the given tool."""
    from cerebral.mcp.orchestrator import ToolResult

    class _Stub:
        def list_tools(self):
            from plugins.google_maps import GoogleMapsPlugin
            return GoogleMapsPlugin().list_tools()

        async def call_tool(self, name, args):
            if name == tool_name:
                return ToolResult(content=json.dumps(payload or {"status": "OK", "results": []}))
            return ToolResult(content=f"Unknown tool: '{name}'", is_error=True)

    return _Stub()


def _make_maps_validation_error(tool_name: str, field: str):
    """Maps primary stub that returns a validation error."""
    from cerebral.mcp.orchestrator import ToolResult

    class _Stub:
        def list_tools(self):
            from plugins.google_maps import GoogleMapsPlugin
            return GoogleMapsPlugin().list_tools()

        async def call_tool(self, name, args):
            return ToolResult(
                content=f"'{field}' is required for {tool_name}",
                is_error=True,
            )

    return _Stub()


# ===========================================================================
# Cycle 16 -- NominatimFallback unit tests (Issue #234)
# ===========================================================================

class TestNominatimFallback:
    @pytest.mark.asyncio
    async def test_geocode_returns_lat_lng(self):
        """geocode() result payload includes lat and lng for the first result."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_geocode_fetch()
        fallback = NominatimFallback(fetch_fn=fetch)
        result = await fallback.geocode("10 Downing Street, London")
        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["results"][0]["lat"] == pytest.approx(51.503407, abs=1e-4)
        assert payload["results"][0]["lng"] == pytest.approx(-0.1276248, abs=1e-4)

    @pytest.mark.asyncio
    async def test_geocode_result_has_formatted_address(self):
        """geocode() result includes formatted_address from display_name."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_geocode_fetch()
        fallback = NominatimFallback(fetch_fn=fetch)
        result = await fallback.geocode("10 Downing Street, London")
        payload = json.loads(result.content)
        assert "formatted_address" in payload["results"][0]
        assert "London" in payload["results"][0]["formatted_address"]

    @pytest.mark.asyncio
    async def test_geocode_network_failure_returns_error(self):
        """geocode() returns is_error=True when the HTTP call raises."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_geocode_fetch(raises=OSError("Nominatim unreachable"))
        fallback = NominatimFallback(fetch_fn=fetch)
        result = await fallback.geocode("anywhere")
        assert result.is_error

    @pytest.mark.asyncio
    async def test_geocode_sends_user_agent_header(self):
        """geocode() sends the configured User-Agent header on every request."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_geocode_fetch()
        fallback = NominatimFallback(fetch_fn=fetch, user_agent="TestAgent/1.0")
        await fallback.geocode("anywhere")
        assert fetch._captured["headers"].get("User-Agent") == "TestAgent/1.0"

    @pytest.mark.asyncio
    async def test_geocode_uses_configurable_endpoint(self):
        """geocode() calls the configured base_url, not the hardcoded public URL."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_geocode_fetch()
        fallback = NominatimFallback(fetch_fn=fetch, base_url="http://nominatim.local")
        await fallback.geocode("anywhere")
        assert fetch._captured["url"].startswith("http://nominatim.local")

    @pytest.mark.asyncio
    async def test_reverse_geocode_returns_formatted_address(self):
        """reverse_geocode() result includes formatted_address."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_reverse_fetch()
        fallback = NominatimFallback(fetch_fn=fetch)
        result = await fallback.reverse_geocode(51.5034070, -0.1276248)
        assert not result.is_error
        payload = json.loads(result.content)
        assert "formatted_address" in payload["results"][0]

    @pytest.mark.asyncio
    async def test_reverse_geocode_result_matches_coordinates(self):
        """reverse_geocode() result lat/lng match the parsed Nominatim response."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_reverse_fetch()
        fallback = NominatimFallback(fetch_fn=fetch)
        result = await fallback.reverse_geocode(51.5034070, -0.1276248)
        payload = json.loads(result.content)
        assert payload["results"][0]["lat"] == pytest.approx(51.503407, abs=1e-4)

    @pytest.mark.asyncio
    async def test_reverse_geocode_network_failure_returns_error(self):
        """reverse_geocode() returns is_error=True when the HTTP call raises."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_reverse_fetch(raises=OSError("connection refused"))
        fallback = NominatimFallback(fetch_fn=fetch)
        result = await fallback.reverse_geocode(0.0, 0.0)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_reverse_geocode_nominatim_error_response_returns_error(self):
        """reverse_geocode() surfaces Nominatim 'error' field as is_error=True."""
        from plugins.google_workspace_fallback import NominatimFallback

        fetch = _make_nominatim_reverse_fetch(error="Unable to geocode")
        fallback = NominatimFallback(fetch_fn=fetch)
        result = await fallback.reverse_geocode(0.0, 0.0)
        assert result.is_error
        assert "Unable to geocode" in result.content


# ===========================================================================
# Cycle 17 -- GoogleWorkspaceFallbackPlugin maps integration (Issue #234)
# ===========================================================================

class TestMapsFallbackIntegration:
    @pytest.mark.asyncio
    async def test_maps_geocode_falls_back_to_nominatim_on_primary_failure(self):
        """maps_geocode routes to Nominatim when the maps primary returns a connectivity error."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            maps_primary=_make_maps_primary_fail("Google Maps API unreachable"),
            fetch_fn=_make_nominatim_geocode_fetch(),
        )
        result = await plugin.call_tool("maps_geocode", {"address": "10 Downing Street"})
        assert not result.is_error
        payload = json.loads(result.content)
        assert "results" in payload

    @pytest.mark.asyncio
    async def test_maps_reverse_geocode_falls_back_to_nominatim_on_primary_failure(self):
        """maps_reverse_geocode routes to Nominatim when the maps primary fails."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            maps_primary=_make_maps_primary_fail(),
            fetch_fn=_make_nominatim_reverse_fetch(),
        )
        result = await plugin.call_tool("maps_reverse_geocode", {"lat": 51.5, "lng": -0.1})
        assert not result.is_error
        payload = json.loads(result.content)
        assert "results" in payload

    @pytest.mark.asyncio
    async def test_maps_geocode_passes_through_on_primary_success(self):
        """maps_geocode returns primary result without calling Nominatim when primary succeeds."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        nominatim_called = []

        async def spy_fetch(method, url, *, headers=None, params=None, json=None):
            nominatim_called.append(method)
            return _NOMINATIM_GEOCODE_SAMPLE

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            maps_primary=_make_maps_primary_success(
                "maps_geocode", {"status": "OK", "results": [{"formatted_address": "London"}]}
            ),
            fetch_fn=spy_fetch,
        )
        result = await plugin.call_tool("maps_geocode", {"address": "London"})
        assert not result.is_error
        assert not nominatim_called

    @pytest.mark.asyncio
    async def test_maps_validation_error_bypasses_nominatim(self):
        """maps_geocode validation error from primary is returned without fallback."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        nominatim_called = []

        async def spy_fetch(method, url, *, headers=None, params=None, json=None):
            nominatim_called.append(method)
            return _NOMINATIM_GEOCODE_SAMPLE

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            maps_primary=_make_maps_validation_error("maps_geocode", "address"),
            fetch_fn=spy_fetch,
        )
        result = await plugin.call_tool("maps_geocode", {})
        assert result.is_error
        assert "required" in result.content
        assert not nominatim_called

    @pytest.mark.asyncio
    async def test_maps_geocode_directly_when_no_maps_primary(self):
        """When maps_primary=None, maps_geocode routes directly to Nominatim."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            maps_primary=None,
            fetch_fn=_make_nominatim_geocode_fetch(),
        )
        result = await plugin.call_tool("maps_geocode", {"address": "London"})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_maps_place_search_returns_error_when_no_maps_primary(self):
        """maps_place_search (no Nominatim fallback) returns error when maps_primary=None."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail(),
            maps_primary=None,
            fetch_fn=_make_nominatim_geocode_fetch(),
        )
        result = await plugin.call_tool("maps_place_search", {"query": "coffee"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_workspace_tools_still_work_alongside_maps_fallback(self):
        """Adding maps fallback does not break existing workspace (calendar) fallback."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_make_primary_fail("Google Calendar unreachable"),
            maps_primary=None,
            db_path=":memory:",
        )
        result = await plugin.call_tool("calendar_create_event", {
            "title": "Maps-era standup", "start": "2026-08-01T09:00:00",
        })
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_list_tools_includes_maps_tools_when_maps_primary_set(self):
        """With a maps_primary, list_tools() includes all maps_* tools."""
        from plugins.google_workspace_fallback import GoogleWorkspaceFallbackPlugin, _StubPrimaryPlugin
        from plugins.google_maps import GoogleMapsPlugin

        plugin = GoogleWorkspaceFallbackPlugin(
            primary=_StubPrimaryPlugin(),
            docs_primary=None,
            maps_primary=GoogleMapsPlugin(),
        )
        names = {t.name for t in plugin.list_tools()}
        assert "maps_geocode" in names
        assert "maps_reverse_geocode" in names
