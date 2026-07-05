"""
Job Search MCP plugin — Issues #334 (S1) + #335 (S2) + #336 (S3) + #337 (S4) + #338 (S5)
                         + #339 (S6) + #340 (S7).

Tools: jobs_fetch_postings, jobs_store_resume, jobs_score_shortlist,
       jobs_set_approval, jobs_apply_start, jobs_apply_submit, jobs_answer_field,
       jobs_set_auto_submit.

S1: Reads Rat Race Rebellion job board logged-out via OpenClaw navigate/Readability.
    Parses Job postings and upserts into SQLite job_postings table.

S2: Accepts a resume PDF text + path, persists the Resume artifact path per-profile,
    extracts structured Applicant dossier fields via injectable extract_fn (LLM in
    prod, stub in tests), and upserts into SQLite resume_artifacts + applicant_dossier.

S3: Scores new Job postings for fit via injectable score_fn (LLM in prod, stub in tests),
    persists fit_score + status on job_postings, and returns a ranked Shortlist.
    User approve/reject transitions: shortlisted | rejected.

S4 (ADR-0009): Apply spine — navigate to ATS URL, detect supported ATS (Greenhouse/
    Lever), generic-fill form from dossier (via injectable apply_driver_fn), upload
    resume, check for unfilled required fields, stop for user review, then submit
    (irreversible=True, ADR-0005 modal). Applications are logged in SQLite
    (URL-deduped). Unsupported ATS -> bail + mark failed.

S5 (Answer bank): Unknown required field -> try semantic recall from Answer bank
    (via injectable recall_fn) before escalating to user. If no match, notify and
    wait. User provides answer via jobs_answer_field -> stored in SQLite answer_bank
    table, indexed in ChromaDB (via injectable index_answer_fn). Next apply for a
    semantically-equivalent field auto-fills from the bank (Known value).

S6 (Account-creation + email verification): When apply_driver_fn signals
    {"needs_login": True}, Felix creates an ATS account using the jobs-email
    Connected account (via injectable get_jobs_email_fn), a Felix-generated password
    (gen_password_fn), stores it in the keyring (store_ats_password_fn), reads the
    jobs inbox for the verification link (read_verify_link_fn), and clicks it
    (click_verify_link_fn). Control then returns to the apply flow logged in.
    ATS accounts are persisted in SQLite ats_accounts table. All seams are injectable
    for fake-only testing; no real account creation or email in tests.

S7 (Gated auto-submit — ADR-0009 exception): Supervised ramp + opt-in auto-submit.
    Per-profile reviewed_count tracks modal-confirmed submissions (the ramp). Once
    opted-in AND ramp complete AND zero-guessed AND no new eligibility question,
    jobs_apply_submit bypasses the ADR-0005 modal (auto-approves via ModalSurface
    auto_gate_fn). Every other case routes through the modal as before. Eligibility/
    knockout questions (work auth, sponsorship, relocation, start date, salary) always
    escalate on first encounter. Settings in SQLite job_search_settings table.

All network I/O is injected via navigate_fn.
All DB I/O is injectable via a JobSearchStore seam.
LLM extraction is injectable via set_extract_fn.
LLM fit-scoring is injectable via set_score_fn.
Browser driving + LLM field-mapping is injectable via set_apply_driver_fn.
Submit action is injectable via set_apply_submit_fn.
Answer bank recall is injectable via set_recall_fn.
Answer bank ChromaDB indexing is injectable via set_index_answer_fn.
S6 account/email seams: set_gen_password_fn, set_create_ats_account_fn,
    set_get_jobs_email_fn, set_store_ats_password_fn, set_read_verify_link_fn,
    set_click_verify_link_fn.
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
# Issue #337 (S4) — jobs_apply_submit: external_data_write (submitting to ATS is a write).
# Issue #339 (S6) — ATS account passwords: secrets_read (per ADR-0009).
# network_egress_cloud covers cloud-LLM extraction + field-mapping; llm_call is not in the 16-class vocabulary.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
    "fs_write",
    "fs_read",
    "secrets_read",  # S6 #339: reading ATS account passwords from keyring (ADR-0009)
})

# ATSes Felix can drive generically (guest-apply, clean DOM form).
SUPPORTED_ATS_TYPES: frozenset[str] = frozenset({"greenhouse", "lever"})

_DB_PATH = Path(__file__).parent.parent / "cerebral" / "data" / "openmind.db"

# S7 #340 — Eligibility/knockout question keywords (ADR-0009).
# Any ATS field whose label matches one of these always escalates on first encounter
# (i.e., when is_known=False). Once in the Answer bank (is_known=True) it flows normally.
ELIGIBILITY_KEYWORDS: frozenset[str] = frozenset({
    "work authorization", "authorized to work", "legally authorized",
    "visa", "sponsorship", "require sponsorship",
    "work in the us", "eligible to work",
    "relocation", "relocate", "willing to relocate",
    "start date", "available to start", "earliest start",
    "salary", "salary expectation", "desired salary", "pay expectation",
    "compensation", "expected compensation",
})


def is_eligibility_question(label: str) -> bool:
    """True if the field label matches an eligibility/knockout keyword (ADR-0009)."""
    lower = label.lower()
    return any(kw in lower for kw in ELIGIBILITY_KEYWORDS)


def check_auto_submit_gate(
    profile_id: int,
    store: "JobSearchStore",
    pending_app: dict,
) -> tuple[bool, str]:
    """Return (can_auto_submit, reason).

    can_auto_submit is True ONLY when all ADR-0009 gate conditions hold:
    1. auto_submit_enabled for this profile (opt-in)
    2. reviewed_count >= ramp_threshold (supervised ramp complete)
    3. every filled field is a Known value (is_known=True or not set)
    4. no eligibility/knockout question was newly encountered (is_known=False)
    """
    settings = store.get_job_settings(profile_id)
    if not settings.get("auto_submit_enabled"):
        return False, "opted-out"
    if settings["reviewed_count"] < settings["ramp_threshold"]:
        return False, f"pre-ramp ({settings['reviewed_count']}/{settings['ramp_threshold']})"
    fields = pending_app.get("fields", [])
    # Eligibility check first — ADR-0009: always escalate on first encounter
    for f in fields:
        if is_eligibility_question(f.get("label", "")) and not f.get("is_known", True):
            return False, f"new eligibility: {f.get('label', '?')}"
    # Zero-guessed: every field with a value must be a Known value
    for f in fields:
        if f.get("value") and not f.get("is_known", True):
            return False, f"guessed field: {f.get('label', '?')}"
    return True, "ok"


# ── ATS detection ─────────────────────────────────────────────────────────────

def detect_ats_type(url: str) -> str:
    """Classify the ATS from the outbound URL hostname. Returns 'unknown' if unsupported."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
    except Exception:
        return "unknown"
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    return "unknown"


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


# Live RRR listing-page format (2026 — issue #380): repeating labeled entries
#   <p><strong>Company</strong>: {company}<!-- rrr-job-id: RRR-YYYYMMDD-NNN --><br>
#   <strong>Job/Gig</strong>: <a href="{rrr_article_url}">{title}</a>…<br>
#   <strong>Pay</strong>: {pay}</p>
#   <p><strong>Snapshot</strong>: {snapshot}</p>
# The only link on the listing page is the per-job RRR article; the employer/ATS
# link lives on that article page. ``url`` therefore carries the article URL
# (unique dedup key); apply-time resolution of the outbound link is follow-up work.
_LABELED_ENTRY_RE = re.compile(
    r"<p><strong>Company</strong>:\s*(?P<company>[^<]*?)\s*"
    r"(?:<!--\s*rrr-job-id:\s*(?P<jid>[\w-]+)\s*-->)?\s*<br\s*/?>\s*"
    r"<strong>Job/Gig</strong>:\s*<a\s+href=\"(?P<url>[^\"]+)\"[^>]*>(?P<title>.*?)</a>"
    r"(?P<rest>.*?)</p>"
    r"(?:\s*<p><strong>Snapshot</strong>:\s*(?P<snapshot>.*?)</p>)?",
    re.DOTALL | re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(fragment: str) -> str:
    import html as _html

    return _html.unescape(_TAG_RE.sub("", fragment or "")).strip()


def _parse_labeled_postings(html: str) -> list[dict]:
    results: list[dict] = []
    for m in _LABELED_ENTRY_RE.finditer(html):
        title = _plain(m.group("title"))
        if not title:
            continue
        pay = ""
        pay_m = re.search(
            r"<strong>Pay</strong>:\s*(.*?)$", m.group("rest") or "",
            re.DOTALL | re.IGNORECASE,
        )
        if pay_m:
            pay = _plain(pay_m.group(1))
        posted = ""
        jid = m.group("jid") or ""
        date_m = re.match(r"RRR-(\d{4})(\d{2})(\d{2})-", jid)
        if date_m:
            posted = "-".join(date_m.groups())
        results.append({
            "title": title,
            "company": _plain(m.group("company")),
            "pay": pay,
            "snapshot": _plain(m.group("snapshot") or "")[:400],
            "posted_date": posted,
            "url": m.group("url"),
        })
    return results


def parse_postings(html: str) -> list[dict]:
    """Parse the RRR job-board HTML, return list of posting dicts.

    Tries the live labeled-entry format first (what the real page serves —
    issue #380); falls back to the legacy Readability heading parser the S1
    fixtures use.
    """
    labeled = _parse_labeled_postings(html)
    if labeled:
        return labeled
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
        # S4 #337 — Applications: one row per outbound ATS URL (dedup key).
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                url                 TEXT    UNIQUE NOT NULL,
                posting_url         TEXT    NOT NULL DEFAULT '',
                ats_type            TEXT    NOT NULL DEFAULT '',
                status              TEXT    NOT NULL DEFAULT 'awaiting-input',
                filled_fields_json  TEXT    NOT NULL DEFAULT '[]',
                submitted_at        TEXT,
                created_at          TEXT    NOT NULL
            )
        """)
        # S5 #338 — Answer bank: ATS question -> user answer, per-profile.
        # SQLite is source of truth; ChromaDB holds the semantic index (via index_answer_fn).
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS answer_bank (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id  INTEGER NOT NULL,
                question    TEXT    NOT NULL,
                answer      TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL,
                UNIQUE(profile_id, question)
            )
        """)
        # S6 #339 — ATS accounts: per-profile per-provider account created by Felix.
        # Password is in the keyring (store_ats_password_fn); only non-secret metadata here.
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS ats_accounts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id   INTEGER NOT NULL,
                ats_provider TEXT    NOT NULL,
                email        TEXT    NOT NULL DEFAULT '',
                status       TEXT    NOT NULL DEFAULT 'created',
                created_at   TEXT    NOT NULL,
                UNIQUE(profile_id, ats_provider)
            )
        """)
        # S7 #340 — Per-profile auto-submit settings (ADR-0009 gated exception).
        # auto_submit_enabled: user opted in (default OFF).
        # reviewed_count: number of modal-confirmed submissions (the supervised ramp).
        # ramp_threshold: how many reviews before auto-submit is allowed (default 5).
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS job_search_settings (
                profile_id          INTEGER PRIMARY KEY,
                auto_submit_enabled INTEGER NOT NULL DEFAULT 0,
                reviewed_count      INTEGER NOT NULL DEFAULT 0,
                ramp_threshold      INTEGER NOT NULL DEFAULT 5
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

    def get_posting_by_url(self, url: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM job_postings WHERE url = ?", (url,)
        ).fetchone()
        return dict(row) if row else None

    # ── S4 #337 — Applications ─────────────────────────────────────────────

    def upsert_application(
        self, url: str, posting_url: str, ats_type: str,
        status: str, fields: list,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute("""
            INSERT INTO applications
                (url, posting_url, ats_type, status, filled_fields_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                posting_url         = excluded.posting_url,
                ats_type            = excluded.ats_type,
                status              = excluded.status,
                filled_fields_json  = excluded.filled_fields_json
        """, (url, posting_url, ats_type, status, json.dumps(fields), now))
        self._con.commit()

    def set_application_status(self, url: str, status: str, submitted_at: str | None = None) -> None:
        if submitted_at:
            self._con.execute(
                "UPDATE applications SET status = ?, submitted_at = ? WHERE url = ?",
                (status, submitted_at, url),
            )
        else:
            self._con.execute(
                "UPDATE applications SET status = ? WHERE url = ?", (status, url)
            )
        self._con.commit()

    def get_application(self, url: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM applications WHERE url = ?", (url,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["filled_fields_json"] = json.loads(d["filled_fields_json"])
        except (json.JSONDecodeError, TypeError):
            d["filled_fields_json"] = []
        return d

    def list_applications(self) -> list[dict]:
        cur = self._con.execute(
            "SELECT * FROM applications ORDER BY created_at DESC, id DESC"
        )
        rows = []
        for row in cur.fetchall():
            d = dict(row)
            try:
                d["filled_fields_json"] = json.loads(d["filled_fields_json"])
            except (json.JSONDecodeError, TypeError):
                d["filled_fields_json"] = []
            rows.append(d)
        return rows

    # ── S5 #338 — Answer bank ──────────────────────────────────────────────

    def upsert_answer(self, profile_id: int, question: str, answer: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute("""
            INSERT INTO answer_bank (profile_id, question, answer, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id, question) DO UPDATE SET answer = excluded.answer
        """, (profile_id, question, answer, now))
        self._con.commit()

    def list_answers(self, profile_id: int) -> list[dict]:
        cur = self._con.execute(
            "SELECT question, answer, created_at FROM answer_bank"
            " WHERE profile_id = ? ORDER BY created_at DESC",
            (profile_id,),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── S6 #339 — ATS accounts ─────────────────────────────────────────────

    def upsert_ats_account(self, profile_id: int, ats_provider: str, email: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute("""
            INSERT INTO ats_accounts (profile_id, ats_provider, email, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, ats_provider) DO UPDATE SET
                email  = excluded.email,
                status = excluded.status
        """, (profile_id, ats_provider, email, status, now))
        self._con.commit()

    def get_ats_account(self, profile_id: int, ats_provider: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM ats_accounts WHERE profile_id = ? AND ats_provider = ?",
            (profile_id, ats_provider),
        ).fetchone()
        return dict(row) if row else None

    def list_ats_accounts(self, profile_id: int) -> list[dict]:
        cur = self._con.execute(
            "SELECT * FROM ats_accounts WHERE profile_id = ? ORDER BY created_at DESC",
            (profile_id,),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── S7 #340 — Auto-submit settings ────────────────────────────────────

    def get_job_settings(self, profile_id: int) -> dict:
        """Return auto-submit settings for profile_id (defaults if row absent)."""
        row = self._con.execute(
            "SELECT * FROM job_search_settings WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {
            "profile_id": profile_id,
            "auto_submit_enabled": 0,
            "reviewed_count": 0,
            "ramp_threshold": 5,
        }

    def set_auto_submit(self, profile_id: int, enabled: bool) -> None:
        self._con.execute("""
            INSERT INTO job_search_settings (profile_id, auto_submit_enabled)
            VALUES (?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET auto_submit_enabled = excluded.auto_submit_enabled
        """, (profile_id, int(enabled)))
        self._con.commit()

    def increment_reviewed_count(self, profile_id: int) -> int:
        """Increment reviewed_count for profile, returning the new value."""
        self._con.execute("""
            INSERT INTO job_search_settings (profile_id, reviewed_count)
            VALUES (?, 1)
            ON CONFLICT(profile_id) DO UPDATE SET reviewed_count = reviewed_count + 1
        """, (profile_id,))
        self._con.commit()
        return self._con.execute(
            "SELECT reviewed_count FROM job_search_settings WHERE profile_id = ?", (profile_id,)
        ).fetchone()[0]


# ── Module-level seams ────────────────────────────────────────────────────────

_navigate_fn: Callable[[str], Awaitable[str]] | None = None
_store: JobSearchStore | None = None
_active_profile_id: int | None = None
_pending_resume_path: str = ""
_extract_fn: Callable[[str], Any] | None = None  # sync or async (str) -> dict
_score_fn: Callable[[dict, dict], Any] | None = None  # sync or async (posting, dossier) -> float

# S4 #337 — injectable apply driver (browser nav + LLM field-mapping + form fill + upload).
# async (posting_url: str, dossier: dict, resume_path: str) -> dict:
#   {"fields": [{selector, label, value, required, is_file_upload}], "submit_selector": str}
# Side effect in prod: navigates to URL, fills form fields, uploads resume, leaves form open.
_apply_driver_fn: Callable | None = None

# S4 #337 — injectable submit action (clicks the submit button on the open page).
# async () -> None
_apply_submit_fn: Callable | None = None

# S4 #337 — pending application held between jobs_apply_start and jobs_apply_submit.
_pending_application: dict | None = None

# S5 #338 — Answer bank seams (both injectable so tests need no live ChromaDB/model).
# async (profile_id: int, question: str) -> str | None  (None = no match / below threshold)
_recall_fn: Callable | None = None
# async (profile_id: int, question: str, answer: str) -> None
_index_answer_fn: Callable | None = None

# S6 #339 — Account-creation + email verification seams (all injectable; tests use fakes).
# () -> str — generate a random password for a new ATS account
_gen_password_fn: Callable | None = None
# async (ats_url: str, email: str, password: str) -> bool — create account on the ATS
_create_ats_account_fn: Callable | None = None
# async (profile_id: int) -> str | None — return the jobs email address
_get_jobs_email_fn: Callable | None = None
# async (profile_id: int, ats_provider: str, email: str, password: str) -> None
_store_ats_password_fn: Callable | None = None
# async (profile_id: int) -> str | None — read jobs inbox, return verification URL
_read_verify_link_fn: Callable | None = None
# async (verify_url: str) -> bool — navigate to the verification link
_click_verify_link_fn: Callable | None = None


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


def set_apply_driver_fn(fn: Callable) -> None:
    """Inject the apply driver (async fn(url, dossier, resume_path) -> draft). S4 #337."""
    global _apply_driver_fn
    _apply_driver_fn = fn


def set_apply_submit_fn(fn: Callable) -> None:
    """Inject the submit action (async fn() -> None). S4 #337."""
    global _apply_submit_fn
    _apply_submit_fn = fn


def set_recall_fn(fn: Callable) -> None:
    """Inject Answer bank recall (async fn(profile_id, question) -> str|None). S5 #338."""
    global _recall_fn
    _recall_fn = fn


def set_index_answer_fn(fn: Callable) -> None:
    """Inject ChromaDB indexer (async fn(profile_id, question, answer) -> None). S5 #338."""
    global _index_answer_fn
    _index_answer_fn = fn


# S6 #339 setters ─────────────────────────────────────────────────────────────

def set_gen_password_fn(fn: Callable) -> None:
    """Inject password generator (() -> str). S6 #339."""
    global _gen_password_fn
    _gen_password_fn = fn


def set_create_ats_account_fn(fn: Callable) -> None:
    """Inject ATS account creator (async fn(ats_url, email, password) -> bool). S6 #339."""
    global _create_ats_account_fn
    _create_ats_account_fn = fn


def set_get_jobs_email_fn(fn: Callable) -> None:
    """Inject jobs-email getter (async fn(profile_id) -> str | None). S6 #339."""
    global _get_jobs_email_fn
    _get_jobs_email_fn = fn


def set_store_ats_password_fn(fn: Callable) -> None:
    """Inject keyring password writer (async fn(profile_id, ats_provider, email, password) -> None). S6 #339."""
    global _store_ats_password_fn
    _store_ats_password_fn = fn


def set_read_verify_link_fn(fn: Callable) -> None:
    """Inject jobs-inbox verifier (async fn(profile_id) -> str | None). S6 #339."""
    global _read_verify_link_fn
    _read_verify_link_fn = fn


def set_click_verify_link_fn(fn: Callable) -> None:
    """Inject link-clicker (async fn(verify_url) -> bool). S6 #339."""
    global _click_verify_link_fn
    _click_verify_link_fn = fn


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
        apply_driver_fn: Callable | None = None,
        apply_submit_fn: Callable | None = None,
        recall_fn: Callable | None = None,
        index_answer_fn: Callable | None = None,
        # S6 #339 — account-creation + email-verification seams
        gen_password_fn: Callable | None = None,
        create_ats_account_fn: Callable | None = None,
        get_jobs_email_fn: Callable | None = None,
        store_ats_password_fn: Callable | None = None,
        read_verify_link_fn: Callable | None = None,
        click_verify_link_fn: Callable | None = None,
    ) -> None:
        self._navigate = navigate_fn or _default_navigate
        self._store = store
        self._extract_fn = extract_fn
        self._score_fn = score_fn
        self._apply_driver_fn = apply_driver_fn
        self._apply_submit_fn = apply_submit_fn
        self._recall_fn = recall_fn          # S5: Answer bank semantic recall
        self._index_answer_fn = index_answer_fn  # S5: ChromaDB indexer
        # S6 #339
        self._gen_password_fn = gen_password_fn
        self._create_ats_account_fn = create_ats_account_fn
        self._get_jobs_email_fn = get_jobs_email_fn
        self._store_ats_password_fn = store_ats_password_fn
        self._read_verify_link_fn = read_verify_link_fn
        self._click_verify_link_fn = click_verify_link_fn

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
            Tool(
                name="jobs_apply_start",
                description=(
                    "Start an application for a shortlisted Job posting. Opens the "
                    "ATS URL, detects the ATS type (Greenhouse/Lever), fills form "
                    "fields from the Applicant dossier, uploads the Resume artifact, "
                    "and stops for user review before submit. Returns a review payload "
                    "or an error if a required field has no known value or the ATS is "
                    "unsupported (which marks the application as failed)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "ATS URL of the shortlisted Job posting to apply to.",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="jobs_apply_submit",
                description=(
                    "Submit the pending application after user review. "
                    "IRREVERSIBLE — routes through the ADR-0005 modal for explicit "
                    "confirmation before submitting. Logs the Application as submitted "
                    "and updates the Job Search panel. Call only after jobs_apply_start "
                    "has returned a ready_to_submit review payload."
                ),
                plugin=PLUGIN_NAME,
                irreversible=True,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="jobs_answer_field",
                description=(
                    "Store an answer to an ATS question in the Answer bank. "
                    "Call this after jobs_apply_start reports a missing required field. "
                    "The answer is stored in SQLite and indexed semantically in ChromaDB "
                    "so future forms with the same or similar question are auto-filled "
                    "(Known value, zero-guessed rule — ADR-0009)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The field label or question from the ATS form.",
                        },
                        "answer": {
                            "type": "string",
                            "description": "The user's answer to store.",
                        },
                    },
                    "required": ["question", "answer"],
                },
            ),
            Tool(
                name="jobs_set_auto_submit",
                description=(
                    "Enable or disable auto-submit for the active profile (ADR-0009). "
                    "Auto-submit allows jobs_apply_submit to bypass the irreversible modal "
                    "ONLY when: opted-in AND supervised ramp complete AND zero-guessed AND "
                    "no new eligibility question. Default is OFF (review-before-submit). "
                    "Call this when the user explicitly asks to enable or disable auto-submit."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "enabled": {
                            "type": "boolean",
                            "description": "True to enable auto-submit, false to disable.",
                        },
                    },
                    "required": ["enabled"],
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
        if tool_name == "jobs_apply_start":
            return await self._apply_start(args.get("url", ""))
        if tool_name == "jobs_apply_submit":
            return await self._apply_submit()
        if tool_name == "jobs_answer_field":
            return await self._answer_field(args.get("question", ""), args.get("answer", ""))
        if tool_name == "jobs_set_auto_submit":
            return await self._set_auto_submit(bool(args.get("enabled")))
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

    async def _apply_start(self, url: str) -> ToolResult:
        """S4 #337 — open ATS, fill form, upload resume, stop for review."""
        global _pending_application
        import asyncio
        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)
        if not url:
            return ToolResult(content="url is required", is_error=True)
        profile_id = _active_profile_id
        if profile_id is None:
            return ToolResult(content="No active profile", is_error=True)

        # Verify posting is shortlisted
        posting = store.get_posting_by_url(url)
        if posting is None or posting.get("status") != "shortlisted":
            return ToolResult(
                content="Posting not found or not shortlisted — approve it first",
                is_error=True,
            )

        dossier = store.get_dossier(profile_id)
        if not dossier:
            return ToolResult(
                content="No Applicant dossier — store your resume first", is_error=True
            )

        resume = store.get_resume_artifact(profile_id)
        resume_path = (resume or {}).get("pdf_path", "")

        # Detect ATS type from URL; bail immediately if unsupported
        ats_type = detect_ats_type(url)
        if ats_type not in SUPPORTED_ATS_TYPES:
            store.upsert_application(
                url=url, posting_url=url, ats_type=ats_type,
                status="failed", fields=[],
            )
            return ToolResult(
                content=json.dumps({
                    "status": "failed",
                    "reason": f"Unsupported ATS '{ats_type}' — Felix cannot reliably drive this site",
                    "url": url,
                }),
                is_error=True,
            )

        # Drive browser: navigate, map fields, fill, upload (all injected for tests)
        driver = self._apply_driver_fn or _apply_driver_fn
        if driver is None:
            return ToolResult(content="Apply driver not configured", is_error=True)

        try:
            result = driver(url, dossier, resume_path)
            if asyncio.iscoroutine(result):
                draft = await result
            else:
                draft = result
        except Exception as exc:
            logger.error("[job_search] apply_driver failed for %s: %s", url, exc)
            store.upsert_application(
                url=url, posting_url=url, ats_type=ats_type,
                status="failed", fields=[],
            )
            return ToolResult(content=f"Apply driver error: {exc}", is_error=True)

        # S6 #339: ATS requires login -> create account + verify email, then retry driver.
        if draft.get("needs_login"):
            ats_prov = draft.get("ats_provider") or ats_type
            login_ok = await self._ensure_ats_login(profile_id, ats_prov, url)
            if not login_ok:
                store.upsert_application(
                    url=url, posting_url=url, ats_type=ats_type,
                    status="failed", fields=[],
                )
                return ToolResult(
                    content=json.dumps({
                        "status": "failed",
                        "reason": "ATS login/account-creation failed — check jobs email credential",
                        "url": url,
                    }),
                    is_error=True,
                )
            # Retry driver now logged in
            try:
                result2 = driver(url, dossier, resume_path)
                if asyncio.iscoroutine(result2):
                    draft = await result2
                else:
                    draft = result2
            except Exception as exc:
                logger.error("[job_search] apply_driver (post-login) failed for %s: %s", url, exc)
                store.upsert_application(url=url, posting_url=url, ats_type=ats_type, status="failed", fields=[])
                return ToolResult(content=f"Apply driver (post-login) error: {exc}", is_error=True)
            if draft.get("needs_login"):
                store.upsert_application(url=url, posting_url=url, ats_type=ats_type, status="failed", fields=[])
                return ToolResult(
                    content=json.dumps({
                        "status": "failed",
                        "reason": "ATS still requires login after account verification",
                        "url": url,
                    }),
                    is_error=True,
                )

        # Copy field dicts so we can mutate values without touching driver-stub constants.
        # S7: is_known defaults to True for dossier-filled fields; set to False for guesses.
        fields = [dict(f) for f in draft.get("fields", [])]
        for f in fields:
            if "is_known" not in f:
                f["is_known"] = bool(f.get("value"))  # driver-filled = treat as known

        # S5 #338: try Answer bank semantic recall for required fields the dossier didn't fill.
        recall = self._recall_fn or _recall_fn
        if recall:
            for f in fields:
                if f.get("required") and not f.get("value"):
                    try:
                        recalled = recall(profile_id, f.get("label", ""))
                        if asyncio.iscoroutine(recalled):
                            recalled = await recalled
                        if recalled is not None:
                            f["value"] = recalled   # Known value from Answer bank
                            f["is_known"] = True    # S7: explicitly Known (from bank)
                    except Exception as exc:
                        logger.warning(
                            "[job_search] recall failed for %r: %s", f.get("label"), exc
                        )

        # Zero-guessed rule: any required field still without a Known value -> escalate.
        missing = [f for f in fields if f.get("required") and not f.get("value")]
        if missing:
            store.upsert_application(
                url=url, posting_url=url, ats_type=ats_type,
                status="awaiting-input", fields=fields,
            )
            return ToolResult(
                content=json.dumps({
                    "status": "awaiting-input",
                    "url": url,
                    "missing_fields": [f.get("label", f.get("selector", "?")) for f in missing],
                }),
                is_error=True,
            )

        # S7 #340: detect newly-encountered eligibility questions (is_known=False).
        has_new_eligibility = any(
            is_eligibility_question(f.get("label", "")) and not f.get("is_known", True)
            for f in fields
        )

        # Form is filled (by the driver side-effect); store pending for submit
        _pending_application = {
            "url": url,
            "ats_type": ats_type,
            "fields": fields,
            "submit_selector": draft.get("submit_selector", 'button[type="submit"]'),
            "has_new_eligibility": has_new_eligibility,  # S7: gate condition 4
        }
        store.upsert_application(
            url=url, posting_url=url, ats_type=ats_type,
            status="shortlisted", fields=fields,
        )

        return ToolResult(content=json.dumps({
            "status": "ready_to_submit",
            "url": url,
            "ats_type": ats_type,
            "fields": fields,
        }))

    async def _answer_field(self, question: str, answer: str) -> ToolResult:
        """S5 #338 — capture user answer, store in Answer bank, index in ChromaDB."""
        import asyncio
        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)
        profile_id = _active_profile_id
        if profile_id is None:
            return ToolResult(content="No active profile", is_error=True)
        if not question.strip():
            return ToolResult(content="question is required", is_error=True)

        store.upsert_answer(profile_id, question, answer)

        indexer = self._index_answer_fn or _index_answer_fn
        if indexer:
            try:
                result = indexer(profile_id, question, answer)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("[job_search] index_answer failed for %r: %s", question, exc)

        return ToolResult(content=json.dumps({
            "status": "stored",
            "question": question,
            "answer": answer,
            "answer_bank": store.list_answers(profile_id),
        }))

    async def _ensure_ats_login(self, profile_id: int, ats_provider: str, ats_url: str) -> bool:
        """S6 #339 — create ATS account + verify email. Returns True when ready to apply logged in."""
        import asyncio
        store = self._store or _store

        # 1. Already verified — nothing to do.
        existing = store.get_ats_account(profile_id, ats_provider) if store else None
        if existing and existing["status"] == "verified":
            return True

        # 2. Get the jobs email address (Connected account, per-profile).
        get_email = self._get_jobs_email_fn or _get_jobs_email_fn
        if get_email is None:
            logger.warning("[job_search] no get_jobs_email_fn — cannot create ATS account")
            return False
        r = get_email(profile_id)
        jobs_email = (await r) if asyncio.iscoroutine(r) else r
        if not jobs_email:
            logger.warning("[job_search] jobs email not configured for profile %d", profile_id)
            return False

        # 3. Generate a random password for the new ATS account.
        import secrets as _secrets
        gen_pw = self._gen_password_fn or _gen_password_fn
        password = gen_pw() if gen_pw is not None else _secrets.token_urlsafe(16)

        # 4. Create account on the ATS (attended browser or fake in tests).
        create = self._create_ats_account_fn or _create_ats_account_fn
        if create is not None:
            r2 = create(ats_url, jobs_email, password)
            ok = (await r2) if asyncio.iscoroutine(r2) else r2
            if not ok:
                if store:
                    store.upsert_ats_account(profile_id, ats_provider, jobs_email, "failed")
                return False

        # 5. Persist account as 'created' + store password in keyring.
        if store:
            store.upsert_ats_account(profile_id, ats_provider, jobs_email, "created")
        store_pw = self._store_ats_password_fn or _store_ats_password_fn
        if store_pw is not None:
            r3 = store_pw(profile_id, ats_provider, jobs_email, password)
            if asyncio.iscoroutine(r3):
                await r3

        # 6. Read jobs inbox for the verification link.
        read_link = self._read_verify_link_fn or _read_verify_link_fn
        verify_url = None
        if read_link is not None:
            r4 = read_link(profile_id)
            verify_url = (await r4) if asyncio.iscoroutine(r4) else r4
        if not verify_url:
            logger.warning("[job_search] no verification link found in jobs inbox")
            return False

        # 7. Click the verification link to complete account activation.
        click = self._click_verify_link_fn or _click_verify_link_fn
        if click is not None:
            r5 = click(verify_url)
            click_ok = (await r5) if asyncio.iscoroutine(r5) else r5
            if not click_ok:
                if store:
                    store.upsert_ats_account(profile_id, ats_provider, jobs_email, "failed")
                return False

        if store:
            store.upsert_ats_account(profile_id, ats_provider, jobs_email, "verified")
        return True

    async def _set_auto_submit(self, enabled: bool) -> ToolResult:
        """S7 #340 — toggle auto-submit opt-in for the active profile (ADR-0009)."""
        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)
        profile_id = _active_profile_id
        if profile_id is None:
            return ToolResult(content="No active profile", is_error=True)
        store.set_auto_submit(profile_id, enabled)
        settings = store.get_job_settings(profile_id)
        return ToolResult(content=json.dumps({
            "status": "ok",
            "auto_submit_enabled": bool(settings["auto_submit_enabled"]),
            "reviewed_count": settings["reviewed_count"],
            "ramp_threshold": settings["ramp_threshold"],
        }))

    async def _apply_submit(self) -> ToolResult:
        """S4 #337 / S7 #340 — submit the pending application (irreversible=True, ADR-0009).

        S7: When the auto-submit gate passes, the ModalSurface auto-approves and this
        method runs unattended. When the gate fails, the modal was shown to the user
        (supervised ramp), so increment reviewed_count to track ramp progress.
        """
        global _pending_application
        import asyncio

        pending = _pending_application
        if not pending:
            return ToolResult(
                content="No pending application — call jobs_apply_start first",
                is_error=True,
            )

        store = self._store or _store
        if store is None:
            return ToolResult(content="Job search store not initialised", is_error=True)

        submitter = self._apply_submit_fn or _apply_submit_fn
        if submitter is None:
            return ToolResult(content="Apply submit action not configured", is_error=True)

        profile_id = _active_profile_id
        # S7 #340: check gate to determine whether this was a modal-confirmed or auto-submit.
        # Gate failing means the modal WAS shown (supervised ramp path) → increment count.
        # Gate passing means the modal was bypassed (auto-submit path) → don't increment.
        auto_gate_passed = False
        if profile_id is not None:
            try:
                auto_gate_passed, _ = check_auto_submit_gate(profile_id, store, pending)
            except Exception:
                auto_gate_passed = False

        url = pending["url"]
        try:
            result = submitter()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error("[job_search] apply_submit failed for %s: %s", url, exc)
            store.set_application_status(url, "failed")
            _pending_application = None
            return ToolResult(content=f"Submit failed: {exc}", is_error=True)

        submitted_at = datetime.now(timezone.utc).isoformat()
        store.set_application_status(url, "submitted", submitted_at=submitted_at)

        # S7: increment reviewed_count when modal was shown (gate failed = modal path).
        if profile_id is not None and not auto_gate_passed:
            store.increment_reviewed_count(profile_id)

        _pending_application = None

        apps = store.list_applications()
        settings = store.get_job_settings(profile_id) if profile_id is not None else {}
        return ToolResult(content=json.dumps({
            "status": "submitted",
            "url": url,
            "submitted_at": submitted_at,
            "auto_submitted": auto_gate_passed,
            "applications": apps,
            "job_settings": settings,
        }))

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

        # The stored PDF is ground truth; the pdf_text arg is an LLM-copied
        # (possibly truncated/paraphrased) rendition. Prefer whichever is
        # longer so a lazy model call still stores the full resume.
        if _pending_resume_path:
            try:
                from pathlib import Path

                from cerebral.db.attachments import _extract_pdf_text

                file_text = _extract_pdf_text(Path(_pending_resume_path).read_bytes())
                if len(file_text.strip()) > len(pdf_text.strip()):
                    pdf_text = file_text
            except Exception:
                pass  # unreadable/missing file -- fall back to the arg
        if not pdf_text.strip():
            return ToolResult(
                content="pdf_text is empty and no uploaded resume PDF was found",
                is_error=True,
            )

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
    navigate_fn: "Callable[[str], Awaitable[str]] | None" = None,
    store: "JobSearchStore | None" = None,
    extract_fn: "Callable[[str], Any] | None" = None,
    score_fn: "Callable[[dict, dict], Any] | None" = None,
    apply_driver_fn: "Callable | None" = None,
    apply_submit_fn: "Callable | None" = None,
    recall_fn: "Callable | None" = None,
    index_answer_fn: "Callable | None" = None,
    # S6 #339
    gen_password_fn: "Callable | None" = None,
    create_ats_account_fn: "Callable | None" = None,
    get_jobs_email_fn: "Callable | None" = None,
    store_ats_password_fn: "Callable | None" = None,
    read_verify_link_fn: "Callable | None" = None,
    click_verify_link_fn: "Callable | None" = None,
) -> JobSearchPlugin:
    return JobSearchPlugin(
        navigate_fn=navigate_fn, store=store,
        extract_fn=extract_fn, score_fn=score_fn,
        apply_driver_fn=apply_driver_fn, apply_submit_fn=apply_submit_fn,
        recall_fn=recall_fn, index_answer_fn=index_answer_fn,
        gen_password_fn=gen_password_fn,
        create_ats_account_fn=create_ats_account_fn,
        get_jobs_email_fn=get_jobs_email_fn,
        store_ats_password_fn=store_ats_password_fn,
        read_verify_link_fn=read_verify_link_fn,
        click_verify_link_fn=click_verify_link_fn,
    )
