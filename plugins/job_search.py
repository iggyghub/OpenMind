"""
Job Search MCP plugin — Issues #334 (S1) + #335 (S2) + #336 (S3).

Tools: jobs_fetch_postings, jobs_store_resume, jobs_score_shortlist, jobs_set_approval.

S1: Reads Rat Race Rebellion job board logged-out via OpenClaw navigate/Readability.
    Parses Job postings and upserts into SQLite job_postings table.

S2: Accepts a resume PDF text + path, persists the Resume artifact path per-profile,
    extracts structured Applicant dossier fields via injectable extract_fn (LLM in
    prod, stub in tests), and upserts into SQLite resume_artifacts + applicant_dossier.

S3: Scores new Job postings for fit via injectable score_fn (LLM in prod, stub in tests),
    persists fit_score + status on job_postings, and returns a ranked Shortlist.
    User approve/reject transitions: shortlisted | rejected.

All network I/O is injected via navigate_fn.
All DB I/O is injectable via a JobSearchStore seam.
LLM extraction is injectable via set_extract_fn.
LLM fit-scoring is injectable via set_score_fn.
"""
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Awaitable, Any

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "job_search"
RRR_URL = "https://ratracerebellion.com/job-postings"
OPENCLAW_BASE = "http://localhost:3000"

# ADR-0005 / Issue #334 — jobs_fetch_postings: network_egress_cloud + external_data_read + fs_write.
# Issue #335 — jobs_store_resume: fs_read (PDF artifact) + fs_write (dossier).
# network_egress_cloud covers cloud-LLM extraction; llm_call is not in the 16-class vocabulary.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
    "fs_write",
    "fs_read",
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
                fetched_at  TEXT    NOT NULL,
                fit_score   REAL,
                status      TEXT    NOT NULL DEFAULT 'new'
            )
        """)
        # S3 #336 — upgrade existing DB rows that pre-date these columns.
        for stmt in (
            "ALTER TABLE job_postings ADD COLUMN fit_score REAL",
            "ALTER TABLE job_postings ADD COLUMN status TEXT NOT NULL DEFAULT 'new'",
        ):
            try:
                self._con.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        # S2 #335 — Resume artifact: one PDF path per profile (UNIQUE on profile_id).
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS resume_artifacts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id  INTEGER UNIQUE NOT NULL,
                pdf_path    TEXT    NOT NULL DEFAULT '',
                updated_at  TEXT    NOT NULL
            )
        """)
        # S2 #335 — Applicant dossier: structured fields extracted from Resume artifact.
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS applicant_dossier (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id         INTEGER UNIQUE NOT NULL,
                name               TEXT NOT NULL DEFAULT '',
                email              TEXT NOT NULL DEFAULT '',
                phone              TEXT NOT NULL DEFAULT '',
                location           TEXT NOT NULL DEFAULT '',
                linkedin           TEXT NOT NULL DEFAULT '',
                github             TEXT NOT NULL DEFAULT '',
                website            TEXT NOT NULL DEFAULT '',
                work_history_json  TEXT NOT NULL DEFAULT '[]',
                education_json     TEXT NOT NULL DEFAULT '[]',
                skills_json        TEXT NOT NULL DEFAULT '[]',
                raw_text           TEXT NOT NULL DEFAULT '',
                updated_at         TEXT NOT NULL
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

    # ── S2 #335 — Resume artifact ──────────────────────────────────────────

    def upsert_resume_artifact(self, profile_id: int, pdf_path: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute("""
            INSERT INTO resume_artifacts (profile_id, pdf_path, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                pdf_path   = excluded.pdf_path,
                updated_at = excluded.updated_at
        """, (profile_id, pdf_path, now))
        self._con.commit()

    def get_resume_artifact(self, profile_id: int) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM resume_artifacts WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── S2 #335 — Applicant dossier ────────────────────────────────────────

    def upsert_dossier(self, profile_id: int, fields: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute("""
            INSERT INTO applicant_dossier
                (profile_id, name, email, phone, location, linkedin, github, website,
                 work_history_json, education_json, skills_json, raw_text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                name               = excluded.name,
                email              = excluded.email,
                phone              = excluded.phone,
                location           = excluded.location,
                linkedin           = excluded.linkedin,
                github             = excluded.github,
                website            = excluded.website,
                work_history_json  = excluded.work_history_json,
                education_json     = excluded.education_json,
                skills_json        = excluded.skills_json,
                raw_text           = excluded.raw_text,
                updated_at         = excluded.updated_at
        """, (
            profile_id,
            fields.get("name", ""),
            fields.get("email", ""),
            fields.get("phone", ""),
            fields.get("location", ""),
            fields.get("linkedin", ""),
            fields.get("github", ""),
            fields.get("website", ""),
            json.dumps(fields.get("work_history", [])),
            json.dumps(fields.get("education", [])),
            json.dumps(fields.get("skills", [])),
            fields.get("raw_text", ""),
            now,
        ))
        self._con.commit()

    def get_dossier(self, profile_id: int) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM applicant_dossier WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for key in ("work_history_json", "education_json", "skills_json"):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = []
        return d

    # ── S3 #336 — Shortlist scoring + approval ─────────────────────────────

    def set_score(self, url: str, score: float) -> None:
        self._con.execute(
            "UPDATE job_postings SET fit_score = ? WHERE url = ?", (score, url)
        )
        self._con.commit()

    def set_status(self, url: str, status: str) -> None:
        self._con.execute(
            "UPDATE job_postings SET status = ? WHERE url = ?", (status, url)
        )
        self._con.commit()

    def list_unscored(self) -> list[dict]:
        """Return new postings that have not been scored yet."""
        cur = self._con.execute(
            "SELECT * FROM job_postings WHERE fit_score IS NULL AND status = 'new'"
        )
        return [dict(row) for row in cur.fetchall()]

    def list_shortlist(self) -> list[dict]:
        """Return scored, non-rejected postings ranked by fit_score DESC."""
        cur = self._con.execute("""
            SELECT * FROM job_postings
            WHERE fit_score IS NOT NULL AND status != 'rejected'
            ORDER BY fit_score DESC, id DESC
        """)
        return [dict(row) for row in cur.fetchall()]


# ── Module-level seams ────────────────────────────────────────────────────────

_navigate_fn: Callable[[str], Awaitable[str]] | None = None
_store: JobSearchStore | None = None
_active_profile_id: int | None = None
_pending_resume_path: str = ""
_extract_fn: Callable[[str], Any] | None = None  # sync or async (str) -> dict
_score_fn: Callable[[dict, dict], Any] | None = None  # sync or async (posting, dossier) -> float


def set_navigate_fn(fn: Callable[[str], Awaitable[str]]) -> None:
    global _navigate_fn
    _navigate_fn = fn


def set_store(store: "JobSearchStore") -> None:
    global _store
    _store = store


def set_active_profile_id(profile_id: int | None) -> None:
    global _active_profile_id
    _active_profile_id = profile_id


def set_pending_resume_path(path: str) -> None:
    """Called by Cerebral when a PDF attachment is uploaded so the tool knows where it was stored."""
    global _pending_resume_path
    _pending_resume_path = path or ""


def set_extract_fn(fn: Callable[[str], Any]) -> None:
    """Inject the LLM extractor (sync or async fn(text) -> dict)."""
    global _extract_fn
    _extract_fn = fn


def set_score_fn(fn: Callable[[dict, dict], Any]) -> None:
    """Inject the LLM fit-scorer (sync or async fn(posting, dossier) -> float). S3 #336."""
    global _score_fn
    _score_fn = fn


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
        extract_fn: Callable[[str], Any] | None = None,
        score_fn: Callable[[dict, dict], Any] | None = None,
    ) -> None:
        self._navigate = navigate_fn or _default_navigate
        self._store = store
        self._extract_fn = extract_fn  # overrides module-level _extract_fn when set
        self._score_fn = score_fn       # overrides module-level _score_fn when set

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
            Tool(
                name="jobs_store_resume",
                description=(
                    "Store the user's resume PDF as their Resume artifact and parse it into "
                    "a structured Applicant dossier (name, contact, work history, education, "
                    "links). Call this when the user says 'store this as my resume' or "
                    "similar upload intent. Pass the full extracted text from the PDF."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "pdf_text": {
                            "type": "string",
                            "description": "The full text extracted from the uploaded resume PDF.",
                        },
                    },
                    "required": ["pdf_text"],
                },
            ),
            Tool(
                name="jobs_score_shortlist",
                description=(
                    "Score new Job postings for fit against the Applicant dossier and "
                    "AI/tech+IT targeting. Returns a ranked Shortlist for user approval. "
                    "Run after fetching new postings."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="jobs_set_approval",
                description=(
                    "Approve or reject a Job posting from the Shortlist. "
                    "Approved entries move to shortlisted status (input for the apply step); "
                    "rejected entries are skipped and not re-surfaced."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "ATS URL of the Job posting.",
                        },
                        "approved": {
                            "type": "boolean",
                            "description": "True to approve (shortlisted), false to reject.",
                        },
                    },
                    "required": ["url", "approved"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "jobs_fetch_postings":
            return await self._fetch_postings()
        if tool_name == "jobs_store_resume":
            return await self._store_resume(args.get("pdf_text", ""))
        if tool_name == "jobs_score_shortlist":
            return await self._score_shortlist()
        if tool_name == "jobs_set_approval":
            return await self._set_approval(args.get("url", ""), bool(args.get("approved")))
        return ToolResult(content=f"Unknown tool: {tool_name!r}", is_error=True)

    async def _score_shortlist(self) -> ToolResult:
        """S3 #336 — score unscored postings via injected scorer, return ranked shortlist."""
        import asyncio
        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)
        profile_id = _active_profile_id
        if profile_id is None:
            return ToolResult(content="No active profile", is_error=True)
        dossier = store.get_dossier(profile_id)
        if not dossier:
            return ToolResult(
                content="No Applicant dossier found — store your resume first", is_error=True
            )
        score = self._score_fn or _score_fn
        if score is None:
            return ToolResult(content="No scorer configured", is_error=True)
        unscored = store.list_unscored()
        for posting in unscored:
            try:
                result = score(posting, dossier)
                if asyncio.iscoroutine(result):
                    val = float(await result)
                else:
                    val = float(result)
                store.set_score(posting["url"], val)
            except Exception as exc:
                logger.error("[job_search] scoring failed for %s: %s", posting.get("url"), exc)
        shortlist = store.list_shortlist()
        return ToolResult(content=json.dumps({"scored": len(unscored), "shortlist": shortlist}))

    async def _set_approval(self, url: str, approved: bool) -> ToolResult:
        """S3 #336 — approve or reject a shortlist entry."""
        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)
        if not url:
            return ToolResult(content="url is required", is_error=True)
        status = "shortlisted" if approved else "rejected"
        store.set_status(url, status)
        shortlist = store.list_shortlist()
        return ToolResult(content=json.dumps({"url": url, "status": status, "shortlist": shortlist}))

    async def _store_resume(self, pdf_text: str) -> ToolResult:
        """S2 #335 — persist Resume artifact + extract Applicant dossier."""
        import asyncio
        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)
        profile_id = _active_profile_id
        if profile_id is None:
            return ToolResult(content="No active profile", is_error=True)
        if not pdf_text.strip():
            return ToolResult(content="pdf_text is empty", is_error=True)

        # Record the pending resume path (set by Cerebral when a PDF was uploaded)
        pdf_path = _pending_resume_path
        store.upsert_resume_artifact(profile_id, pdf_path)

        # Extract structured dossier fields
        extract = self._extract_fn or _extract_fn
        if extract is None:
            fields: dict = {}
        else:
            try:
                result = extract(pdf_text)
                if asyncio.iscoroutine(result):
                    fields = await result
                else:
                    fields = result
                if not isinstance(fields, dict):
                    fields = {}
            except Exception as exc:
                logger.error("[job_search] dossier extraction failed: %s", exc)
                return ToolResult(content=f"Dossier extraction failed: {exc}", is_error=True)

        fields["raw_text"] = pdf_text[:10_000]
        store.upsert_dossier(profile_id, fields)

        dossier = store.get_dossier(profile_id)
        return ToolResult(content=json.dumps({"status": "stored", "dossier": dossier}))

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
    extract_fn: "Callable[[str], Any] | None" = None,
    score_fn: "Callable[[dict, dict], Any] | None" = None,
) -> JobSearchPlugin:
    return JobSearchPlugin(navigate_fn=navigate_fn, store=store, extract_fn=extract_fn, score_fn=score_fn)
