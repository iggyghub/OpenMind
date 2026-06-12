"""
Google Workspace fallback plugin — Issue #21.

Wraps GoogleWorkspacePlugin and, when the primary Google/n8n path returns a
connectivity error, automatically routes to local OSS equivalents:

  gmail_send / gmail_search   -> IMAP/SMTP  (stdlib imaplib + smtplib)
  sheets_read / sheets_write  -> Grist HTTP API  (default: localhost:8484)
  drive_list / drive_upload   -> Nextcloud WebDAV  (configurable host)
  calendar_* (4 tools)        -> local SQLite scheduler (Issue #232)
  docs_create/read/append     -> local ODF .odt files via stdlib (Issue #233)
  maps_geocode/reverse_geocode -> Nominatim OSS geocoder (Issue #234)

Offline detection: if the primary plugin returns is_error=True AND the error
content does not look like a client-side validation error ("is required",
"Unknown tool"), the call is treated as a connectivity failure and routed to
the appropriate OSS backend.

Injectable dependencies (all optional -- defaults used in production):
  imap_fn              : (host, port) -> IMAP4_SSL-like connection
  smtp_fn              : (host, port) -> SMTP_SSL-like connection
  fetch_fn             : async (method, url, *, headers, json, params) -> dict
                         used by GristFallback, NextcloudFallback, NominatimFallback
  db_path              : path to the SQLite file used by CalendarSQLiteFallback;
                         ":memory:" is accepted for tests.
  docs_primary         : GoogleDocsPlugin (or stub) for docs_* tools; defaults to a
                         live GoogleDocsPlugin() if importable, else None.
  docs_dir             : directory for local .odt files; defaults to LOCAL_DOCS_DIR
                         env var or ~/Documents/OpenMind.
  maps_primary         : GoogleMapsPlugin (or stub) for maps_* tools; defaults to a
                         live GoogleMapsPlugin() if importable, else None.
  nominatim_url        : Nominatim base URL; defaults to NOMINATIM_URL env var or
                         the public instance. Set to a self-hosted URL for true
                         offline use or high-volume geocoding.
  nominatim_user_agent : User-Agent header sent to Nominatim; defaults to
                         NOMINATIM_USER_AGENT env var or "OpenMind/1.0 ...".

Intentional omissions:
  - Nextcloud is conditional -- drive fallbacks return a clear error when
    nextcloud_url is None. Set NEXTCLOUD_URL env var or pass nextcloud_url
    to GoogleWorkspaceFallbackPlugin to enable.
  - maps_place_search and maps_directions have no direct Nominatim equivalent;
    those calls pass through to the primary (Google Maps) only.

Nominatim public instance usage policy:
  - A custom User-Agent identifying this application is required on every request.
  - Maximum 1 request per second on the public instance; self-host for higher
    volume or fully offline deployments (set nominatim_url accordingly).
  - Do not use the public instance for bulk or automated geocoding.
  - See https://operations.osmfoundation.org/policies/nominatim/
"""
import email as _email_lib
import imaplib
import json
import logging
import os
import smtplib
import uuid
import xml.etree.ElementTree as ET
import zipfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_workspace"

_UNSET = object()  # sentinel: distinguishes "not provided" from None in docs_primary

# ADR-0005 / Issue #44 — wraps GoogleWorkspacePlugin. Adds direct IMAP/SMTP
# (mail), Grist (sheets), and Nextcloud (drive) fallbacks. Reads and writes
# external services; reaches both local OSS backends and remote SMTP/IMAP
# servers.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "external_data_write",
    "network_egress_local",
    "network_egress_cloud",
    "fs_read",   # DocsODTFallback reads .meta.json and .odt content
    "fs_write",  # DocsODTFallback writes .odt files and .meta.json sidecars
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
# SQLite calendar fallback (Issue #232)
# ---------------------------------------------------------------------------

class CalendarSQLiteFallback:
    """
    Routes calendar_list_events / calendar_create_event / calendar_update_event
    / calendar_delete_event to the local SchedulerPlugin (SQLite-backed).

    Args are mapped from the calendar plugin's naming convention (start/end/from/to)
    to the scheduler's convention (start_iso/end_iso/from_iso/to_iso).
    """

    def __init__(self, db_path: str | None = None) -> None:
        from plugins.scheduler import SchedulerPlugin
        self._sched = SchedulerPlugin(db_path=db_path)

    def list_events(self, args: dict) -> ToolResult:
        mapped: dict = {}
        if args.get("from"):
            mapped["from_iso"] = args["from"]
        if args.get("to"):
            mapped["to_iso"] = args["to"]
        return self._sched._list_events(mapped)

    def create_event(self, args: dict) -> ToolResult:
        mapped: dict = {
            "title": args.get("title", ""),
            "start_iso": args.get("start", ""),
        }
        if args.get("end"):
            mapped["end_iso"] = args["end"]
        return self._sched._create_event(mapped)

    def update_event(self, args: dict) -> ToolResult:
        mapped: dict = {"id": args.get("id")}
        if args.get("title"):
            mapped["title"] = args["title"]
        if args.get("start"):
            mapped["start_iso"] = args["start"]
        if args.get("end"):
            mapped["end_iso"] = args["end"]
        if args.get("recurrence"):
            mapped["recurrence"] = args["recurrence"]
        return self._sched._update_event(mapped)

    def delete_event(self, args: dict) -> ToolResult:
        return self._sched._delete_event({"id": args.get("id")})


# ---------------------------------------------------------------------------
# SQLite tasks fallback (Issue #235)
# ---------------------------------------------------------------------------

class TasksSQLiteFallback:
    """
    Routes tasks_list / tasks_create / tasks_complete / tasks_delete to a
    local SQLite backing store.

    Maps Google Tasks API naming convention to internal SQLite representation.
    """

    def __init__(self, db_path: str | None = None) -> None:
        import sqlite3
        from pathlib import Path

        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "cerebral" / "data" / "openmind.db")
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tasklist_id TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                status      TEXT    DEFAULT 'needsAction',
                notes       TEXT,
                due         TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._con.commit()

    def list_tasks(self, args: dict) -> ToolResult:
        tasklist_id = args.get("tasklist_id")
        if not tasklist_id:
            return ToolResult(content="tasklist_id is required", is_error=True)
        max_results = args.get("max_results", 10)

        rows = self._con.execute(
            "SELECT * FROM tasks WHERE tasklist_id=? ORDER BY created_at DESC LIMIT ?",
            (tasklist_id, max_results),
        ).fetchall()
        tasks = [_row_to_task(r) for r in rows]
        return ToolResult(content=json.dumps({"tasks": tasks}))

    def create_task(self, args: dict) -> ToolResult:
        tasklist_id = args.get("tasklist_id", "").strip()
        title = args.get("title", "").strip()
        notes = args.get("notes")
        due = args.get("due")

        if not tasklist_id:
            return ToolResult(content="tasklist_id is required", is_error=True)
        if not title:
            return ToolResult(content="title is required", is_error=True)

        cur = self._con.execute(
            "INSERT INTO tasks (tasklist_id, title, status, notes, due) VALUES (?, ?, ?, ?, ?)",
            (tasklist_id, title, "needsAction", notes, due),
        )
        self._con.commit()
        return ToolResult(content=json.dumps({
            "id": str(cur.lastrowid),
            "title": title,
            "status": "needsAction",
        }))

    def complete_task(self, args: dict) -> ToolResult:
        task_id = args.get("task_id")
        tasklist_id = args.get("tasklist_id")

        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        if not tasklist_id:
            return ToolResult(content="tasklist_id is required", is_error=True)

        row = self._con.execute(
            "SELECT id FROM tasks WHERE id=? AND tasklist_id=?",
            (task_id, tasklist_id),
        ).fetchone()
        if not row:
            return ToolResult(content=f"Task {task_id} not found", is_error=True)

        self._con.execute(
            "UPDATE tasks SET status=? WHERE id=?",
            ("completed", task_id),
        )
        self._con.commit()
        return ToolResult(content=json.dumps({
            "id": str(task_id),
            "status": "completed",
        }))

    def delete_task(self, args: dict) -> ToolResult:
        task_id = args.get("task_id")
        tasklist_id = args.get("tasklist_id")

        if not task_id:
            return ToolResult(content="task_id is required", is_error=True)
        if not tasklist_id:
            return ToolResult(content="tasklist_id is required", is_error=True)

        row = self._con.execute(
            "SELECT id FROM tasks WHERE id=? AND tasklist_id=?",
            (task_id, tasklist_id),
        ).fetchone()
        if not row:
            return ToolResult(content=f"Task {task_id} not found", is_error=True)

        self._con.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self._con.commit()
        return ToolResult(content=json.dumps({"deleted": True}))


def _row_to_task(row) -> dict:
    """Convert a SQLite row to a task dict."""
    return {
        "id": str(row["id"]),
        "tasklist_id": row["tasklist_id"],
        "title": row["title"],
        "status": row["status"],
        "notes": row["notes"] or "",
        "due": row["due"] or "",
    }


# ---------------------------------------------------------------------------
# ODF fallback (Docs) — Issue #233
# ---------------------------------------------------------------------------

class DocsODTFallback:
    """
    Routes docs_create / docs_read / docs_append to local .odt files.

    Mechanism chosen: pure-Python ODF 1.2 via stdlib zipfile +
    xml.etree.ElementTree — no LibreOffice binary, no third-party packages.
    A <doc-id>.meta.json sidecar next to each .odt stores the title so
    docs_read can return it.  document_id is an 8-hex UUID stem;
    docs_dir defaults to ~/Documents/OpenMind or LOCAL_DOCS_DIR env var.
    """

    _OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    _TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    _MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
    _ODF_MIME = "application/vnd.oasis.opendocument.text"

    def __init__(self, docs_dir: str | None = None) -> None:
        self._docs_dir = docs_dir or os.environ.get(
            "LOCAL_DOCS_DIR",
            os.path.join(os.path.expanduser("~"), "Documents", "OpenMind"),
        )

    def _odt_path(self, doc_id: str) -> str:
        stem = doc_id[:-4] if doc_id.endswith(".odt") else doc_id
        stem = stem.replace("/", "_").replace("\\", "_")
        return os.path.join(self._docs_dir, stem + ".odt")

    def _meta_path(self, doc_id: str) -> str:
        stem = doc_id[:-4] if doc_id.endswith(".odt") else doc_id
        stem = stem.replace("/", "_").replace("\\", "_")
        return os.path.join(self._docs_dir, stem + ".meta.json")

    def _ensure_dir(self) -> None:
        os.makedirs(self._docs_dir, exist_ok=True)

    def _write_odt(self, path: str, paragraphs: list[str]) -> None:
        ns_o = self._OFFICE_NS
        ns_t = self._TEXT_NS
        ns_m = self._MANIFEST_NS

        root = ET.Element(f"{{{ns_o}}}document-content")
        root.set(f"{{{ns_o}}}version", "1.2")
        body = ET.SubElement(root, f"{{{ns_o}}}body")
        text_elem = ET.SubElement(body, f"{{{ns_o}}}text")
        for para in (paragraphs or [""]):
            p = ET.SubElement(text_elem, f"{{{ns_t}}}p")
            p.text = para
        content_xml = ET.tostring(root, encoding="UTF-8", xml_declaration=True)

        mf_root = ET.Element(f"{{{ns_m}}}manifest")
        mf_root.set(f"{{{ns_m}}}version", "1.2")
        ET.SubElement(mf_root, f"{{{ns_m}}}file-entry", {
            f"{{{ns_m}}}full-path": "/",
            f"{{{ns_m}}}media-type": self._ODF_MIME,
        })
        ET.SubElement(mf_root, f"{{{ns_m}}}file-entry", {
            f"{{{ns_m}}}full-path": "content.xml",
            f"{{{ns_m}}}media-type": "text/xml",
        })
        manifest_xml = ET.tostring(mf_root, encoding="UTF-8", xml_declaration=True)

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            mi = zipfile.ZipInfo("mimetype")
            mi.compress_type = zipfile.ZIP_STORED
            zf.writestr(mi, self._ODF_MIME)
            zf.writestr("content.xml", content_xml)
            zf.writestr("META-INF/manifest.xml", manifest_xml)

    def _read_paragraphs(self, path: str) -> list[str]:
        with zipfile.ZipFile(path, "r") as zf:
            with zf.open("content.xml") as f:
                tree = ET.parse(f)
        return [
            (p.text or "")
            for p in tree.getroot().iter(f"{{{self._TEXT_NS}}}p")
        ]

    def create(self, title: str, body: str = "") -> ToolResult:
        try:
            self._ensure_dir()
            doc_id = uuid.uuid4().hex[:8]
            path = self._odt_path(doc_id)
            paragraphs = body.splitlines() if body else [""]
            self._write_odt(path, paragraphs)
            with open(self._meta_path(doc_id), "w", encoding="utf-8") as f:
                json.dump({"title": title}, f)
            return ToolResult(content=json.dumps({
                "id": doc_id,
                "title": title,
                "path": path,
            }))
        except Exception as exc:
            return ToolResult(content=f"ODF create failed: {exc}", is_error=True)

    def read(self, document_id: str) -> ToolResult:
        try:
            path = self._odt_path(document_id)
            if not os.path.exists(path):
                return ToolResult(
                    content=f"Document not found: {document_id}", is_error=True
                )
            paras = self._read_paragraphs(path)
            title = document_id
            meta_path = self._meta_path(document_id)
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    title = json.load(f).get("title", document_id)
            return ToolResult(content=json.dumps({
                "document_id": document_id,
                "title": title,
                "body": "\n".join(paras),
            }))
        except Exception as exc:
            return ToolResult(content=f"ODF read failed: {exc}", is_error=True)

    def append(self, document_id: str, text: str) -> ToolResult:
        try:
            path = self._odt_path(document_id)
            if not os.path.exists(path):
                return ToolResult(
                    content=f"Document not found: {document_id}", is_error=True
                )
            paras = self._read_paragraphs(path)
            paras.extend(text.splitlines() if text else [""])
            self._write_odt(path, paras)
            return ToolResult(content=json.dumps({
                "document_id": document_id,
                "appended": True,
            }))
        except Exception as exc:
            return ToolResult(content=f"ODF append failed: {exc}", is_error=True)

    def call(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "docs_create":
            return self.create(title=args.get("title", ""), body=args.get("body", ""))
        if tool_name == "docs_read":
            return self.read(document_id=args.get("document_id", ""))
        if tool_name == "docs_append":
            return self.append(
                document_id=args.get("document_id", ""),
                text=args.get("text", ""),
            )
        return ToolResult(content=f"No ODF fallback for: {tool_name}", is_error=True)


# ---------------------------------------------------------------------------
# Nominatim fallback (Maps) — Issue #234
# ---------------------------------------------------------------------------

_NOMINATIM_DEFAULT_URL = "https://nominatim.openstreetmap.org"
_NOMINATIM_DEFAULT_UA = "OpenMind/1.0 (github.com/iggyghub/OpenMind)"


class NominatimFallback:
    """Routes maps_geocode / maps_reverse_geocode to the Nominatim geocoding API.

    Public instance usage policy (https://operations.osmfoundation.org/policies/nominatim/):
      - A unique User-Agent is sent on every request (required by policy).
      - Callers must not burst; rate-limit to 1 req/s on the public instance.
      - Set nominatim_url to a self-hosted instance for high-volume or fully
        offline use (no rate limit or User-Agent enforcement on private instances,
        but the header is still sent for good practice).

    Args:
      fetch_fn       : injectable transport; defaults to google_maps._default_fetch
                       (supports params= keyword arg for GET query strings).
      base_url       : Nominatim base URL (default: public OSM instance).
      user_agent     : User-Agent header value (required by Nominatim policy).
    """

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        base_url: str = _NOMINATIM_DEFAULT_URL,
        user_agent: str = _NOMINATIM_DEFAULT_UA,
    ) -> None:
        self._fetch = fetch_fn
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent

    def _headers(self) -> dict:
        return {"User-Agent": self._user_agent, "Accept": "application/json"}

    def _get_fetch(self) -> FetchFn:
        if self._fetch is not None:
            return self._fetch
        from plugins.google_maps import _default_fetch
        return _default_fetch

    async def geocode(self, address: str) -> ToolResult:
        try:
            url = f"{self._base_url}/search"
            resp = await self._get_fetch()(
                "GET", url,
                headers=self._headers(),
                params={"q": address, "format": "json", "addressdetails": "1"},
            )
            if not isinstance(resp, list):
                return ToolResult(
                    content="Nominatim geocode returned unexpected format", is_error=True
                )
            results = []
            for r in resp:
                if not isinstance(r, dict):
                    continue
                results.append({
                    "formatted_address": r.get("display_name", ""),
                    "lat": float(r["lat"]) if r.get("lat") else None,
                    "lng": float(r["lon"]) if r.get("lon") else None,
                    "place_id": str(r.get("place_id", "")),
                    "types": r.get("type", ""),
                })
            return ToolResult(content=json.dumps({"status": "OK", "results": results}))
        except Exception as exc:
            return ToolResult(content=f"Nominatim geocode failed: {exc}", is_error=True)

    async def reverse_geocode(self, lat: float, lng: float) -> ToolResult:
        try:
            url = f"{self._base_url}/reverse"
            resp = await self._get_fetch()(
                "GET", url,
                headers=self._headers(),
                params={"lat": str(lat), "lon": str(lng), "format": "json"},
            )
            if not isinstance(resp, dict):
                return ToolResult(
                    content="Nominatim reverse geocode returned unexpected format",
                    is_error=True,
                )
            if "error" in resp:
                return ToolResult(
                    content=f"Nominatim reverse geocode: {resp['error']}", is_error=True
                )
            result = {
                "formatted_address": resp.get("display_name", ""),
                "lat": float(resp["lat"]) if resp.get("lat") else lat,
                "lng": float(resp["lon"]) if resp.get("lon") else lng,
                "place_id": str(resp.get("place_id", "")),
                "types": resp.get("type", ""),
            }
            return ToolResult(content=json.dumps({"status": "OK", "results": [result]}))
        except Exception as exc:
            return ToolResult(
                content=f"Nominatim reverse geocode failed: {exc}", is_error=True
            )


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
    "calendar_list_events",
    "calendar_create_event",
    "calendar_update_event",
    "calendar_delete_event",
    "tasks_list",
    "tasks_create",
    "tasks_complete",
    "tasks_delete",
})

_DOCS_FALLBACK_TOOLS: frozenset[str] = frozenset({
    "docs_create",
    "docs_read",
    "docs_append",
})

_MAPS_FALLBACK_TOOLS: frozenset[str] = frozenset({
    "maps_geocode",
    "maps_reverse_geocode",
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
                name="calendar_update_event",
                description="Update an existing calendar event (fallback to SQLite).",
                plugin="google_workspace",
            ),
            Tool(
                name="calendar_delete_event",
                description="Delete a calendar event by id (fallback to SQLite).",
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
        # Calendar SQLite fallback
        db_path: str | None = None,
        # Docs ODF fallback (Issue #233)
        docs_primary=_UNSET,
        docs_dir: str | None = None,
        # Maps Nominatim fallback (Issue #234)
        maps_primary=_UNSET,
        nominatim_url: str = os.environ.get("NOMINATIM_URL", _NOMINATIM_DEFAULT_URL),
        nominatim_user_agent: str = os.environ.get(
            "NOMINATIM_USER_AGENT", _NOMINATIM_DEFAULT_UA
        ),
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
        self._calendar_sqlite = CalendarSQLiteFallback(db_path=db_path)
        self._tasks_sqlite = TasksSQLiteFallback(db_path=db_path)

        if docs_primary is not _UNSET:
            self._docs_primary = docs_primary
        else:
            try:
                from plugins.google_docs import GoogleDocsPlugin
                self._docs_primary = GoogleDocsPlugin()
            except (ModuleNotFoundError, Exception):
                self._docs_primary = None
        self._docs_odt = DocsODTFallback(docs_dir=docs_dir)

        if maps_primary is not _UNSET:
            self._maps_primary = maps_primary
        else:
            try:
                from plugins.google_maps import GoogleMapsPlugin
                self._maps_primary = GoogleMapsPlugin()
            except (ModuleNotFoundError, Exception):
                self._maps_primary = None
        self._nominatim = NominatimFallback(
            fetch_fn=fetch_fn,
            base_url=nominatim_url,
            user_agent=nominatim_user_agent,
        )

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        tools = list(self._primary.list_tools())
        if self._docs_primary is not None:
            tools.extend(self._docs_primary.list_tools())
        if self._maps_primary is not None:
            tools.extend(self._maps_primary.list_tools())
        return tools

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name.startswith("docs_"):
            return await self._call_docs_tool(tool_name, args)
        if tool_name.startswith("maps_"):
            return await self._call_maps_tool(tool_name, args)

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

    async def _call_docs_tool(self, tool_name: str, args: dict) -> ToolResult:
        if self._docs_primary is not None:
            result = await self._docs_primary.call_tool(tool_name, args)
            if not result.is_error:
                return result
            if not _is_connection_failure(result) or tool_name not in _DOCS_FALLBACK_TOOLS:
                return result
            logger.info(
                "Google Docs API failed for %s (%s) — routing to ODF fallback",
                tool_name,
                result.content[:80],
            )
        elif tool_name not in _DOCS_FALLBACK_TOOLS:
            return ToolResult(
                content=f"No fallback available for: {tool_name}", is_error=True
            )
        return self._docs_odt.call(tool_name, args)

    async def _call_maps_tool(self, tool_name: str, args: dict) -> ToolResult:
        if self._maps_primary is not None:
            result = await self._maps_primary.call_tool(tool_name, args)
            if not result.is_error:
                return result
            if not _is_connection_failure(result) or tool_name not in _MAPS_FALLBACK_TOOLS:
                return result
            logger.info(
                "Google Maps API failed for %s (%s) -- routing to Nominatim fallback",
                tool_name,
                result.content[:80],
            )
        elif tool_name not in _MAPS_FALLBACK_TOOLS:
            return ToolResult(
                content=f"No fallback available for: {tool_name}", is_error=True
            )
        if tool_name == "maps_geocode":
            return await self._nominatim.geocode(address=args.get("address", ""))
        if tool_name == "maps_reverse_geocode":
            return await self._nominatim.reverse_geocode(
                lat=args.get("lat", 0.0),
                lng=args.get("lng", 0.0),
            )
        return ToolResult(
            content=f"No Nominatim fallback for: {tool_name}", is_error=True
        )

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
        if tool_name == "calendar_list_events":
            return self._calendar_sqlite.list_events(args)
        if tool_name == "calendar_create_event":
            return self._calendar_sqlite.create_event(args)
        if tool_name == "calendar_update_event":
            return self._calendar_sqlite.update_event(args)
        if tool_name == "calendar_delete_event":
            return self._calendar_sqlite.delete_event(args)
        if tool_name == "tasks_list":
            return self._tasks_sqlite.list_tasks(args)
        if tool_name == "tasks_create":
            return self._tasks_sqlite.create_task(args)
        if tool_name == "tasks_complete":
            return self._tasks_sqlite.complete_task(args)
        if tool_name == "tasks_delete":
            return self._tasks_sqlite.delete_task(args)
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
    docs_dir: str | None = None,
    nominatim_url: str | None = None,
    nominatim_user_agent: str | None = None,
    **kwargs,
) -> GoogleWorkspaceFallbackPlugin:
    init_kwargs: dict = dict(
        primary=primary,
        fetch_fn=fetch_fn,
        imap_fn=imap_fn,
        smtp_fn=smtp_fn,
        grist_url=grist_url or os.environ.get("GRIST_URL", "http://localhost:8484"),
        nextcloud_url=nextcloud_url,
        docs_dir=docs_dir,
    )
    if nominatim_url is not None:
        init_kwargs["nominatim_url"] = nominatim_url
    if nominatim_user_agent is not None:
        init_kwargs["nominatim_user_agent"] = nominatim_user_agent
    init_kwargs.update(kwargs)
    return GoogleWorkspaceFallbackPlugin(**init_kwargs)
