"""
Job Search MCP plugin tests — Issue #334 (S1).

Tools: jobs_fetch_postings.

All network I/O is replaced by an injectable navigate_fn.
All DB I/O uses an in-memory SQLite store (JobSearchStore with :memory:).
No live network, no real ATS, no real submissions.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── RRR HTML fixture ──────────────────────────────────────────────────────────
#
# Minimal but realistic representation of what OpenClaw's navigate/Readability
# returns for ratracerebellion.com/job-postings. Two entries with distinct
# outbound ATS URLs. Covers: title, company (dash-sep), pay, snapshot, date.

RRR_FIXTURE_HTML = """
<div>
<h2><a href="https://ratracerebellion.com/post/senior-python-dev/">Senior Python Developer — Acme Corp</a></h2>
<p>Pay: $80,000 - $100,000/year. Fully remote. Acme Corp is looking for an
experienced Python developer to join their distributed team.</p>
<p><strong>APPLY:</strong> <a href="https://boards.greenhouse.io/acme/jobs/123">Apply at Greenhouse</a></p>
<time datetime="2026-07-01">July 1, 2026</time>

<h2><a href="https://ratracerebellion.com/post/data-analyst-beta/">Data Analyst — Beta Inc</a></h2>
<p>Pay: $65,000/year. Remote-first. Beta Inc seeks a data analyst
comfortable with SQL and Python to support the analytics team.</p>
<p><strong>APPLY:</strong> <a href="https://jobs.lever.co/beta/456">Apply at Lever</a></p>
<time datetime="2026-07-02">July 2, 2026</time>
</div>
"""

# Fixture with no outbound URLs — tests that entries without ATS links are skipped
RRR_FIXTURE_NO_LINKS = """
<div>
<h2><a href="https://ratracerebellion.com/post/mystery-job/">Mystery Job — Anon Co</a></h2>
<p>No apply link here.</p>
</div>
"""

# Duplicate URL fixture — same ATS URL appears twice
RRR_FIXTURE_DUPE = """
<div>
<h2><a href="https://ratracerebellion.com/post/job-a/">Job A — Corp A</a></h2>
<p>Pay: $50/hr.</p>
<p><a href="https://jobs.example.com/jobA">Apply</a></p>
<h2><a href="https://ratracerebellion.com/post/job-a-repost/">Job A Repost — Corp A</a></h2>
<p>Pay: $50/hr updated.</p>
<p><a href="https://jobs.example.com/jobA">Apply</a></p>
</div>
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_navigate(html: str):
    async def _fn(url: str) -> str:
        return html
    return _fn


def _make_failing_navigate(exc=None):
    async def _fn(url: str) -> str:
        raise (exc or ConnectionError("network down"))
    return _fn


def _in_memory_store():
    from plugins.job_search import JobSearchStore
    return JobSearchStore(db_path=Path(":memory:"))


# ── Cycle 1 — plugin meta ─────────────────────────────────────────────────────

class TestPluginMeta:
    def test_name(self):
        from plugins.job_search import JobSearchPlugin
        assert JobSearchPlugin().name == "job_search"

    def test_lists_tools(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert names == {
            "jobs_fetch_postings", "jobs_store_resume", "jobs_score_shortlist",
            "jobs_set_approval", "jobs_apply_start", "jobs_apply_submit",
            "jobs_answer_field",    # S5
            "jobs_set_auto_submit", # S7
        }

    def test_required_capabilities(self):
        from plugins.job_search import REQUIRED_CAPABILITIES
        assert REQUIRED_CAPABILITIES == frozenset({
            "external_data_read",
            "external_data_write",   # S4: submitting an application
            "network_egress_cloud",
            "fs_write",
            "fs_read",               # S2: read resume PDF artifact
            "secrets_read",          # S6: reading ATS account passwords (ADR-0009)
        })

    def test_create_factory(self):
        from plugins.job_search import create, JobSearchPlugin
        assert isinstance(create(), JobSearchPlugin)

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(""), store=_in_memory_store())
        result = await plugin.call_tool("nonexistent", {})
        assert result.is_error


# ── Cycle 2 — HTML parsing ────────────────────────────────────────────────────

class TestParsePostings:
    def test_parses_two_entries(self):
        from plugins.job_search import parse_postings
        postings = parse_postings(RRR_FIXTURE_HTML)
        assert len(postings) == 2

    def test_first_entry_title(self):
        from plugins.job_search import parse_postings
        p = parse_postings(RRR_FIXTURE_HTML)[0]
        assert "Senior Python Developer" in p["title"]

    def test_first_entry_company(self):
        from plugins.job_search import parse_postings
        p = parse_postings(RRR_FIXTURE_HTML)[0]
        assert p["company"] == "Acme Corp"

    def test_first_entry_pay(self):
        from plugins.job_search import parse_postings
        p = parse_postings(RRR_FIXTURE_HTML)[0]
        assert "$80,000" in p["pay"]

    def test_first_entry_outbound_url(self):
        from plugins.job_search import parse_postings
        p = parse_postings(RRR_FIXTURE_HTML)[0]
        assert p["url"] == "https://boards.greenhouse.io/acme/jobs/123"

    def test_first_entry_snapshot_nonempty(self):
        from plugins.job_search import parse_postings
        p = parse_postings(RRR_FIXTURE_HTML)[0]
        assert len(p["snapshot"]) > 10

    def test_first_entry_date(self):
        from plugins.job_search import parse_postings
        p = parse_postings(RRR_FIXTURE_HTML)[0]
        assert p["posted_date"] == "2026-07-01"

    def test_second_entry_url_is_lever(self):
        from plugins.job_search import parse_postings
        p = parse_postings(RRR_FIXTURE_HTML)[1]
        assert "lever.co" in p["url"]

    def test_entry_without_outbound_url_still_parsed(self):
        # parse_postings returns it; jobs_fetch_postings skips it at upsert time
        from plugins.job_search import parse_postings
        postings = parse_postings(RRR_FIXTURE_NO_LINKS)
        assert len(postings) == 1
        assert postings[0]["url"] == ""


# ── Cycle 3 — SQLite store ────────────────────────────────────────────────────

class TestJobSearchStore:
    def test_count_starts_zero(self):
        store = _in_memory_store()
        assert store.count() == 0

    def test_upsert_increments_count(self):
        store = _in_memory_store()
        store.upsert({"url": "https://example.com/1", "title": "T1", "company": "C1",
                      "pay": "$50/hr", "snapshot": "s1", "posted_date": "2026-07-01"})
        assert store.count() == 1

    def test_upsert_dedupes_on_url(self):
        store = _in_memory_store()
        p = {"url": "https://example.com/1", "title": "T1", "company": "C1",
             "pay": "$50/hr", "snapshot": "s1", "posted_date": "2026-07-01"}
        store.upsert(p)
        store.upsert(p)          # same URL — should update, not insert
        assert store.count() == 1

    def test_upsert_updates_fields_on_conflict(self):
        store = _in_memory_store()
        store.upsert({"url": "https://example.com/1", "title": "Old Title", "company": "C",
                      "pay": "$40/hr", "snapshot": "s", "posted_date": "2026-07-01"})
        store.upsert({"url": "https://example.com/1", "title": "New Title", "company": "C",
                      "pay": "$50/hr", "snapshot": "s", "posted_date": "2026-07-01"})
        rows = store.list_postings()
        assert rows[0]["title"] == "New Title"
        assert rows[0]["pay"] == "$50/hr"

    def test_list_postings_returns_dicts(self):
        store = _in_memory_store()
        store.upsert({"url": "https://example.com/1", "title": "T1", "company": "C1",
                      "pay": "", "snapshot": "", "posted_date": ""})
        rows = store.list_postings()
        assert isinstance(rows[0], dict)
        assert "url" in rows[0]


# ── Cycle 4 — tool call (no live network) ────────────────────────────────────

_BOARD_URL = "https://test.board.example.com/jobs"


class TestJobsFetchPostings:
    @pytest.mark.asyncio
    async def test_returns_parsed_count(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        store.add_board(_BOARD_URL)  # S1 #396: fetch loops boards, not RRR_URL directly
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_HTML), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["fetched"] == 2
        assert data["saved"] == 2

    @pytest.mark.asyncio
    async def test_postings_in_response(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        store.add_board(_BOARD_URL)
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_HTML), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        data = json.loads(result.content)
        urls = {p["url"] for p in data["postings"]}
        assert "https://boards.greenhouse.io/acme/jobs/123" in urls

    @pytest.mark.asyncio
    async def test_skips_entries_without_ats_url(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        store.add_board(_BOARD_URL)
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_NO_LINKS), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        data = json.loads(result.content)
        assert data["saved"] == 0     # no outbound URL -> not stored
        assert data["fetched"] == 1   # still parsed 1 entry

    @pytest.mark.asyncio
    async def test_dedup_on_second_fetch(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        store.add_board(_BOARD_URL)
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_HTML), store=store)
        await plugin.call_tool("jobs_fetch_postings", {})
        result2 = await plugin.call_tool("jobs_fetch_postings", {})
        data = json.loads(result2.content)
        assert store.count() == 2     # dedup: still 2 rows

    @pytest.mark.asyncio
    async def test_dedup_within_fixture(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        store.add_board(_BOARD_URL)
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_DUPE), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        assert store.count() == 1    # two entries, same ATS URL -> 1 row

    @pytest.mark.asyncio
    async def test_navigate_failure_reported_per_board(self):
        # S1 #396: a failing board is reported in per_board; overall is not is_error.
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        store.add_board(_BOARD_URL)
        plugin = JobSearchPlugin(navigate_fn=_make_failing_navigate(), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert "per_board" in data
        assert "error" in data["per_board"][0]

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self):
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_HTML), store=None)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_navigate_seam_set_after_create_is_used(self, monkeypatch):
        """The live #401 bug: create() runs during plugin discovery, BEFORE
        _wire_plugin_seams injects the real navigate. Eagerly coalescing
        `navigate_fn or _default_navigate` in __init__ froze the OpenClaw
        default into the instance and the wired seam was never consulted.
        Resolution must happen at CALL time, like every other seam."""
        import plugins.job_search as jm
        store = _in_memory_store()
        store.add_board(_BOARD_URL)
        plugin = jm.JobSearchPlugin(store=store)  # no navigate_fn at create()
        calls = []

        async def late_navigate(url):
            calls.append(url)
            return RRR_FIXTURE_HTML

        monkeypatch.setattr(jm, "_navigate_fn", None)
        jm.set_navigate_fn(late_navigate)  # seam wired AFTER create()
        try:
            result = await plugin.call_tool("jobs_fetch_postings", {})
        finally:
            jm._navigate_fn = None
        assert calls, "post-create set_navigate_fn was ignored (init-capture trap)"
        data = json.loads(result.content)
        assert data["saved"] == 2


# ── S2 fixtures ───────────────────────────────────────────────────────────────
#
# Simulates extracted text from a single-page resume PDF.

RESUME_FIXTURE_TEXT = """
John Doe
john.doe@example.com | 555-123-4567 | New York, NY
https://linkedin.com/in/johndoe | https://github.com/johndoe

EXPERIENCE
Senior Python Developer, Acme Corp (2022 - Present)
  Built distributed data pipelines; reduced latency 40%.

Data Engineer, Beta Inc (2019 - 2022)
  Migrated on-prem ETL to AWS Glue.

EDUCATION
B.S. Computer Science, State University, 2019

SKILLS
Python, SQL, AWS, Docker, Kubernetes
"""

_FIXTURE_DOSSIER = {
    "name": "John Doe",
    "email": "john.doe@example.com",
    "phone": "555-123-4567",
    "location": "New York, NY",
    "linkedin": "https://linkedin.com/in/johndoe",
    "github": "https://github.com/johndoe",
    "website": "",
    "work_history": [
        {"title": "Senior Python Developer", "company": "Acme Corp", "years": "2022-Present"},
        {"title": "Data Engineer", "company": "Beta Inc", "years": "2019-2022"},
    ],
    "education": [
        {"degree": "B.S. Computer Science", "school": "State University", "year": "2019"}
    ],
    "skills": ["Python", "SQL", "AWS", "Docker", "Kubernetes"],
}

_PROFILE_ID = 1
_RESUME_PATH = "/tmp/test-resume.pdf"


def _stub_extract(text: str) -> dict:
    """Synchronous stub extractor — returns fixture dossier regardless of text."""
    return dict(_FIXTURE_DOSSIER)


async def _async_stub_extract(text: str) -> dict:
    """Async stub extractor."""
    return dict(_FIXTURE_DOSSIER)


# ── Cycle 5 — JobSearchStore dossier/artifact ─────────────────────────────────

class TestDossierStore:
    def test_get_resume_artifact_none_initially(self):
        store = _in_memory_store()
        assert store.get_resume_artifact(_PROFILE_ID) is None

    def test_upsert_resume_artifact(self):
        store = _in_memory_store()
        store.upsert_resume_artifact(_PROFILE_ID, _RESUME_PATH)
        artifact = store.get_resume_artifact(_PROFILE_ID)
        assert artifact is not None
        assert artifact["pdf_path"] == _RESUME_PATH

    def test_upsert_resume_artifact_replaces(self):
        store = _in_memory_store()
        store.upsert_resume_artifact(_PROFILE_ID, "/old/path.pdf")
        store.upsert_resume_artifact(_PROFILE_ID, _RESUME_PATH)
        artifact = store.get_resume_artifact(_PROFILE_ID)
        assert artifact["pdf_path"] == _RESUME_PATH

    def test_get_dossier_none_initially(self):
        store = _in_memory_store()
        assert store.get_dossier(_PROFILE_ID) is None

    def test_upsert_dossier_stores_fields(self):
        store = _in_memory_store()
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        d = store.get_dossier(_PROFILE_ID)
        assert d is not None
        assert d["name"] == "John Doe"
        assert d["email"] == "john.doe@example.com"
        assert d["phone"] == "555-123-4567"
        assert d["location"] == "New York, NY"
        assert d["linkedin"] == "https://linkedin.com/in/johndoe"
        assert d["github"] == "https://github.com/johndoe"

    def test_upsert_dossier_json_lists_deserialised(self):
        store = _in_memory_store()
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        d = store.get_dossier(_PROFILE_ID)
        assert isinstance(d["work_history_json"], list)
        assert len(d["work_history_json"]) == 2
        assert isinstance(d["education_json"], list)
        assert isinstance(d["skills_json"], list)

    def test_upsert_dossier_replaces(self):
        store = _in_memory_store()
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        updated = {**_FIXTURE_DOSSIER, "email": "new@example.com"}
        store.upsert_dossier(_PROFILE_ID, updated)
        d = store.get_dossier(_PROFILE_ID)
        assert d["email"] == "new@example.com"

    def test_upsert_dossier_raw_text_stored(self):
        store = _in_memory_store()
        fields = {**_FIXTURE_DOSSIER, "raw_text": "raw pdf text"}
        store.upsert_dossier(_PROFILE_ID, fields)
        d = store.get_dossier(_PROFILE_ID)
        assert d["raw_text"] == "raw pdf text"

    def test_different_profiles_isolated(self):
        store = _in_memory_store()
        store.upsert_dossier(1, _FIXTURE_DOSSIER)
        store.upsert_dossier(2, {**_FIXTURE_DOSSIER, "name": "Jane Smith"})
        assert store.get_dossier(1)["name"] == "John Doe"
        assert store.get_dossier(2)["name"] == "Jane Smith"

    def test_upsert_dossier_tolerates_explicit_nulls(self):
        """LLM extractors emit '"linkedin": null' — keys PRESENT with None
        values, which .get(key, "") does not default. Live failure
        2026-07-06: NOT NULL constraint on applicant_dossier.linkedin."""
        store = _in_memory_store()
        nulled = {
            "name": "Adam Poder", "email": "a@example.com",
            "phone": None, "location": None, "linkedin": None,
            "github": None, "website": None,
            "work_history": None, "education": None, "skills": None,
            "raw_text": None,
        }
        store.upsert_dossier(_PROFILE_ID, nulled)
        d = store.get_dossier(_PROFILE_ID)
        assert d["linkedin"] == ""
        assert d["work_history_json"] == []

    def test_upsert_dossier_failure_does_not_wedge_connection(self):
        """A failed write must roll back — the open implicit transaction
        holds the SQLite write lock and every other connection in the
        process starts failing with 'database is locked' (live 2026-07-06:
        conversation turns stopped recording after one bad dossier insert)."""
        store = _in_memory_store()
        with pytest.raises(Exception):
            # profile_id=None violates NOT NULL on a guaranteed column.
            store.upsert_dossier(None, _FIXTURE_DOSSIER)
        assert not store._con.in_transaction, (
            "failed upsert left the transaction (write lock) open"
        )
        # And the connection still works afterwards.
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        assert store.get_dossier(_PROFILE_ID)["name"] == "John Doe"


# ── Cycle 6 — jobs_store_resume tool ─────────────────────────────────────────

class TestStoreResumeTool:
    def _make_plugin(self, store=None, extract_fn=_stub_extract):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = _RESUME_PATH
        from plugins.job_search import JobSearchPlugin
        return JobSearchPlugin(store=store or _in_memory_store(), extract_fn=extract_fn)

    @pytest.mark.asyncio
    async def test_tool_listed(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert "jobs_store_resume" in names

    @pytest.mark.asyncio
    async def test_store_resume_success(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "stored"
        assert data["dossier"]["name"] == "John Doe"

    @pytest.mark.asyncio
    async def test_store_resume_persists_artifact_path(self):
        store = _in_memory_store()
        plugin = self._make_plugin(store=store)
        await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        artifact = store.get_resume_artifact(_PROFILE_ID)
        assert artifact is not None
        assert artifact["pdf_path"] == _RESUME_PATH

    @pytest.mark.asyncio
    async def test_store_resume_persists_dossier(self):
        store = _in_memory_store()
        plugin = self._make_plugin(store=store)
        await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        d = store.get_dossier(_PROFILE_ID)
        assert d is not None
        assert d["email"] == "john.doe@example.com"

    @pytest.mark.asyncio
    async def test_store_resume_raw_text_stored(self):
        store = _in_memory_store()
        plugin = self._make_plugin(store=store)
        await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        d = store.get_dossier(_PROFILE_ID)
        assert RESUME_FIXTURE_TEXT[:100] in d["raw_text"]

    @pytest.mark.asyncio
    async def test_store_resume_replace_on_reupload(self):
        store = _in_memory_store()
        plugin = self._make_plugin(store=store)
        await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        new_text = RESUME_FIXTURE_TEXT.replace("John Doe", "Jane Smith")
        # stub now returns Jane Smith
        new_stub = lambda t: {**_FIXTURE_DOSSIER, "name": "Jane Smith"}
        plugin2 = self._make_plugin(store=store, extract_fn=new_stub)
        await plugin2.call_tool("jobs_store_resume", {"pdf_text": new_text})
        d = store.get_dossier(_PROFILE_ID)
        assert d["name"] == "Jane Smith"

    @pytest.mark.asyncio
    async def test_store_resume_async_extractor(self):
        plugin = self._make_plugin(extract_fn=_async_stub_extract)
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["dossier"]["name"] == "John Doe"

    @pytest.mark.asyncio
    async def test_store_resume_empty_text_returns_error(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": "   "})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_store_resume_no_store_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=None, extract_fn=_stub_extract)
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_store_resume_no_profile_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=_in_memory_store(), extract_fn=_stub_extract)
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_store_resume_no_extractor_still_stores_raw(self):
        """No extract_fn set — artifact + raw_text are still stored."""
        store = _in_memory_store()
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = _RESUME_PATH
        jm._extract_fn = None  # clear module-level seam to avoid test-order pollution
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, extract_fn=None)
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": RESUME_FIXTURE_TEXT})
        assert not result.is_error
        d = store.get_dossier(_PROFILE_ID)
        assert d is not None
        assert RESUME_FIXTURE_TEXT[:100] in d["raw_text"]


# ── S3 helpers ────────────────────────────────────────────────────────────────

_ATS_URL_1 = "https://boards.greenhouse.io/acme/jobs/123"
_ATS_URL_2 = "https://jobs.lever.co/beta/456"

def _seed_postings(store):
    """Insert two postings into the store (urls from RRR fixture)."""
    from plugins.job_search import parse_postings
    for p in parse_postings(RRR_FIXTURE_HTML):
        if p.get("url"):
            store.upsert(p)

def _stub_scorer(posting: dict, dossier: dict) -> float:
    """Return a deterministic score based on ATS URL so tests can check ordering."""
    return 8.0 if "greenhouse" in posting.get("url", "") else 4.0

async def _async_stub_scorer(posting: dict, dossier: dict) -> float:
    return _stub_scorer(posting, dossier)

def _make_scored_plugin(store):
    import plugins.job_search as jm
    jm._active_profile_id = _PROFILE_ID
    jm._pending_resume_path = _RESUME_PATH
    from plugins.job_search import JobSearchPlugin
    return JobSearchPlugin(store=store, extract_fn=_stub_extract, score_fn=_stub_scorer)


# ── Cycle 7 — JobSearchStore shortlist columns ────────────────────────────────

class TestShortlistStore:
    def test_new_posting_has_null_score(self):
        store = _in_memory_store()
        _seed_postings(store)
        rows = store.list_postings()
        assert all(r["fit_score"] is None for r in rows)

    def test_new_posting_has_status_new(self):
        store = _in_memory_store()
        _seed_postings(store)
        rows = store.list_postings()
        assert all(r["status"] == "new" for r in rows)

    def test_set_score_persists(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 7.5)
        row = next(r for r in store.list_postings() if r["url"] == _ATS_URL_1)
        assert row["fit_score"] == pytest.approx(7.5)

    def test_set_status_persists(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_status(_ATS_URL_1, "shortlisted")
        row = next(r for r in store.list_postings() if r["url"] == _ATS_URL_1)
        assert row["status"] == "shortlisted"

    def test_list_unscored_returns_new_only(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 9.0)
        unscored = store.list_unscored()
        urls = {r["url"] for r in unscored}
        assert _ATS_URL_1 not in urls
        assert _ATS_URL_2 in urls

    def test_list_shortlist_ordered_by_score_desc(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 8.0)
        store.set_score(_ATS_URL_2, 4.0)
        shortlist = store.list_shortlist()
        assert len(shortlist) == 2
        assert shortlist[0]["url"] == _ATS_URL_1
        assert shortlist[1]["url"] == _ATS_URL_2

    def test_list_shortlist_excludes_rejected(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 8.0)
        store.set_score(_ATS_URL_2, 4.0)
        store.set_status(_ATS_URL_2, "rejected")
        shortlist = store.list_shortlist()
        assert len(shortlist) == 1
        assert shortlist[0]["url"] == _ATS_URL_1

    def test_list_shortlist_empty_when_none_scored(self):
        store = _in_memory_store()
        _seed_postings(store)
        assert store.list_shortlist() == []

    def test_shortlisted_entry_included_in_shortlist(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 8.0)
        store.set_status(_ATS_URL_1, "shortlisted")
        shortlist = store.list_shortlist()
        assert any(r["url"] == _ATS_URL_1 for r in shortlist)


# ── Cycle 8 — jobs_score_shortlist tool ──────────────────────────────────────

class TestScoreShortlistTool:
    def _make_plugin_with_dossier(self, store):
        """Seed store with postings + dossier, return ready plugin."""
        _seed_postings(store)
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        return _make_scored_plugin(store)

    @pytest.mark.asyncio
    async def test_tool_listed(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert "jobs_score_shortlist" in names

    @pytest.mark.asyncio
    async def test_scores_unscored_postings(self):
        store = _in_memory_store()
        plugin = self._make_plugin_with_dossier(store)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["scored"] == 2

    @pytest.mark.asyncio
    async def test_shortlist_in_response(self):
        store = _in_memory_store()
        plugin = self._make_plugin_with_dossier(store)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        data = json.loads(result.content)
        assert len(data["shortlist"]) == 2

    @pytest.mark.asyncio
    async def test_shortlist_ordered_by_score(self):
        store = _in_memory_store()
        plugin = self._make_plugin_with_dossier(store)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        data = json.loads(result.content)
        scores = [p["fit_score"] for p in data["shortlist"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_scores_persisted_in_store(self):
        store = _in_memory_store()
        plugin = self._make_plugin_with_dossier(store)
        await plugin.call_tool("jobs_score_shortlist", {})
        row = next(r for r in store.list_postings() if r["url"] == _ATS_URL_1)
        assert row["fit_score"] == pytest.approx(8.0)

    @pytest.mark.asyncio
    async def test_idempotent_second_run(self):
        """Second call scores 0 new entries (all already scored)."""
        store = _in_memory_store()
        plugin = self._make_plugin_with_dossier(store)
        await plugin.call_tool("jobs_score_shortlist", {})
        result2 = await plugin.call_tool("jobs_score_shortlist", {})
        data = json.loads(result2.content)
        assert data["scored"] == 0

    @pytest.mark.asyncio
    async def test_async_scorer(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, score_fn=_async_stub_scorer)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["scored"] == 2

    @pytest.mark.asyncio
    async def test_no_dossier_returns_error(self):
        store = _in_memory_store()
        _seed_postings(store)
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, score_fn=_stub_scorer)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=None, score_fn=_stub_scorer)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_profile_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=_in_memory_store(), score_fn=_stub_scorer)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_scorer_returns_error(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._score_fn = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, score_fn=None)
        result = await plugin.call_tool("jobs_score_shortlist", {})
        assert result.is_error


# ── Cycle 9 — jobs_set_approval tool ─────────────────────────────────────────

class TestSetApprovalTool:
    def _make_plugin_scored(self, store):
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 8.0)
        store.set_score(_ATS_URL_2, 4.0)
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        return JobSearchPlugin(store=store)

    @pytest.mark.asyncio
    async def test_tool_listed(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert "jobs_set_approval" in names

    @pytest.mark.asyncio
    async def test_approve_sets_shortlisted(self):
        store = _in_memory_store()
        plugin = self._make_plugin_scored(store)
        result = await plugin.call_tool("jobs_set_approval", {"url": _ATS_URL_1, "approved": True})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "shortlisted"
        row = next(r for r in store.list_postings() if r["url"] == _ATS_URL_1)
        assert row["status"] == "shortlisted"

    @pytest.mark.asyncio
    async def test_reject_sets_rejected(self):
        store = _in_memory_store()
        plugin = self._make_plugin_scored(store)
        result = await plugin.call_tool("jobs_set_approval", {"url": _ATS_URL_1, "approved": False})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_rejected_removed_from_shortlist(self):
        store = _in_memory_store()
        plugin = self._make_plugin_scored(store)
        result = await plugin.call_tool("jobs_set_approval", {"url": _ATS_URL_1, "approved": False})
        data = json.loads(result.content)
        urls = {p["url"] for p in data["shortlist"]}
        assert _ATS_URL_1 not in urls

    @pytest.mark.asyncio
    async def test_approved_remains_in_shortlist(self):
        store = _in_memory_store()
        plugin = self._make_plugin_scored(store)
        result = await plugin.call_tool("jobs_set_approval", {"url": _ATS_URL_1, "approved": True})
        data = json.loads(result.content)
        urls = {p["url"] for p in data["shortlist"]}
        assert _ATS_URL_1 in urls

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self):
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=None)
        result = await plugin.call_tool("jobs_set_approval", {"url": _ATS_URL_1, "approved": True})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_url_returns_error(self):
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store)
        result = await plugin.call_tool("jobs_set_approval", {"url": "", "approved": True})
        assert result.is_error


# ── S4 fixtures ───────────────────────────────────────────────────────────────

_ATS_UNKNOWN_URL = "https://workday.com/apply/something"

# Stub fields the apply_driver_fn returns — all required fields have values.
_FIXTURE_FIELDS_OK = [
    {"selector": "input[name=first_name]", "label": "First Name", "value": "John",                 "required": True,  "is_file_upload": False},
    {"selector": "input[name=email]",      "label": "Email",      "value": "john.doe@example.com", "required": True,  "is_file_upload": False},
    {"selector": "input[name=phone]",      "label": "Phone",      "value": "555-123-4567",         "required": False, "is_file_upload": False},
    {"selector": "input[type=file]",       "label": "Resume",     "value": "",                     "required": False, "is_file_upload": True},
]

# Fields with an unfilled required field — triggers awaiting-input.
_FIXTURE_FIELDS_MISSING = [
    {"selector": "input[name=first_name]", "label": "First Name", "value": "John", "required": True,  "is_file_upload": False},
    {"selector": "input[name=auth]",       "label": "Work Auth",  "value": "",     "required": True,  "is_file_upload": False},
]


def _stub_apply_driver(url, dossier, resume_path):
    return {"fields": _FIXTURE_FIELDS_OK, "submit_selector": 'button[type="submit"]'}


def _stub_apply_driver_missing(url, dossier, resume_path):
    return {"fields": _FIXTURE_FIELDS_MISSING, "submit_selector": 'button[type="submit"]'}


def _stub_apply_driver_linkedin(url, dossier, resume_path):
    # #435 — a required LinkedIn field the dossier can't fill.
    return {
        "fields": [
            {"selector": "#name", "label": "Name", "value": "Iggy", "required": True},
            {"selector": "#li", "label": "LinkedIn Profile*", "value": "", "required": True},
        ],
        "submit_selector": 'button[type="submit"]',
    }


def _stub_apply_submit():
    pass


def _make_apply_plugin(store, apply_driver_fn=None, apply_submit_fn=None):
    import plugins.job_search as jm
    jm._active_profile_id = _PROFILE_ID
    jm._pending_resume_path = _RESUME_PATH
    jm._pending_application = None
    from plugins.job_search import JobSearchPlugin
    return JobSearchPlugin(
        store=store,
        extract_fn=_stub_extract,
        score_fn=_stub_scorer,
        apply_driver_fn=apply_driver_fn or _stub_apply_driver,
        apply_submit_fn=apply_submit_fn or _stub_apply_submit,
    )


def _seed_shortlisted(store):
    """Seed store with a shortlisted Greenhouse posting + dossier + resume."""
    _seed_postings(store)
    store.set_score(_ATS_URL_1, 8.0)
    store.set_status(_ATS_URL_1, "shortlisted")
    store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
    store.upsert_resume_artifact(_PROFILE_ID, _RESUME_PATH)


# ── Cycle 10 — detect_ats_type ────────────────────────────────────────────────

class TestDetectAtsType:
    def test_greenhouse(self):
        from plugins.job_search import detect_ats_type
        assert detect_ats_type("https://boards.greenhouse.io/acme/jobs/123") == "greenhouse"

    def test_lever(self):
        from plugins.job_search import detect_ats_type
        assert detect_ats_type("https://jobs.lever.co/beta/456") == "lever"

    def test_unknown_returns_unknown(self):
        from plugins.job_search import detect_ats_type
        assert detect_ats_type("https://www.workday.com/apply/123") == "unknown"

    def test_empty_returns_unknown(self):
        from plugins.job_search import detect_ats_type
        assert detect_ats_type("") == "unknown"

    def test_greenhouse_subdomain(self):
        from plugins.job_search import detect_ats_type
        assert detect_ats_type("https://boards.greenhouse.io/acme") == "greenhouse"

    def test_lever_subdomain(self):
        from plugins.job_search import detect_ats_type
        assert detect_ats_type("https://jobs.lever.co/company/position") == "lever"


# ── Cycle 11 — ApplicationStore ──────────────────────────────────────────────

class TestApplicationStore:
    def test_list_applications_empty(self):
        store = _in_memory_store()
        assert store.list_applications() == []

    def test_upsert_application_stores_row(self):
        store = _in_memory_store()
        store.upsert_application(
            url="https://boards.greenhouse.io/acme/jobs/1",
            posting_url="https://boards.greenhouse.io/acme/jobs/1",
            ats_type="greenhouse", status="shortlisted",
            fields=[{"selector": "input[name=email]", "value": "a@b.com"}],
        )
        apps = store.list_applications()
        assert len(apps) == 1
        assert apps[0]["status"] == "shortlisted"
        assert apps[0]["ats_type"] == "greenhouse"

    def test_upsert_application_dedup_on_url(self):
        store = _in_memory_store()
        url = "https://boards.greenhouse.io/acme/jobs/1"
        store.upsert_application(url=url, posting_url=url, ats_type="greenhouse", status="shortlisted", fields=[])
        store.upsert_application(url=url, posting_url=url, ats_type="greenhouse", status="submitted", fields=[])
        assert len(store.list_applications()) == 1
        assert store.get_application(url)["status"] == "submitted"

    def test_set_application_status(self):
        store = _in_memory_store()
        url = "https://boards.greenhouse.io/acme/jobs/1"
        store.upsert_application(url=url, posting_url=url, ats_type="greenhouse", status="shortlisted", fields=[])
        store.set_application_status(url, "submitted", submitted_at="2026-07-02T00:00:00Z")
        app = store.get_application(url)
        assert app["status"] == "submitted"
        assert app["submitted_at"] == "2026-07-02T00:00:00Z"

    def test_get_application_none(self):
        store = _in_memory_store()
        assert store.get_application("https://missing.example.com/") is None

    def test_filled_fields_json_deserialised(self):
        store = _in_memory_store()
        url = "https://boards.greenhouse.io/acme/jobs/1"
        fields = [{"selector": "input[name=email]", "value": "a@b.com"}]
        store.upsert_application(url=url, posting_url=url, ats_type="greenhouse", status="shortlisted", fields=fields)
        app = store.get_application(url)
        assert isinstance(app["filled_fields_json"], list)
        assert app["filled_fields_json"][0]["selector"] == "input[name=email]"

    def test_get_posting_by_url(self):
        store = _in_memory_store()
        _seed_postings(store)
        posting = store.get_posting_by_url(_ATS_URL_1)
        assert posting is not None
        assert "greenhouse" in posting["url"]

    def test_get_posting_by_url_missing(self):
        store = _in_memory_store()
        assert store.get_posting_by_url("https://nope.example.com/") is None


# ── Cycle 12 — jobs_apply_start tool ─────────────────────────────────────────

class TestApplyStart:
    @pytest.mark.asyncio
    async def test_tool_listed(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert "jobs_apply_start" in names

    @pytest.mark.asyncio
    async def test_happy_path_returns_ready_to_submit(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_apply_plugin(store)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "ready_to_submit"
        assert data["ats_type"] == "greenhouse"

    @pytest.mark.asyncio
    async def test_happy_path_stores_application_as_shortlisted(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_apply_plugin(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        app = store.get_application(_ATS_URL_1)
        assert app is not None
        assert app["status"] == "shortlisted"

    @pytest.mark.asyncio
    async def test_unsupported_ats_bails_and_marks_failed(self):
        store = _in_memory_store()
        store.upsert({
            "url": _ATS_UNKNOWN_URL, "title": "Workday Job", "company": "Co",
            "pay": "", "snapshot": "", "posted_date": "",
        })
        store.set_score(_ATS_UNKNOWN_URL, 7.0)
        store.set_status(_ATS_UNKNOWN_URL, "shortlisted")
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        store.upsert_resume_artifact(_PROFILE_ID, _RESUME_PATH)
        plugin = _make_apply_plugin(store)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_UNKNOWN_URL})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "failed"
        app = store.get_application(_ATS_UNKNOWN_URL)
        assert app is not None
        assert app["status"] == "failed"

    @pytest.mark.asyncio
    async def test_missing_required_field_stops_awaiting_input(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_apply_plugin(store, apply_driver_fn=_stub_apply_driver_missing)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "awaiting-input"
        assert "Work Auth" in data["missing_fields"]
        app = store.get_application(_ATS_URL_1)
        assert app is not None
        assert app["status"] == "awaiting-input"

    @pytest.mark.asyncio
    async def test_unfillable_linkedin_skips_posting(self):
        """#435 — user skip rule: required LinkedIn we can't fill abandons
        the posting (skipped), not awaiting-input."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_apply_plugin(store, apply_driver_fn=_stub_apply_driver_linkedin)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "skipped"
        assert "LinkedIn" in data["reason"]
        app = store.get_application(_ATS_URL_1)
        assert app["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=None, apply_driver_fn=_stub_apply_driver)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_profile_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = None
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, apply_driver_fn=_stub_apply_driver)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_url_returns_error(self):
        store = _in_memory_store()
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, apply_driver_fn=_stub_apply_driver)
        result = await plugin.call_tool("jobs_apply_start", {"url": ""})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_not_shortlisted_returns_error(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 8.0)
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        store.upsert_resume_artifact(_PROFILE_ID, _RESUME_PATH)
        plugin = _make_apply_plugin(store)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_dossier_returns_error(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_1, 8.0)
        store.set_status(_ATS_URL_1, "shortlisted")
        plugin = _make_apply_plugin(store)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_apply_driver_returns_error(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._apply_driver_fn = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, apply_driver_fn=None)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_lever_ats_supported(self):
        store = _in_memory_store()
        _seed_postings(store)
        store.set_score(_ATS_URL_2, 7.0)
        store.set_status(_ATS_URL_2, "shortlisted")
        store.upsert_dossier(_PROFILE_ID, _FIXTURE_DOSSIER)
        store.upsert_resume_artifact(_PROFILE_ID, _RESUME_PATH)
        plugin = _make_apply_plugin(store)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_2})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["ats_type"] == "lever"


# ── Cycle 13 — jobs_apply_submit tool ────────────────────────────────────────

class TestApplySubmit:
    @pytest.mark.asyncio
    async def test_tool_is_irreversible(self):
        from plugins.job_search import JobSearchPlugin
        tools = {t.name: t for t in JobSearchPlugin().list_tools()}
        assert tools["jobs_apply_submit"].irreversible is True

    @pytest.mark.asyncio
    async def test_submit_succeeds_after_start(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_apply_plugin(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        result = await plugin.call_tool("jobs_apply_submit", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "submitted"
        assert data["url"] == _ATS_URL_1

    @pytest.mark.asyncio
    async def test_submit_calls_submit_fn(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        calls = []
        def recorder():
            calls.append("submit")
        plugin = _make_apply_plugin(store, apply_submit_fn=recorder)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        await plugin.call_tool("jobs_apply_submit", {})
        assert "submit" in calls

    @pytest.mark.asyncio
    async def test_submit_logs_application_as_submitted(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_apply_plugin(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        await plugin.call_tool("jobs_apply_submit", {})
        app = store.get_application(_ATS_URL_1)
        assert app is not None
        assert app["status"] == "submitted"
        assert app["submitted_at"] is not None

    @pytest.mark.asyncio
    async def test_submit_without_start_returns_error(self):
        store = _in_memory_store()
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_application = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, apply_submit_fn=_stub_apply_submit)
        result = await plugin.call_tool("jobs_apply_submit", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_submit_no_store_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_application = {"url": _ATS_URL_1, "ats_type": "greenhouse"}
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=None, apply_submit_fn=_stub_apply_submit)
        result = await plugin.call_tool("jobs_apply_submit", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_submit_no_submit_fn_returns_error(self):
        store = _in_memory_store()
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_application = {"url": _ATS_URL_1, "ats_type": "greenhouse"}
        jm._apply_submit_fn = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, apply_submit_fn=None)
        result = await plugin.call_tool("jobs_apply_submit", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_submit_deduped_on_url(self):
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_apply_plugin(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        await plugin.call_tool("jobs_apply_submit", {})
        store.set_status(_ATS_URL_1, "shortlisted")
        plugin2 = _make_apply_plugin(store)
        await plugin2.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        await plugin2.call_tool("jobs_apply_submit", {})
        apps = store.list_applications()
        assert len(apps) == 1  # same URL deduped


# ── Cycle 14 — updated plugin meta ────────────────────────────────────────────

class TestPluginMetaS4:
    def test_lists_tools(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert names == {
            "jobs_fetch_postings", "jobs_store_resume",
            "jobs_score_shortlist", "jobs_set_approval",
            "jobs_apply_start", "jobs_apply_submit",
            "jobs_answer_field",    # S5
            "jobs_set_auto_submit", # S7
        }

    def test_required_capabilities_includes_data_write(self):
        from plugins.job_search import REQUIRED_CAPABILITIES
        assert "external_data_write" in REQUIRED_CAPABILITIES

    def test_jobs_apply_submit_is_irreversible(self):
        from plugins.job_search import JobSearchPlugin
        tools = {t.name: t for t in JobSearchPlugin().list_tools()}
        assert tools["jobs_apply_submit"].irreversible is True

    def test_jobs_apply_start_not_irreversible(self):
        from plugins.job_search import JobSearchPlugin
        tools = {t.name: t for t in JobSearchPlugin().list_tools()}
        assert not tools["jobs_apply_start"].irreversible


# ── S5 fixtures ───────────────────────────────────────────────────────────────

# Fields where "Work Authorization" is required but empty (not in dossier).
_FIXTURE_FIELDS_WORK_AUTH = [
    {"selector": "input[name=first_name]", "label": "First Name",        "value": "John", "required": True,  "is_file_upload": False},
    {"selector": "input[name=work_auth]",  "label": "Work Authorization", "value": "",     "required": True,  "is_file_upload": False},
]


def _stub_driver_work_auth(url, dossier, resume_path):
    return {"fields": _FIXTURE_FIELDS_WORK_AUTH, "submit_selector": 'button[type="submit"]'}


def _make_stub_recall(bank: dict):
    """Exact-match recall stub: returns the stored answer or None."""
    async def _fn(profile_id: int, question: str) -> str | None:
        return bank.get(question)
    return _fn


def _make_fuzzy_recall(bank: dict):
    """Simulates semantic matching via lowercase+strip (stands in for vector similarity)."""
    async def _fn(profile_id: int, question: str) -> str | None:
        return bank.get(question.lower().strip())
    return _fn


# ── Cycle 15 — Answer bank store ─────────────────────────────────────────────

class TestAnswerBankStore:
    def test_list_answers_empty(self):
        store = _in_memory_store()
        assert store.list_answers(_PROFILE_ID) == []

    def test_upsert_answer_stores(self):
        store = _in_memory_store()
        store.upsert_answer(_PROFILE_ID, "Work Authorization", "US Citizen")
        answers = store.list_answers(_PROFILE_ID)
        assert len(answers) == 1
        assert answers[0]["question"] == "Work Authorization"
        assert answers[0]["answer"] == "US Citizen"

    def test_upsert_answer_replaces(self):
        store = _in_memory_store()
        store.upsert_answer(_PROFILE_ID, "Work Authorization", "Visa")
        store.upsert_answer(_PROFILE_ID, "Work Authorization", "US Citizen")
        answers = store.list_answers(_PROFILE_ID)
        assert len(answers) == 1
        assert answers[0]["answer"] == "US Citizen"

    def test_different_questions_stored_separately(self):
        store = _in_memory_store()
        store.upsert_answer(_PROFILE_ID, "Work Authorization", "US Citizen")
        store.upsert_answer(_PROFILE_ID, "Salary Expectation", "$80k")
        assert len(store.list_answers(_PROFILE_ID)) == 2

    def test_different_profiles_isolated(self):
        store = _in_memory_store()
        store.upsert_answer(1, "Salary", "$80k")
        store.upsert_answer(2, "Salary", "$90k")
        assert store.list_answers(1)[0]["answer"] == "$80k"
        assert store.list_answers(2)[0]["answer"] == "$90k"


# ── Cycle 16 — jobs_answer_field tool ────────────────────────────────────────

class TestAnswerFieldTool:
    def _make_plugin(self, store=None, recall_fn=None, index_answer_fn=None):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._recall_fn = None
        jm._index_answer_fn = None
        from plugins.job_search import JobSearchPlugin
        return JobSearchPlugin(
            store=store or _in_memory_store(),
            recall_fn=recall_fn,
            index_answer_fn=index_answer_fn,
        )

    @pytest.mark.asyncio
    async def test_tool_listed(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert "jobs_answer_field" in names

    @pytest.mark.asyncio
    async def test_store_answer_success(self):
        store = _in_memory_store()
        plugin = self._make_plugin(store=store)
        result = await plugin.call_tool("jobs_answer_field", {"question": "Work Auth", "answer": "US Citizen"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "stored"
        assert any(a["question"] == "Work Auth" for a in store.list_answers(_PROFILE_ID))

    @pytest.mark.asyncio
    async def test_empty_question_returns_error(self):
        plugin = self._make_plugin()
        result = await plugin.call_tool("jobs_answer_field", {"question": "  ", "answer": "US Citizen"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_calls_index_fn(self):
        indexed = []
        async def capture_index(pid, q, a):
            indexed.append((q, a))
        store = _in_memory_store()
        plugin = self._make_plugin(store=store, index_answer_fn=capture_index)
        await plugin.call_tool("jobs_answer_field", {"question": "Work Auth", "answer": "Yes"})
        assert ("Work Auth", "Yes") in indexed

    @pytest.mark.asyncio
    async def test_index_fn_not_required(self):
        """Omitting index_fn is fine — SQLite persistence still works."""
        store = _in_memory_store()
        plugin = self._make_plugin(store=store, index_answer_fn=None)
        result = await plugin.call_tool("jobs_answer_field", {"question": "q", "answer": "a"})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=None)
        result = await plugin.call_tool("jobs_answer_field", {"question": "q", "answer": "a"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_profile_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=_in_memory_store())
        result = await plugin.call_tool("jobs_answer_field", {"question": "q", "answer": "a"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_answer_returned_in_bank_list(self):
        store = _in_memory_store()
        plugin = self._make_plugin(store=store)
        result = await plugin.call_tool("jobs_answer_field", {"question": "Salary", "answer": "$80k"})
        data = json.loads(result.content)
        assert any(a["question"] == "Salary" for a in data["answer_bank"])


# ── Cycle 17 — semantic field-matching in _apply_start ───────────────────────

class TestSemanticFieldMatching:
    """S5: Answer bank auto-fills a required field when recall_fn returns a match."""

    def _make_plugin(self, store, recall_fn=None, driver_fn=None):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = _RESUME_PATH
        jm._pending_application = None
        jm._recall_fn = None
        from plugins.job_search import JobSearchPlugin
        return JobSearchPlugin(
            store=store,
            extract_fn=_stub_extract,
            score_fn=_stub_scorer,
            apply_driver_fn=driver_fn or _stub_driver_work_auth,
            apply_submit_fn=_stub_apply_submit,
            recall_fn=recall_fn,
        )

    @pytest.mark.asyncio
    async def test_no_recall_fn_triggers_awaiting_input(self):
        """Without recall_fn the missing required field still escalates."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = self._make_plugin(store, recall_fn=None)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "awaiting-input"

    @pytest.mark.asyncio
    async def test_exact_match_fills_field_ready_to_submit(self):
        """Exact match in Answer bank fills missing required field -> ready_to_submit."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        recall = _make_stub_recall({"Work Authorization": "US Citizen"})
        plugin = self._make_plugin(store, recall_fn=recall)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "ready_to_submit"

    @pytest.mark.asyncio
    async def test_no_match_triggers_awaiting_input_with_field_name(self):
        """No Answer-bank match -> escalate, missing_fields contains the label."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        recall = _make_stub_recall({})  # empty bank
        plugin = self._make_plugin(store, recall_fn=recall)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "awaiting-input"
        assert "Work Authorization" in data["missing_fields"]

    @pytest.mark.asyncio
    async def test_semantic_match_fills_field(self):
        """Fuzzy/semantic stub matches the label and fills the field."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        # Stub treats lowercase+stripped label as the semantic key
        recall = _make_fuzzy_recall({"work authorization": "US Citizen"})
        plugin = self._make_plugin(store, recall_fn=recall)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "ready_to_submit"

    @pytest.mark.asyncio
    async def test_store_then_recall_end_to_end(self):
        """Store answer via jobs_answer_field then re-apply — field filled via recall."""
        store = _in_memory_store()
        _seed_shortlisted(store)

        # Shared in-memory bank backing both indexer and recall
        live_bank: dict = {}

        async def indexer(pid, q, a):
            live_bank[q] = a

        async def recall(pid, q):
            return live_bank.get(q)

        plugin = self._make_plugin(store, recall_fn=recall)
        plugin._index_answer_fn = indexer

        # First apply: Work Authorization missing -> awaiting-input
        result1 = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result1.is_error
        data1 = json.loads(result1.content)
        assert data1["status"] == "awaiting-input"

        # User provides the answer
        import plugins.job_search as jm
        jm._pending_application = None
        result2 = await plugin.call_tool(
            "jobs_answer_field", {"question": "Work Authorization", "answer": "US Citizen"}
        )
        assert not result2.is_error

        # Second apply: recall fills the field -> ready_to_submit
        result3 = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert not result3.is_error
        data3 = json.loads(result3.content)
        assert data3["status"] == "ready_to_submit"

    @pytest.mark.asyncio
    async def test_filled_field_value_present_in_response(self):
        """The recalled answer appears in the filled fields returned by apply_start."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        recall = _make_stub_recall({"Work Authorization": "US Citizen"})
        plugin = self._make_plugin(store, recall_fn=recall)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert not result.is_error
        data = json.loads(result.content)
        work_auth_field = next(
            (f for f in data["fields"] if f.get("label") == "Work Authorization"), None
        )
        assert work_auth_field is not None
        assert work_auth_field["value"] == "US Citizen"


# ── S6 fixtures ───────────────────────────────────────────────────────────────
#
# Stubs for: get_jobs_email_fn, create_ats_account_fn, store_ats_password_fn,
#            read_verify_link_fn, click_verify_link_fn, gen_password_fn.
# All async; no real network, inbox, browser, or keyring.

_JOBS_EMAIL = "jobs@example.com"
_FAKE_VERIFY_URL = "https://greenhouse.io/verify?token=abc123"
_FAKE_PASSWORD = "FakePass123!"


def _make_s6_plugin(
    store,
    *,
    jobs_email=_JOBS_EMAIL,
    create_ok=True,
    verify_url=_FAKE_VERIFY_URL,
    click_ok=True,
    driver_fn=None,
    second_driver_fn=None,
):
    """Build a plugin wired with S6 fakes. second_driver_fn replaces driver after login."""
    import plugins.job_search as jm
    jm._active_profile_id = _PROFILE_ID
    jm._pending_resume_path = _RESUME_PATH
    jm._pending_application = None
    jm._recall_fn = None

    # The driver call counter lets us swap behaviour after login.
    call_counter = [0]
    base_driver = driver_fn or _stub_apply_driver
    post_driver = second_driver_fn

    def _driver(url, dossier, resume_path):
        call_counter[0] += 1
        if call_counter[0] == 1 and post_driver is not None:
            # first call: signal needs_login (caller provides via driver_fn)
            return base_driver(url, dossier, resume_path)
        if call_counter[0] >= 2 and post_driver is not None:
            return post_driver(url, dossier, resume_path)
        return base_driver(url, dossier, resume_path)

    async def _get_email(pid):
        return jobs_email

    async def _create(url, email, password):
        return create_ok

    stored = {}

    async def _store_pw(pid, provider, email, password):
        stored[(pid, provider)] = (email, password)

    async def _read_link(pid):
        return verify_url

    async def _click(url):
        return click_ok

    from plugins.job_search import JobSearchPlugin
    p = JobSearchPlugin(
        store=store,
        extract_fn=_stub_extract,
        score_fn=_stub_scorer,
        apply_driver_fn=_driver,
        apply_submit_fn=_stub_apply_submit,
        get_jobs_email_fn=_get_email,
        create_ats_account_fn=_create,
        store_ats_password_fn=_store_pw,
        read_verify_link_fn=_read_link,
        click_verify_link_fn=_click,
        gen_password_fn=lambda: _FAKE_PASSWORD,
    )
    p._stored_passwords = stored  # expose for assertions
    return p


def _stub_driver_needs_login(url, dossier, resume_path):
    """Apply driver that signals the ATS requires login."""
    return {"needs_login": True, "ats_provider": "greenhouse"}


# ── Cycle 18 — ATS accounts store ────────────────────────────────────────────

class TestAtsAccountStore:
    def test_list_ats_accounts_empty(self):
        store = _in_memory_store()
        assert store.list_ats_accounts(_PROFILE_ID) == []

    def test_upsert_ats_account_stores(self):
        store = _in_memory_store()
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "created")
        accounts = store.list_ats_accounts(_PROFILE_ID)
        assert len(accounts) == 1
        assert accounts[0]["ats_provider"] == "greenhouse"
        assert accounts[0]["email"] == _JOBS_EMAIL
        assert accounts[0]["status"] == "created"

    def test_upsert_ats_account_dedup_on_provider(self):
        store = _in_memory_store()
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "created")
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "verified")
        accounts = store.list_ats_accounts(_PROFILE_ID)
        assert len(accounts) == 1
        assert accounts[0]["status"] == "verified"

    def test_get_ats_account_none(self):
        store = _in_memory_store()
        assert store.get_ats_account(_PROFILE_ID, "greenhouse") is None

    def test_get_ats_account_returns_row(self):
        store = _in_memory_store()
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "verified")
        acct = store.get_ats_account(_PROFILE_ID, "greenhouse")
        assert acct is not None
        assert acct["status"] == "verified"

    def test_different_profiles_isolated(self):
        store = _in_memory_store()
        store.upsert_ats_account(1, "greenhouse", "a@example.com", "created")
        store.upsert_ats_account(2, "greenhouse", "b@example.com", "created")
        assert store.get_ats_account(1, "greenhouse")["email"] == "a@example.com"
        assert store.get_ats_account(2, "greenhouse")["email"] == "b@example.com"

    def test_different_providers_stored_separately(self):
        store = _in_memory_store()
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "verified")
        store.upsert_ats_account(_PROFILE_ID, "lever", _JOBS_EMAIL, "created")
        assert len(store.list_ats_accounts(_PROFILE_ID)) == 2
        assert store.get_ats_account(_PROFILE_ID, "lever")["status"] == "created"


# ── Cycle 19 — _ensure_ats_login orchestration ───────────────────────────────

class TestEnsureAtsLogin:
    """Unit tests for _ensure_ats_login: all seams are fakes; no real network/email."""

    @pytest.mark.asyncio
    async def test_already_verified_returns_true_immediately(self):
        store = _in_memory_store()
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "verified")
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store)
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is True

    @pytest.mark.asyncio
    async def test_no_jobs_email_fn_returns_false(self):
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin
        import plugins.job_search as jm
        jm._get_jobs_email_fn = None
        plugin = JobSearchPlugin(store=store, get_jobs_email_fn=None)
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is False

    @pytest.mark.asyncio
    async def test_no_jobs_email_value_returns_false(self):
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=store, get_jobs_email_fn=lambda pid: None)
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is False

    @pytest.mark.asyncio
    async def test_create_account_failure_marks_failed_and_returns_false(self):
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin

        async def _email(pid): return _JOBS_EMAIL
        async def _create_fail(url, email, pw): return False

        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=_email,
            create_ats_account_fn=_create_fail,
        )
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is False
        acct = store.get_ats_account(_PROFILE_ID, "greenhouse")
        assert acct is not None
        assert acct["status"] == "failed"

    @pytest.mark.asyncio
    async def test_no_verify_link_returns_false_and_status_created(self):
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin

        async def _email(pid): return _JOBS_EMAIL
        async def _create_ok(url, email, pw): return True
        async def _no_link(pid): return None

        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=_email,
            create_ats_account_fn=_create_ok,
            read_verify_link_fn=_no_link,
        )
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is False
        # Status stays at 'created' (link never arrived)
        acct = store.get_ats_account(_PROFILE_ID, "greenhouse")
        assert acct is not None
        assert acct["status"] == "created"

    @pytest.mark.asyncio
    async def test_click_link_failure_marks_failed(self):
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin

        async def _email(pid): return _JOBS_EMAIL
        async def _create_ok(url, email, pw): return True
        async def _link(pid): return _FAKE_VERIFY_URL
        async def _click_fail(url): return False

        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=_email,
            create_ats_account_fn=_create_ok,
            read_verify_link_fn=_link,
            click_verify_link_fn=_click_fail,
        )
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is False
        acct = store.get_ats_account(_PROFILE_ID, "greenhouse")
        assert acct["status"] == "failed"

    @pytest.mark.asyncio
    async def test_happy_path_returns_true_and_marks_verified(self):
        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin

        async def _email(pid): return _JOBS_EMAIL
        async def _create_ok(url, email, pw): return True
        async def _link(pid): return _FAKE_VERIFY_URL
        async def _click_ok(url): return True

        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=_email,
            create_ats_account_fn=_create_ok,
            read_verify_link_fn=_link,
            click_verify_link_fn=_click_ok,
        )
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is True
        acct = store.get_ats_account(_PROFILE_ID, "greenhouse")
        assert acct is not None
        assert acct["status"] == "verified"
        assert acct["email"] == _JOBS_EMAIL

    @pytest.mark.asyncio
    async def test_happy_path_calls_store_password_fn(self):
        store = _in_memory_store()
        calls = []

        async def _email(pid): return _JOBS_EMAIL
        async def _create_ok(url, email, pw): return True
        async def _link(pid): return _FAKE_VERIFY_URL
        async def _click_ok(url): return True
        async def _store_pw(pid, provider, email, pw):
            calls.append((pid, provider, email, pw))

        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=_email,
            create_ats_account_fn=_create_ok,
            store_ats_password_fn=_store_pw,
            read_verify_link_fn=_link,
            click_verify_link_fn=_click_ok,
            gen_password_fn=lambda: _FAKE_PASSWORD,
        )
        await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert len(calls) == 1
        pid, provider, email, pw = calls[0]
        assert pid == _PROFILE_ID
        assert provider == "greenhouse"
        assert email == _JOBS_EMAIL
        assert pw == _FAKE_PASSWORD

    @pytest.mark.asyncio
    async def test_gen_password_fn_used(self):
        """Custom gen_password_fn is called; the generated password reaches create_account_fn."""
        passwords_used = []

        async def _email(pid): return _JOBS_EMAIL
        async def _create_capture(url, email, pw):
            passwords_used.append(pw)
            return True
        async def _link(pid): return _FAKE_VERIFY_URL
        async def _click_ok(url): return True

        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=_email,
            create_ats_account_fn=_create_capture,
            read_verify_link_fn=_link,
            click_verify_link_fn=_click_ok,
            gen_password_fn=lambda: "CustomPass!42",
        )
        await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert passwords_used == ["CustomPass!42"]

    @pytest.mark.asyncio
    async def test_default_password_generated_when_no_fn(self):
        """When gen_password_fn is None, secrets.token_urlsafe is used (non-empty, URL-safe)."""
        import re as _re
        passwords = []

        async def _email(pid): return _JOBS_EMAIL
        async def _create_capture(url, email, pw):
            passwords.append(pw)
            return True
        async def _link(pid): return _FAKE_VERIFY_URL
        async def _click_ok(url): return True

        store = _in_memory_store()
        from plugins.job_search import JobSearchPlugin
        import plugins.job_search as jm
        jm._gen_password_fn = None
        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=_email,
            create_ats_account_fn=_create_capture,
            read_verify_link_fn=_link,
            click_verify_link_fn=_click_ok,
            gen_password_fn=None,
        )
        await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert len(passwords) == 1
        assert len(passwords[0]) >= 16
        assert _re.match(r"^[A-Za-z0-9_-]+$", passwords[0])

    @pytest.mark.asyncio
    async def test_idempotent_second_call_with_verified_account(self):
        """Second call when account is already verified skips all creation steps."""
        store = _in_memory_store()
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "verified")
        create_calls = []

        async def _create_should_not_be_called(url, email, pw):
            create_calls.append(pw)
            return True

        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(
            store=store,
            get_jobs_email_fn=lambda pid: _JOBS_EMAIL,
            create_ats_account_fn=_create_should_not_be_called,
        )
        result = await plugin._ensure_ats_login(_PROFILE_ID, "greenhouse", _ATS_URL_1)
        assert result is True
        assert create_calls == []  # account creation never called


# ── Cycle 20 — needs_login flow in jobs_apply_start ──────────────────────────

class TestApplyStartNeedsLogin:
    """S6: apply_driver signals needs_login -> ensure_ats_login -> retry driver."""

    @pytest.mark.asyncio
    async def test_needs_login_triggers_account_creation_and_returns_ready(self):
        """Full happy path: driver needs login -> account created -> retry succeeds."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_s6_plugin(
            store,
            driver_fn=_stub_driver_needs_login,
            second_driver_fn=_stub_apply_driver,  # second call returns filled fields
        )
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "ready_to_submit"

    @pytest.mark.asyncio
    async def test_needs_login_marks_ats_account_verified(self):
        """ATS account row is written to the store after a successful login flow."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_s6_plugin(
            store,
            driver_fn=_stub_driver_needs_login,
            second_driver_fn=_stub_apply_driver,
        )
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        acct = store.get_ats_account(_PROFILE_ID, "greenhouse")
        assert acct is not None
        assert acct["status"] == "verified"

    @pytest.mark.asyncio
    async def test_needs_login_no_jobs_email_returns_failed(self):
        """Missing jobs email -> login fails -> application marked failed."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        # Override: no jobs email
        plugin = _make_s6_plugin(store, jobs_email=None, driver_fn=_stub_driver_needs_login)
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "failed"
        app = store.get_application(_ATS_URL_1)
        assert app is not None
        assert app["status"] == "failed"

    @pytest.mark.asyncio
    async def test_needs_login_account_create_failure_returns_failed(self):
        """Account creation fails -> login fails -> application marked failed."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_s6_plugin(
            store,
            create_ok=False,
            driver_fn=_stub_driver_needs_login,
        )
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_needs_login_no_verify_link_returns_failed(self):
        """No verification link in inbox -> login fails."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_s6_plugin(
            store,
            verify_url=None,
            driver_fn=_stub_driver_needs_login,
        )
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_needs_login_click_fail_returns_failed(self):
        """Verification link click fails -> login fails."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        plugin = _make_s6_plugin(
            store,
            click_ok=False,
            driver_fn=_stub_driver_needs_login,
        )
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_needs_login_retry_still_needs_login_returns_failed(self):
        """Even after login, driver still signals needs_login -> bail."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        # both calls return needs_login
        plugin = _make_s6_plugin(
            store,
            driver_fn=_stub_driver_needs_login,
            second_driver_fn=_stub_driver_needs_login,
        )
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert result.is_error
        data = json.loads(result.content)
        assert data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_already_verified_skips_creation_on_second_apply(self):
        """If account already verified, driver skips creation on re-apply."""
        store = _in_memory_store()
        _seed_shortlisted(store)
        # Pre-seed verified account
        store.upsert_ats_account(_PROFILE_ID, "greenhouse", _JOBS_EMAIL, "verified")

        create_calls = []
        async def _create_track(url, email, pw):
            create_calls.append(pw)
            return True

        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = _RESUME_PATH
        jm._pending_application = None
        jm._recall_fn = None

        from plugins.job_search import JobSearchPlugin
        # two-shot driver: first signals needs_login, second fills OK
        call_n = [0]
        def _two_shot(url, dossier, resume_path):
            call_n[0] += 1
            if call_n[0] == 1:
                return {"needs_login": True, "ats_provider": "greenhouse"}
            return _stub_apply_driver(url, dossier, resume_path)

        plugin = JobSearchPlugin(
            store=store,
            extract_fn=_stub_extract,
            score_fn=_stub_scorer,
            apply_driver_fn=_two_shot,
            apply_submit_fn=_stub_apply_submit,
            get_jobs_email_fn=lambda pid: _JOBS_EMAIL,
            create_ats_account_fn=_create_track,
            read_verify_link_fn=lambda pid: _FAKE_VERIFY_URL,
            click_verify_link_fn=lambda url: True,
            gen_password_fn=lambda: _FAKE_PASSWORD,
        )
        result = await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        assert not result.is_error
        # Account was already verified; _ensure_ats_login returned True immediately
        assert create_calls == []


# ── Cycle 21 — capabilities + meta ───────────────────────────────────────────

class TestPluginMetaS6:
    def test_secrets_read_in_capabilities(self):
        from plugins.job_search import REQUIRED_CAPABILITIES
        assert "secrets_read" in REQUIRED_CAPABILITIES

    def test_tool_list_has_expected_tools(self):
        """S6 adds no new tools; S7 adds jobs_set_auto_submit."""
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert names == {
            "jobs_fetch_postings", "jobs_store_resume", "jobs_score_shortlist",
            "jobs_set_approval", "jobs_apply_start", "jobs_apply_submit",
            "jobs_answer_field",
            "jobs_set_auto_submit",  # S7 #340
        }

    def test_create_factory_accepts_s6_seams(self):
        from plugins.job_search import create, JobSearchPlugin
        p = create(
            gen_password_fn=lambda: "pw",
            get_jobs_email_fn=lambda pid: "jobs@example.com",
        )
        assert isinstance(p, JobSearchPlugin)


# ── S7 helpers ────────────────────────────────────────────────────────────────

def _pending_app_clean():
    """Pending application with all Known fields — passes zero-guessed check."""
    return {
        "url": _ATS_URL_1,
        "ats_type": "greenhouse",
        "fields": [
            {"label": "First Name", "value": "John",               "required": True,  "is_known": True},
            {"label": "Email",      "value": "john@example.com",   "required": True,  "is_known": True},
        ],
        "has_new_eligibility": False,
    }


def _pending_app_guessed():
    """Pending application with one guessed (is_known=False) field."""
    return {
        "url": _ATS_URL_1,
        "ats_type": "greenhouse",
        "fields": [
            {"label": "First Name",     "value": "John",         "required": True, "is_known": True},
            {"label": "Desired Salary", "value": "$80,000",      "required": True, "is_known": False},
        ],
        "has_new_eligibility": False,
    }


def _pending_app_new_eligibility():
    """Pending application with a newly-encountered eligibility question."""
    return {
        "url": _ATS_URL_1,
        "ats_type": "greenhouse",
        "fields": [
            {"label": "First Name",         "value": "John",  "required": True, "is_known": True},
            {"label": "Work Authorization", "value": "Yes",   "required": True, "is_known": False},
        ],
        "has_new_eligibility": True,
    }


def _store_with_ramp_complete(auto_submit_enabled=True, reviewed_count=5, threshold=5):
    store = _in_memory_store()
    store.set_auto_submit(_PROFILE_ID, auto_submit_enabled)
    if reviewed_count > 0:
        for _ in range(reviewed_count):
            store.increment_reviewed_count(_PROFILE_ID)
    return store


def _make_auto_submit_plugin(store, *, auto_submit=True):
    """Plugin wired for auto-submit gate tests."""
    import plugins.job_search as jm
    jm._active_profile_id = _PROFILE_ID
    jm._pending_resume_path = _RESUME_PATH
    jm._pending_application = None
    jm._recall_fn = None
    from plugins.job_search import JobSearchPlugin
    return JobSearchPlugin(
        store=store,
        extract_fn=_stub_extract,
        score_fn=_stub_scorer,
        apply_driver_fn=_stub_apply_driver,
        apply_submit_fn=_stub_apply_submit,
    )


# ── Cycle 22 — job_search_settings store ─────────────────────────────────────

class TestJobSettingsStore:
    def test_get_settings_returns_defaults_for_new_profile(self):
        store = _in_memory_store()
        s = store.get_job_settings(_PROFILE_ID)
        assert s["auto_submit_enabled"] == 0
        assert s["reviewed_count"] == 0
        assert s["ramp_threshold"] == 5

    def test_set_auto_submit_true(self):
        store = _in_memory_store()
        store.set_auto_submit(_PROFILE_ID, True)
        s = store.get_job_settings(_PROFILE_ID)
        assert s["auto_submit_enabled"] == 1

    def test_set_auto_submit_false(self):
        store = _in_memory_store()
        store.set_auto_submit(_PROFILE_ID, True)
        store.set_auto_submit(_PROFILE_ID, False)
        s = store.get_job_settings(_PROFILE_ID)
        assert s["auto_submit_enabled"] == 0

    def test_increment_reviewed_count(self):
        store = _in_memory_store()
        n = store.increment_reviewed_count(_PROFILE_ID)
        assert n == 1
        n2 = store.increment_reviewed_count(_PROFILE_ID)
        assert n2 == 2

    def test_increment_reviewed_count_from_zero(self):
        store = _in_memory_store()
        store.increment_reviewed_count(_PROFILE_ID)
        store.increment_reviewed_count(_PROFILE_ID)
        s = store.get_job_settings(_PROFILE_ID)
        assert s["reviewed_count"] == 2

    def test_different_profiles_isolated(self):
        store = _in_memory_store()
        store.set_auto_submit(1, True)
        store.increment_reviewed_count(1)
        assert store.get_job_settings(2)["auto_submit_enabled"] == 0
        assert store.get_job_settings(2)["reviewed_count"] == 0


# ── Cycle 23 — check_auto_submit_gate pure function ──────────────────────────

class TestAutoSubmitGate:
    """Gate logic: 5 branches — opted-out, pre-ramp, guessed-field,
    new-eligibility, clean-auto. NO real submissions."""

    def test_opted_out_fails(self):
        from plugins.job_search import check_auto_submit_gate
        store = _in_memory_store()
        # auto_submit_enabled defaults to False
        ok, reason = check_auto_submit_gate(_PROFILE_ID, store, _pending_app_clean())
        assert not ok
        assert "opted" in reason

    def test_pre_ramp_fails(self):
        from plugins.job_search import check_auto_submit_gate
        store = _in_memory_store()
        store.set_auto_submit(_PROFILE_ID, True)
        # reviewed_count = 0, threshold = 5 → ramp incomplete
        ok, reason = check_auto_submit_gate(_PROFILE_ID, store, _pending_app_clean())
        assert not ok
        assert "ramp" in reason.lower() or "pre-ramp" in reason.lower()

    def test_guessed_field_fails(self):
        from plugins.job_search import check_auto_submit_gate
        store = _store_with_ramp_complete()
        ok, reason = check_auto_submit_gate(_PROFILE_ID, store, _pending_app_guessed())
        assert not ok
        assert "guessed" in reason.lower() or "salary" in reason.lower()

    def test_new_eligibility_fails(self):
        from plugins.job_search import check_auto_submit_gate
        store = _store_with_ramp_complete()
        ok, reason = check_auto_submit_gate(_PROFILE_ID, store, _pending_app_new_eligibility())
        assert not ok
        assert "eligibility" in reason.lower() or "work authorization" in reason.lower()

    def test_clean_auto_passes(self):
        from plugins.job_search import check_auto_submit_gate
        store = _store_with_ramp_complete()
        ok, reason = check_auto_submit_gate(_PROFILE_ID, store, _pending_app_clean())
        assert ok
        assert reason == "ok"

    def test_eligibility_before_guessed_in_check_order(self):
        """Eligibility check comes before guessed-field check per ADR-0009."""
        from plugins.job_search import check_auto_submit_gate
        store = _store_with_ramp_complete()
        # Field is both eligibility AND not known — eligibility reason should appear
        pending = {
            "url": _ATS_URL_1,
            "ats_type": "greenhouse",
            "fields": [
                {"label": "Work Authorization", "value": "Yes", "required": True, "is_known": False},
            ],
        }
        ok, reason = check_auto_submit_gate(_PROFILE_ID, store, pending)
        assert not ok
        assert "eligibility" in reason.lower()

    def test_partial_ramp_still_fails(self):
        from plugins.job_search import check_auto_submit_gate
        store = _in_memory_store()
        store.set_auto_submit(_PROFILE_ID, True)
        store.increment_reviewed_count(_PROFILE_ID)  # 1 / 5
        ok, reason = check_auto_submit_gate(_PROFILE_ID, store, _pending_app_clean())
        assert not ok

    def test_exactly_at_threshold_passes(self):
        from plugins.job_search import check_auto_submit_gate
        store = _in_memory_store()
        store.set_auto_submit(_PROFILE_ID, True)
        for _ in range(5):
            store.increment_reviewed_count(_PROFILE_ID)
        ok, _ = check_auto_submit_gate(_PROFILE_ID, store, _pending_app_clean())
        assert ok

    def test_eligibility_known_does_not_fail(self):
        """Eligibility question filled from bank (is_known=True) does NOT fail gate."""
        from plugins.job_search import check_auto_submit_gate
        store = _store_with_ramp_complete()
        pending = {
            "url": _ATS_URL_1,
            "ats_type": "greenhouse",
            "fields": [
                {"label": "Work Authorization", "value": "Yes", "required": True, "is_known": True},
            ],
        }
        ok, _ = check_auto_submit_gate(_PROFILE_ID, store, pending)
        assert ok


# ── Cycle 24 — is_eligibility_question ───────────────────────────────────────

class TestIsEligibilityQuestion:
    def test_work_authorization(self):
        from plugins.job_search import is_eligibility_question
        assert is_eligibility_question("Work Authorization")

    def test_sponsorship(self):
        from plugins.job_search import is_eligibility_question
        assert is_eligibility_question("Will you require sponsorship?")

    def test_salary(self):
        from plugins.job_search import is_eligibility_question
        assert is_eligibility_question("Desired Salary")

    def test_relocation(self):
        from plugins.job_search import is_eligibility_question
        assert is_eligibility_question("Are you willing to relocate?")

    def test_start_date(self):
        from plugins.job_search import is_eligibility_question
        assert is_eligibility_question("Earliest Start Date")

    def test_non_eligibility(self):
        from plugins.job_search import is_eligibility_question
        assert not is_eligibility_question("First Name")
        assert not is_eligibility_question("LinkedIn Profile URL")


# ── Cycle 25 — jobs_set_auto_submit tool ─────────────────────────────────────

class TestSetAutoSubmitTool:
    def _make_plugin(self, store):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        return JobSearchPlugin(store=store)

    @pytest.mark.asyncio
    async def test_enable_auto_submit(self):
        store = _in_memory_store()
        plugin = self._make_plugin(store)
        result = await plugin.call_tool("jobs_set_auto_submit", {"enabled": True})
        assert not result.is_error
        import json
        data = json.loads(result.content)
        assert data["auto_submit_enabled"] is True

    @pytest.mark.asyncio
    async def test_disable_auto_submit(self):
        store = _in_memory_store()
        store.set_auto_submit(_PROFILE_ID, True)
        plugin = self._make_plugin(store)
        result = await plugin.call_tool("jobs_set_auto_submit", {"enabled": False})
        assert not result.is_error
        import json
        data = json.loads(result.content)
        assert data["auto_submit_enabled"] is False

    @pytest.mark.asyncio
    async def test_no_profile_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = None
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=_in_memory_store())
        result = await plugin.call_tool("jobs_set_auto_submit", {"enabled": True})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self):
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(store=None)
        result = await plugin.call_tool("jobs_set_auto_submit", {"enabled": True})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_response_includes_ramp_info(self):
        store = _in_memory_store()
        store.increment_reviewed_count(_PROFILE_ID)
        plugin = self._make_plugin(store)
        result = await plugin.call_tool("jobs_set_auto_submit", {"enabled": True})
        import json
        data = json.loads(result.content)
        assert data["reviewed_count"] == 1
        assert data["ramp_threshold"] == 5


# ── Cycle 26 — _apply_submit ramp tracking ───────────────────────────────────

class TestApplySubmitRampTracking:
    """S7: reviewed_count increments on modal-confirmed (gate-failed) submissions;
    does NOT increment on auto-submit (gate-passed) submissions."""

    def _seed_and_start(self, store):
        """Seed a shortlisted posting, start the apply flow, return plugin."""
        _seed_shortlisted(store)
        import plugins.job_search as jm
        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = _RESUME_PATH
        jm._pending_application = None
        jm._recall_fn = None
        from plugins.job_search import JobSearchPlugin
        return JobSearchPlugin(
            store=store,
            extract_fn=_stub_extract,
            score_fn=_stub_scorer,
            apply_driver_fn=_stub_apply_driver,
            apply_submit_fn=_stub_apply_submit,
        )

    @pytest.mark.asyncio
    async def test_modal_path_increments_reviewed_count(self):
        """Gate fails (pre-ramp) → modal path → reviewed_count incremented."""
        store = _in_memory_store()
        # auto_submit disabled → gate always fails → modal path
        plugin = self._seed_and_start(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        await plugin.call_tool("jobs_apply_submit", {})
        s = store.get_job_settings(_PROFILE_ID)
        assert s["reviewed_count"] == 1

    @pytest.mark.asyncio
    async def test_modal_path_accumulates_across_submissions(self):
        """Each modal-confirmed submission increments the count."""
        store = _in_memory_store()
        plugin = self._seed_and_start(store)
        # First apply cycle
        _seed_shortlisted(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        await plugin.call_tool("jobs_apply_submit", {})
        # Second apply cycle (different URL)
        store.upsert({"url": _ATS_URL_2, "title": "Job2", "company": "Corp2",
                      "pay": "", "snapshot": "", "posted_date": ""})
        store.set_status(_ATS_URL_2, "shortlisted")
        store.upsert_application(_ATS_URL_2, _ATS_URL_2, "lever", "shortlisted", [])
        import plugins.job_search as jm
        jm._pending_application = None
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_2})
        await plugin.call_tool("jobs_apply_submit", {})
        s = store.get_job_settings(_PROFILE_ID)
        assert s["reviewed_count"] == 2

    @pytest.mark.asyncio
    async def test_auto_submit_does_not_increment_reviewed_count(self):
        """Gate passes (all conditions met) → auto-submit → count NOT incremented."""
        store = _store_with_ramp_complete()
        plugin = self._seed_and_start(store)
        initial = store.get_job_settings(_PROFILE_ID)["reviewed_count"]
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        await plugin.call_tool("jobs_apply_submit", {})
        final = store.get_job_settings(_PROFILE_ID)["reviewed_count"]
        assert final == initial  # unchanged

    @pytest.mark.asyncio
    async def test_submit_result_includes_auto_submitted_flag(self):
        """Response body includes auto_submitted=True when gate passed."""
        store = _store_with_ramp_complete()
        plugin = self._seed_and_start(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        result = await plugin.call_tool("jobs_apply_submit", {})
        import json
        data = json.loads(result.content)
        assert data["auto_submitted"] is True

    @pytest.mark.asyncio
    async def test_modal_submit_result_has_auto_submitted_false(self):
        """Response body includes auto_submitted=False when gate failed (modal path)."""
        store = _in_memory_store()  # gate fails: opted-out
        plugin = self._seed_and_start(store)
        await plugin.call_tool("jobs_apply_start", {"url": _ATS_URL_1})
        result = await plugin.call_tool("jobs_apply_submit", {})
        import json
        data = json.loads(result.content)
        assert data["auto_submitted"] is False


# ── Cycle 27 — ModalSurface auto_gate_fn (ADR-0009) ─────────────────────────

class TestModalSurfaceAutoGate:
    """Unit tests for the auto_gate_fn bypass in ModalSurface (ADR-0009 exception)."""

    @pytest.mark.asyncio
    async def test_auto_gate_approves_when_true(self):
        """auto_gate_fn returning True → SILENT (auto-approve) without prompting."""
        from cerebral.security.modal import ModalSurface
        from cerebral.security.gate import Decision, Capability

        prompt_called = []
        async def _prompt(req):
            prompt_called.append(req)
            return "cancel"

        surface = ModalSurface(
            prompt_fn=_prompt,
            auto_gate_fn=lambda tool, args: True,
        )
        result = await surface.request(Capability.EXTERNAL_DATA_WRITE, "jobs_apply_submit", {})
        assert result is Decision.SILENT
        assert prompt_called == []  # modal was NOT shown

    @pytest.mark.asyncio
    async def test_auto_gate_false_falls_through_to_modal(self):
        """auto_gate_fn returning False → modal prompt is shown normally."""
        from cerebral.security.modal import ModalSurface
        from cerebral.security.gate import Decision, Capability

        prompt_called = []
        async def _prompt(req):
            prompt_called.append(req)
            return "accept"

        surface = ModalSurface(
            prompt_fn=_prompt,
            has_subscriber_fn=lambda: True,
            auto_gate_fn=lambda tool, args: False,
        )
        result = await surface.request(Capability.EXTERNAL_DATA_WRITE, "jobs_apply_submit", {})
        assert result is Decision.SILENT
        assert len(prompt_called) == 1  # modal WAS shown

    @pytest.mark.asyncio
    async def test_auto_gate_none_falls_through_to_modal(self):
        """No auto_gate_fn → modal prompt shown as before."""
        from cerebral.security.modal import ModalSurface
        from cerebral.security.gate import Decision, Capability

        prompt_called = []
        async def _prompt(req):
            prompt_called.append(req)
            return "cancel"

        surface = ModalSurface(prompt_fn=_prompt, has_subscriber_fn=lambda: True)
        result = await surface.request(Capability.EXTERNAL_DATA_WRITE, "jobs_apply_submit", {})
        assert result is Decision.DENY
        assert len(prompt_called) == 1

    def test_set_auto_gate_fn_setter(self):
        """set_auto_gate_fn installs / updates the gate function."""
        from cerebral.security.modal import ModalSurface

        async def _dummy_prompt(req): return "cancel"
        surface = ModalSurface(prompt_fn=_dummy_prompt)
        assert surface._auto_gate_fn is None
        surface.set_auto_gate_fn(lambda tool, args: True)
        assert surface._auto_gate_fn is not None
        surface.set_auto_gate_fn(None)
        assert surface._auto_gate_fn is None


# ── Cycle 28 — S7 meta ───────────────────────────────────────────────────────

class TestPluginMetaS7:
    def test_jobs_set_auto_submit_in_tool_list(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert "jobs_set_auto_submit" in names

    def test_jobs_set_auto_submit_not_irreversible(self):
        """Toggling auto-submit is reversible — no modal needed."""
        from plugins.job_search import JobSearchPlugin
        tool = next(t for t in JobSearchPlugin().list_tools() if t.name == "jobs_set_auto_submit")
        assert not tool.irreversible

    def test_jobs_apply_submit_still_irreversible(self):
        """jobs_apply_submit retains irreversible=True; gate is the bypass, not removal."""
        from plugins.job_search import JobSearchPlugin
        tool = next(t for t in JobSearchPlugin().list_tools() if t.name == "jobs_apply_submit")
        assert tool.irreversible

    def test_eligibility_keywords_nonempty(self):
        from plugins.job_search import ELIGIBILITY_KEYWORDS
        assert len(ELIGIBILITY_KEYWORDS) > 5


# ── file fallback — jobs_store_resume prefers the stored PDF over the arg ────

class TestStoreResumeFileFallback:
    @pytest.mark.asyncio
    async def test_empty_arg_falls_back_to_stored_pdf(self, tmp_path, monkeypatch):
        import cerebral.db.attachments as att
        import plugins.job_search as jm
        from plugins.job_search import JobSearchPlugin

        pdf = tmp_path / "resume.pdf"
        pdf.write_bytes(b"%PDF-fake")
        monkeypatch.setattr(att, "_extract_pdf_text", lambda data: RESUME_FIXTURE_TEXT)
        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = str(pdf)

        store = _in_memory_store()
        plugin = JobSearchPlugin(store=store, extract_fn=_stub_extract)
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": ""})
        assert not result.is_error
        d = store.get_dossier(_PROFILE_ID)
        assert RESUME_FIXTURE_TEXT[:100] in d["raw_text"]

    @pytest.mark.asyncio
    async def test_longer_file_text_wins_over_truncated_arg(self, tmp_path, monkeypatch):
        import cerebral.db.attachments as att
        import plugins.job_search as jm
        from plugins.job_search import JobSearchPlugin

        pdf = tmp_path / "resume.pdf"
        pdf.write_bytes(b"%PDF-fake")
        monkeypatch.setattr(att, "_extract_pdf_text", lambda data: RESUME_FIXTURE_TEXT)
        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = str(pdf)

        store = _in_memory_store()
        plugin = JobSearchPlugin(store=store, extract_fn=_stub_extract)
        truncated = RESUME_FIXTURE_TEXT[:50]
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": truncated})
        assert not result.is_error
        d = store.get_dossier(_PROFILE_ID)
        assert RESUME_FIXTURE_TEXT[:100] in d["raw_text"]

    @pytest.mark.asyncio
    async def test_missing_file_and_empty_arg_still_errors(self):
        import plugins.job_search as jm
        from plugins.job_search import JobSearchPlugin

        jm._active_profile_id = _PROFILE_ID
        jm._pending_resume_path = "/nonexistent/resume.pdf"
        plugin = JobSearchPlugin(store=_in_memory_store(), extract_fn=_stub_extract)
        result = await plugin.call_tool("jobs_store_resume", {"pdf_text": "   "})
        assert result.is_error


# ── live labeled-entry format (issue #380) ────────────────────────────────────

_LIVE_RRR_HTML = """
<p><strong>Company</strong>: Muck Rack<!-- rrr-job-id: RRR-20260622-038 --><br>
<strong>Job/Gig</strong>: <a href="https://ratracerebellion.com/remote-press-ops-26/" target="_blank"><strong>Night Shift &#8212; Remote Operations Specialist</strong></a><strong><a href="https://ratracerebellion.com/remote-press-ops-26/">&#128293;</a></strong><br>
<strong>Pay</strong>: $27.00/hr.</p>
<p><strong>Snapshot</strong>: Assist in distributing press releases effectively.</p> <hr>
<p><strong>Company</strong>: Cigna<!-- rrr-job-id: RRR-20260630-043 --><br>
<strong>Job/Gig</strong>: <a href="https://ratracerebellion.com/remote-claims-rep-26/"><strong>Non-Phone &#8212; Claims Representative Remote</strong></a><br>
<strong>Pay</strong>: $17.75 &#8211; $26.00/hr.</p>
<p><strong>Snapshot</strong>: Process claims remotely.</p>
"""


class TestLiveLabeledFormat:
    def test_parses_labeled_entries(self):
        from plugins.job_search import parse_postings

        postings = parse_postings(_LIVE_RRR_HTML)
        assert len(postings) == 2
        first = postings[0]
        assert first["company"] == "Muck Rack"
        assert "Remote Operations Specialist" in first["title"]
        assert first["pay"] == "$27.00/hr."
        assert first["posted_date"] == "2026-06-22"
        assert first["url"] == "https://ratracerebellion.com/remote-press-ops-26/"
        assert first["snapshot"].startswith("Assist in distributing")

    def test_entities_unescaped_and_tags_stripped(self):
        from plugins.job_search import parse_postings

        postings = parse_postings(_LIVE_RRR_HTML)
        assert "—" in postings[0]["title"]        # &#8212; em dash
        assert "<strong>" not in postings[0]["title"]
        assert "–" in postings[1]["pay"]          # &#8211; en dash

    def test_legacy_fixture_format_still_parses(self):
        from plugins.job_search import parse_postings

        legacy = (
            '<h2><a href="https://ratracerebellion.com/some-job/">Support Rep — Acme</a></h2>'
            '<p>Help customers. $20/hr</p>'
            '<a href="https://boards.greenhouse.io/acme/jobs/1">Apply</a>'
        )
        postings = parse_postings(legacy)
        assert len(postings) == 1
        assert postings[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
