"""
Job Search MCP plugin — Issue #334 (S1).

Tools: jobs_fetch_postings.

Reads the Rat Race Rebellion job board (ratracerebellion.com/job-postings)
logged-out via OpenClaw's navigate/Readability path. Parses Job postings
(title, company, pay, snapshot, date, outbound URL) and upserts them into
a new SQLite table (job_postings). Dedup key is the outbound ATS URL.

All network I/O is injected via navigate_fn for testing.
All DB I/O is injectable via a JobSearchStore seam.
"""
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Awaitable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "job_search"
RRR_URL = "https://ratracerebellion.com/job-postings"
OPENCLAW_BASE = "http://localhost:3000"

# ADR-0005 / Issue #334 — jobs_fetch_postings fetches a public web page via
# OpenClaw's navigate path (network_egress_cloud + external_data_read) and
# persists results to SQLite (fs_write).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
    "fs_write",
})

_DB_PATH = Path(__file__).parent.parent / "cerebral" / "data" / "openmind.db"


# ── HTML parser ───────────────────────────────────────────────────────────────

class _RRRParser(HTMLParser):
    """State-machine parser for Readability-processed RRR job listing HTML.

    Expected structure (per-entry):
      <h2> or <h3> containing an <a href="https://ratracerebellion.com/...">title</a>
      Followed by paragraph text (snapshot / pay hint)
      Then one or more <a href="https://EXTERNAL_ATS_URL">...apply...</a>
    """

    _RRR_HOST = "ratracerebellion.com"

    def __init__(self) -> None:
        super().__init__()
        self._results: list[dict] = []
        # per-entry state
        self._current: dict | None = None
        self._capturing_title = False  # inside the title <a> tag
        self._in_heading = False        # inside h2/h3
        self._text_buf = ""             # accumulated text after the title

    # -- HTMLParser callbacks --------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        href = a.get("href", "")

        if tag in ("h2", "h3"):
            self._flush()          # save previous entry before starting new one
            self._in_heading = True
            return

        if tag == "a" and self._in_heading:
            if self._RRR_HOST in href:
                # RRR article link — this is the job title link
                self._current = {
                    "title": "",
                    "company": "",
                    "pay": "",
                    "snapshot": "",
                    "posted_date": "",
                    "url": "",
                    "_rrr_url": href,
                }
                self._capturing_title = True
                self._text_buf = ""
            return

        if tag == "a" and self._current is not None and not self._in_heading:
            if href and self._RRR_HOST not in href and href.startswith("http"):
                # First external link after the title = outbound ATS URL
                if not self._current["url"]:
                    self._current["url"] = href

        if tag == "time":
            dt = a.get("datetime", "")
            if dt and self._current is not None and not self._current["posted_date"]:
                self._current["posted_date"] = dt[:10]  # YYYY-MM-DD

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h2", "h3") and self._in_heading:
            self._in_heading = False
            self._capturing_title = False

        if tag == "a" and self._capturing_title:
            self._capturing_title = False

    def handle_data(self, data: str) -> None:
        if self._capturing_title and self._current is not None:
            self._current["title"] += data
        elif self._current is not None and not self._in_heading:
            self._text_buf += data

    # -- helpers ---------------------------------------------------------------

    def _flush(self) -> None:
        if self._current and self._current.get("title"):
            entry = dict(self._current)
            raw_text = self._text_buf.strip()
            entry["title"] = entry["title"].strip()
            # extract company from title: "Job Title — Company" or "Job Title at Company"
            for sep in (" — ", " - ", " at ", " @ "):
                if sep in entry["title"]:
                    parts = entry["title"].split(sep, 1)
                    entry["company"] = parts[-1].strip()
                    break
            # look for pay hint in text
            pay_m = re.search(r"\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?(?:\s*/\s*(?:hr|hour|year|yr|mo|month))?", raw_text, re.I)
            if pay_m:
                entry["pay"] = pay_m.group(0).strip()
            # first 200 chars as snapshot
            entry["snapshot"] = raw_text[:200].strip()
            self._results.append(entry)
        self._current = None
        self._text_buf = ""

    def results(self) -> list[dict]:
        self._flush()
        return self._results


def parse_postings(html: str) -> list[dict]:
    """Parse Readability-processed RRR HTML, return list of posting dicts."""
    p = _RRRParser()
    p.feed(html)
    return p.results()


# ── SQLite store ──────────────────────────────────────────────────────────────

class JobSearchStore:
    """Persist and query job_postings in SQLite. Thread-safe (check_same_thread=False)."""

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS job_postings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    UNIQUE NOT NULL,
                title       TEXT    NOT NULL DEFAULT '',
                company     TEXT    NOT NULL DEFAULT '',
                pay         TEXT    NOT NULL DEFAULT '',
                snapshot    TEXT    NOT NULL DEFAULT '',
                posted_date TEXT    NOT NULL DEFAULT '',
                fetched_at  TEXT    NOT NULL
            )
        """)
        self._con.commit()

    def upsert(self, posting: dict) -> None:
        """Insert or update a posting by outbound URL."""
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute("""
            INSERT INTO job_postings (url, title, company, pay, snapshot, posted_date, fetched_at)
            VALUES (:url, :title, :company, :pay, :snapshot, :posted_date, :now)
            ON CONFLICT(url) DO UPDATE SET
                title       = excluded.title,
                company     = excluded.company,
                pay         = excluded.pay,
                snapshot    = excluded.snapshot,
                posted_date = excluded.posted_date,
                fetched_at  = excluded.fetched_at
        """, {**posting, "now": now})
        self._con.commit()

    def list_postings(self) -> list[dict]:
        cur = self._con.execute(
            "SELECT * FROM job_postings ORDER BY fetched_at DESC, id DESC"
        )
        return [dict(row) for row in cur.fetchall()]

    def count(self) -> int:
        return self._con.execute("SELECT COUNT(*) FROM job_postings").fetchone()[0]


# ── Module-level seams ────────────────────────────────────────────────────────

_navigate_fn: Callable[[str], Awaitable[str]] | None = None
_store: JobSearchStore | None = None


def set_navigate_fn(fn: Callable[[str], Awaitable[str]]) -> None:
    global _navigate_fn
    _navigate_fn = fn


def set_store(store: "JobSearchStore") -> None:
    global _store
    _store = store


# ── Default navigate fn (calls OpenClaw) ──────────────────────────────────────

async def _default_navigate(url: str) -> str:
    body = {"url": url}
    endpoint = f"{OPENCLAW_BASE}/browser/navigate"
    for lib in ("aiohttp", "httpx"):
        try:
            if lib == "aiohttp":
                import aiohttp  # type: ignore
                async with aiohttp.ClientSession() as s:
                    async with s.post(endpoint, json=body) as r:
                        data = await r.json()
                return data.get("content", "")
            else:
                import httpx  # type: ignore
                async with httpx.AsyncClient() as c:
                    r = await c.post(endpoint, json=body)
                    return r.json().get("content", "")
        except ImportError:
            continue
    raise RuntimeError("Neither aiohttp nor httpx installed")


# ── Plugin ────────────────────────────────────────────────────────────────────

class JobSearchPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        navigate_fn: Callable[[str], Awaitable[str]] | None = None,
        store: JobSearchStore | None = None,
    ) -> None:
        self._navigate = navigate_fn or _default_navigate
        self._store = store

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="jobs_fetch_postings",
                description=(
                    "Check Rat Race Rebellion for new remote job postings. "
                    "Fetches the public feed, parses job listings (title, company, pay, "
                    "outbound ATS URL), persists them, and returns all stored postings."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "jobs_fetch_postings":
            return await self._fetch_postings()
        return ToolResult(content=f"Unknown tool: {tool_name!r}", is_error=True)

    async def _fetch_postings(self) -> ToolResult:
        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)
        navigate = self._navigate
        try:
            html = await navigate(RRR_URL)
        except Exception as exc:
            logger.error("[job_search] navigate failed: %s", exc)
            return ToolResult(content=f"Failed to fetch job board: {exc}", is_error=True)
        postings = parse_postings(html)
        # Only upsert entries that have an outbound URL (the dedup key)
        saved = 0
        for p in postings:
            if p.get("url"):
                store.upsert(p)
                saved += 1
        all_postings = store.list_postings()
        return ToolResult(content=json.dumps({
            "fetched": len(postings),
            "saved": saved,
            "postings": all_postings,
        }))


def create(
    navigate_fn: Callable[[str], Awaitable[str]] | None = None,
    store: "JobSearchStore | None" = None,
) -> JobSearchPlugin:
    return JobSearchPlugin(navigate_fn=navigate_fn, store=store)
