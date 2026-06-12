"""
Google Workspace fallback plugin — Issue #21.

Wraps GoogleWorkspacePlugin and, when the primary Google/n8n path returns a
connectivity error, automatically routes to local OSS equivalents:

  gmail_send / gmail_search   → IMAP/SMTP  (stdlib imaplib + smtplib)
  sheets_read / sheets_write  → Grist HTTP API  (default: localhost:8484)
  drive_list / drive_upload   → Nextcloud WebDAV  (configurable host)
  calendar_create / list      → no fallback — primary error returned
                                (Google Calendar → local Scheduler is a
                                growth-loop candidate, not in scope here)

Offline detection: if the primary plugin returns is_error=True AND the error
content does not look like a client-side validation error ("is required",
"Unknown tool"), the call is treated as a connectivity failure and routed to
the appropriate OSS backend.

Injectable dependencies (all optional — defaults used in production):
  imap_fn   : (host, port) → IMAP4_SSL-like connection
  smtp_fn   : (host, port) → SMTP_SSL-like connection
  fetch_fn  : async (method, url, *, headers, json) → dict
              used by both GristFallback and NextcloudFallback
  run_fn    : reserved for future LibreOffice (Docs/Slides) fallback

Intentional omissions:
  • Nextcloud is conditional — drive fallbacks return a clear error when
    nextcloud_url is None.  Set NEXTCLOUD_URL env var or pass nextcloud_url
    to GoogleWorkspaceFallbackPlugin to enable.
  • LibreOffice (Docs/Slides) is not yet in GoogleWorkspacePlugin's tool set;
    the fallback will be wired when those tools are added.
  • Nominatim/Maps fallback applies to a future maps_* tool family.
"""
import email as _email_lib
import imaplib
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_workspace"

# ADR-0005 / Issue #44 — wraps GoogleWorkspacePlugin. Adds direct IMAP/SMTP
# (mail), Grist (sheets), and Nextcloud (drive) fallbacks. Reads and writes
# external services; reaches both local OSS backends and remote SMTP/IMAP
# servers.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "external_data_write",
    "network_egress_local",
    "network_egress_cloud",
})

FetchFn = Callable[..., Awaitable[dict]]

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

# If the primary error content contains any of these strings (case-insensitive)
# the error is treated as a client-side validation error, NOT a connectivity
# failure, so we do NOT trigger the OSS fallback.
_VALIDATION_HINTS: frozenset[str] = frozenset({"is required", "unknown tool"})


def _is_connection_failure(result: ToolResult) -> bool:
    """True when result looks like a connectivity/backend failure rather than
    a validation error from the primary plugin."""
    if not result.is_error:
        return False
    content_lower = result.content.lower()
    return not any(hint in content_lower for hint in _VALIDATION_HINTS)


# ---------------------------------------------------------------------------
# IMAP query builder
# ---------------------------------------------------------------------------

def _parse_imap_query(gmail_query: str) -> list[str]:
    """
    Translate a Gmail-style search query into IMAP SEARCH criteria tokens.

    Supported prefixes: from:, subject:, is:unread, is:read.
    Unrecognised queries fall back to TEXT search.
    Empty query returns ["ALL"].
    """
    if not gmail_query.strip():
        return ["ALL"]

    criteria: list[str] = []
    matched_any = False

    for token in gmail_query.strip().split():
        if token.startswith("from:"):
            criteria += ["FROM", token[5:].strip('"')]
            matched_any = True
        elif token.startswith("subject:"):
            criteria += ["SUBJECT", token[8:].strip('"')]
            matched_any = True
        elif token == "is:unread":
            criteria.append("UNSEEN")
            matched_any = True
        elif token == "is:read":
            criteria.append("SEEN")
            matched_any = True

    if not matched_any:
        # Fall back to a free-text search for unrecognised tokens.
        criteria += ["TEXT", gmail_query]

    return criteria if criteria else ["ALL"]


def _extract_body(msg) -> str:
    """Return the plain-text body of an email.message.Message, up to 2000 chars."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True)
                if raw:
                    return raw.decode("utf-8", errors="replace")[:2000]
    else:
        raw = msg.get_payload(decode=True)
        if raw:
            return raw.decode("utf-8", errors="replace")[:2000]
    return ""


# ---------------------------------------------------------------------------
# Grist range parser
# ---------------------------------------------------------------------------

def _parse_grist_range(range_str: str) -> tuple[str, str]:
    """
    Parse a Sheets-style range like "Sheet1!A1:D10" into (table_id, cell_range).
    If no "!" is present the default table name "Sheet1" is used.
    """
    if "!" in range_str:
        table_id, cell_range = range_str.split("!", 1)
        return table_id, cell_range
    return "Sheet1", range_str


# ---------------------------------------------------------------------------
# IMAP / SMTP fallback (Gmail)
# ---------------------------------------------------------------------------

class ImapSmtpFallback:
    """Handles gmail_send via SMTP and gmail_search via IMAP."""

    def __init__(
        self,
        imap_fn: Callable | None = None,
        smtp_fn: Callable | None = None,
        imap_host: str = "localhost",
        imap_port: int = 993,
        smtp_host: str = "localhost",
        smtp_port: int = 465,
        username: str = "",
        password: str = "",
    ) -> None:
        self._imap_fn = imap_fn or imaplib.IMAP4_SSL
        self._smtp_fn = smtp_fn or smtplib.SMTP_SSL
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._username = username
        self._password = password

    async def send(self, to: str, subject: str, body: str,
                   cc: str | None = None) -> ToolResult:
        try:
            msg = MIMEMultipart()
            msg["From"] = self._username or to
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc
            msg.attach(MIMEText(body, "plain"))

            recipients = [to]
            if cc:
                recipients.append(cc)

            conn = self._smtp_fn(self._smtp_host, self._smtp_port)
            if self._username and self._password:
                conn.login(self._username, self._password)
            conn.sendmail(self._username or to, recipients, msg.as_string())
            conn.quit()

            return ToolResult(content=json.dumps({
                "sent": True,
                "to": to,
                "subject": subject,
            }))
        except Exception as exc:
            return ToolResult(content=f"SMTP fallback failed: {exc}", is_error=True)

    async def search(self, query: str, max_results: int = 10) -> ToolResult:
        try:
            criteria = _parse_imap_query(query)

            conn = self._imap_fn(self._imap_host, self._imap_port)
            if self._username and self._password:
                conn.login(self._username, self._password)
            conn.select("INBOX")

            status, data = conn.search(None, *criteria)
            if status != "OK":
                conn.logout()
                return ToolResult(content="IMAP search returned non-OK status", is_error=True)

            msg_nums = (data[0].split() if data[0] else [])[-max_results:]
            messages = []

            for num in reversed(msg_nums):
                status, msg_data = conn.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                try:
                    raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                    if isinstance(raw, (bytes, bytearray)):
                        msg = _email_lib.message_from_bytes(raw)
                    else:
                        msg = _email_lib.message_from_string(raw)
                    messages.append({
                        "from": msg.get("From", ""),
                        "subject": msg.get("Subject", ""),
                        "date": msg.get("Date", ""),
                        "snippet": _extract_body(msg)[:200],
                    })
                except Exception:
                    continue

            conn.logout()
            return ToolResult(content=json.dumps({"messages": messages}))
        except Exception as exc:
            return ToolResult(content=f"IMAP fallback failed: {exc}", is_error=True)


# ---------------------------------------------------------------------------
# Grist fallback (Sheets)
# ---------------------------------------------------------------------------

class GristFallback:
    """
    Routes sheets_read_range / sheets_write_range to a local Grist instance.

    Grist REST API:
      GET  /api/docs/{docId}/tables/{tableId}/records  → read rows
      POST /api/docs/{docId}/tables/{tableId}/records  → append rows
    """

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        base_url: str = "http://localhost:8484",
        api_key: str = "",
    ) -> None:
        self._fetch = fetch_fn
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        h: dict = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _get_fetch(self) -> FetchFn:
        if self._fetch is not None:
            return self._fetch
        from plugins.n8n import _default_fetch
        return _default_fetch

    async def read_range(self, spreadsheet_id: str, range_str: str) -> ToolResult:
        try:
            table_id, _ = _parse_grist_range(range_str)
            url = (
                f"{self._base_url}/api/docs/{spreadsheet_id}"
                f"/tables/{table_id}/records"
            )
            resp = await self._get_fetch()("GET", url, headers=self._headers())
            records = resp.get("records", [])
            rows = [list(rec.get("fields", {}).values()) for rec in records]
            return ToolResult(content=json.dumps({"rows": rows, "count": len(rows)}))
        except Exception as exc:
            return ToolResult(content=f"Grist fallback failed: {exc}", is_error=True)

    async def write_range(self, spreadsheet_id: str, range_str: str,
                          data: list) -> ToolResult:
        try:
            table_id, _ = _parse_grist_range(range_str)
            url = (
                f"{self._base_url}/api/docs/{spreadsheet_id}"
                f"/tables/{table_id}/records"
            )
            records = [
                {"fields": {f"col{i + 1}": v for i, v in enumerate(row)}}
                for row in data
            ]
            await self._get_fetch()(
                "POST", url, headers=self._headers(),
                json={"records": records},
            )
            return ToolResult(content=json.dumps({"written": len(records)}))
        except Exception as exc:
            return ToolResult(content=f"Grist fallback failed: {exc}", is_error=True)


# ---------------------------------------------------------------------------
# Nextcloud fallback (Drive)
# ---------------------------------------------------------------------------

_NEXTCLOUD_NOT_CONFIGURED = (
    "Drive fallback unavailable: Nextcloud is not configured. "
    "Set NEXTCLOUD_URL (or pass nextcloud_url=) to enable drive operations offline."
)


class NextcloudFallback:
    """
    Routes drive_list_files / drive_upload_file to a Nextcloud instance via WebDAV.

    Nextcloud is optional — if base_url is None, every call returns a clear
    "not configured" error rather than a cryptic connection failure.
    """

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        base_url: str | None = None,
        username: str = "",
        password: str = "",
    ) -> None:
        self._fetch = fetch_fn
        self._base_url = base_url.rstrip("/") if base_url else None
        self._username = username
        self._password = password

    def _headers(self) -> dict:
        import base64
        creds = base64.b64encode(
            f"{self._username}:{self._password}".encode()
        ).decode()
        return {"Authorization": f"Basic {creds}"}

    def _get_fetch(self) -> FetchFn:
        if self._fetch is not None:
            return self._fetch
        from plugins.n8n import _default_fetch
        return _default_fetch

    def _dav_path(self, folder_id: str | None, filename: str | None = None) -> str:
        path = f"/remote.php/dav/files/{self._username}/"
        if folder_id:
            path += folder_id.strip("/") + "/"
        if filename:
            path += filename
        return path

    async def list_files(
        self,
        query: str | None = None,
        folder_id: str | None = None,
        max_results: int = 20,
    ) -> ToolResult:
        if self._base_url is None:
            return ToolResult(content=_NEXTCLOUD_NOT_CONFIGURED, is_error=True)
        try:
            url = self._base_url + self._dav_path(folder_id)
            resp = await self._get_fetch()("PROPFIND", url, headers=self._headers())
            files = resp.get("files", [])
            if query:
                q = query.lower()
                files = [f for f in files if q in str(f.get("name", "")).lower()]
            return ToolResult(content=json.dumps({"files": files[:max_results]}))
        except Exception as exc:
            return ToolResult(content=f"Nextcloud fallback failed: {exc}", is_error=True)

    async def upload_file(
        self,
        filename: str,
        content: str,
        folder_id: str | None = None,
        mime_type: str | None = None,
    ) -> ToolResult:
        if self._base_url is None:
            return ToolResult(content=_NEXTCLOUD_NOT_CONFIGURED, is_error=True)
        try:
            path = self._dav_path(folder_id, filename)
            url = self._base_url + path
            headers = self._headers()
            if mime_type:
                headers["Content-Type"] = mime_type
            await self._get_fetch()(
                "PUT", url, headers=headers, json={"content": content}
            )
            return ToolResult(content=json.dumps({
                "uploaded": True,
                "filename": filename,
                "path": path,
            }))
        except Exception as exc:
            return ToolResult(content=f"Nextcloud fallback failed: {exc}", is_error=True)


# ---------------------------------------------------------------------------
# Tools with OSS fallbacks
# ---------------------------------------------------------------------------

_FALLBACK_TOOLS: frozenset[str] = frozenset({
    "gmail_send",
    "gmail_search",
    "sheets_read_range",
    "sheets_write_range",
    "drive_list_files",
    "drive_upload_file",
})


# ---------------------------------------------------------------------------
# Stub primary plugin (used when google_workspace.py is retired)
# ---------------------------------------------------------------------------

class _StubPrimaryPlugin:
    """Stub plugin that lists the original GoogleWorkspacePlugin's tools
    but returns "not available" errors for all of them. This allows
    GoogleWorkspaceFallbackPlugin to provide OSS fallbacks for all
    tool calls when google_workspace.py has been retired."""

    name = "google_workspace_stub"

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="gmail_send",
                description="Send an email via Gmail (fallback to SMTP).",
                plugin="google_workspace",
            ),
            Tool(
                name="gmail_search",
                description="Search Gmail (fallback to IMAP).",
                plugin="google_workspace",
            ),
            Tool(
                name="calendar_create_event",
                description="Create a Google Calendar event.",
                plugin="google_workspace",
            ),
            Tool(
                name="calendar_list_events",
                description="List Google Calendar events.",
                plugin="google_workspace",
            ),
            Tool(
                name="drive_list_files",
                description="List Google Drive files (fallback to Nextcloud).",
                plugin="google_workspace",
            ),
            Tool(
                name="drive_upload_file",
                description="Upload a file to Google Drive (fallback to Nextcloud).",
                plugin="google_workspace",
            ),
            Tool(
                name="sheets_read_range",
                description="Read from a Google Sheet (fallback to Grist).",
                plugin="google_workspace",
            ),
            Tool(
                name="sheets_write_range",
                description="Write to a Google Sheet (fallback to Grist).",
                plugin="google_workspace",
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        return ToolResult(
            content="Google Workspace bridge retired; using OSS fallback",
            is_error=True,
        )


# ---------------------------------------------------------------------------
# Main plugin
# ---------------------------------------------------------------------------

class GoogleWorkspaceFallbackPlugin:
    """
    Drop-in replacement for GoogleWorkspacePlugin that automatically falls back
    to OSS equivalents when the primary Google/n8n backend is unreachable.

    The MCP interface (tool names, arg shapes, ToolResult contract) is identical
    to GoogleWorkspacePlugin — Felix's behaviour does not change based on which
    backend is active.
    """

    name = PLUGIN_NAME

    def __init__(
        self,
        primary=None,
        *,
        # IMAP/SMTP
        imap_fn: Callable | None = None,
        smtp_fn: Callable | None = None,
        imap_host: str = os.environ.get("IMAP_HOST", "localhost"),
        imap_port: int = int(os.environ.get("IMAP_PORT", "993")),
        smtp_host: str = os.environ.get("SMTP_HOST", "localhost"),
        smtp_port: int = int(os.environ.get("SMTP_PORT", "465")),
        mail_username: str = os.environ.get("MAIL_USERNAME", ""),
        mail_password: str = os.environ.get("MAIL_PASSWORD", ""),
        # Grist
        fetch_fn: FetchFn | None = None,
        grist_url: str = os.environ.get("GRIST_URL", "http://localhost:8484"),
        grist_api_key: str = os.environ.get("GRIST_API_KEY", ""),
        # Nextcloud
        nextcloud_url: str | None = os.environ.get("NEXTCLOUD_URL"),
        nextcloud_username: str = os.environ.get("NEXTCLOUD_USERNAME", ""),
        nextcloud_password: str = os.environ.get("NEXTCLOUD_PASSWORD", ""),
    ) -> None:
        if primary is not None:
            self._primary = primary
        else:
            try:
                from plugins.google_workspace import GoogleWorkspacePlugin
                self._primary = GoogleWorkspacePlugin()
            except ModuleNotFoundError:
                self._primary = _StubPrimaryPlugin()

        self._imap_smtp = ImapSmtpFallback(
            imap_fn=imap_fn,
            smtp_fn=smtp_fn,
            imap_host=imap_host,
            imap_port=imap_port,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            username=mail_username,
            password=mail_password,
        )
        self._grist = GristFallback(
            fetch_fn=fetch_fn,
            base_url=grist_url,
            api_key=grist_api_key,
        )
        self._nextcloud = NextcloudFallback(
            fetch_fn=fetch_fn,
            base_url=nextcloud_url,
            username=nextcloud_username,
            password=nextcloud_password,
        )

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return self._primary.list_tools()

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        result = await self._primary.call_tool(tool_name, args)
        if not result.is_error:
            return result

        if not _is_connection_failure(result) or tool_name not in _FALLBACK_TOOLS:
            return result

        logger.info(
            "Google/n8n path failed for %s (%s) — routing to OSS fallback",
            tool_name,
            result.content[:80],
        )
        return await self._call_fallback(tool_name, args)

    # ------------------------------------------------------------------
    # Fallback dispatch
    # ------------------------------------------------------------------

    async def _call_fallback(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "gmail_send":
            return await self._imap_smtp.send(
                to=args.get("to", ""),
                subject=args.get("subject", ""),
                body=args.get("body", ""),
                cc=args.get("cc"),
            )
        if tool_name == "gmail_search":
            return await self._imap_smtp.search(
                query=args.get("query", ""),
                max_results=args.get("max_results", 10),
            )
        if tool_name == "sheets_read_range":
            return await self._grist.read_range(
                spreadsheet_id=args.get("spreadsheet_id", ""),
                range_str=args.get("range", ""),
            )
        if tool_name == "sheets_write_range":
            return await self._grist.write_range(
                spreadsheet_id=args.get("spreadsheet_id", ""),
                range_str=args.get("range", ""),
                data=args.get("data", []),
            )
        if tool_name == "drive_list_files":
            return await self._nextcloud.list_files(
                query=args.get("query"),
                folder_id=args.get("folder_id"),
                max_results=args.get("max_results", 20),
            )
        if tool_name == "drive_upload_file":
            return await self._nextcloud.upload_file(
                filename=args.get("filename", ""),
                content=args.get("content", ""),
                folder_id=args.get("folder_id"),
                mime_type=args.get("mime_type"),
            )
        # Unreachable given _FALLBACK_TOOLS guard, but belt-and-suspenders:
        return ToolResult(content=f"No OSS fallback for tool: {tool_name}", is_error=True)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create(
    primary=None,
    *,
    fetch_fn: FetchFn | None = None,
    imap_fn: Callable | None = None,
    smtp_fn: Callable | None = None,
    grist_url: str | None = None,
    nextcloud_url: str | None = None,
    **kwargs,
) -> GoogleWorkspaceFallbackPlugin:
    return GoogleWorkspaceFallbackPlugin(
        primary=primary,
        fetch_fn=fetch_fn,
        imap_fn=imap_fn,
        smtp_fn=smtp_fn,
        grist_url=grist_url or os.environ.get("GRIST_URL", "http://localhost:8484"),
        nextcloud_url=nextcloud_url,
        **kwargs,
    )
