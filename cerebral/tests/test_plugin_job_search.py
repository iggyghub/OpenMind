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
        assert names == {"jobs_fetch_postings", "jobs_store_resume"}  # S1 + S2

    def test_required_capabilities(self):
        from plugins.job_search import REQUIRED_CAPABILITIES
        assert REQUIRED_CAPABILITIES == frozenset({
            "external_data_read",
            "network_egress_cloud",
            "fs_write",
            "fs_read",   # S2: read resume PDF artifact
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

class TestJobsFetchPostings:
    @pytest.mark.asyncio
    async def test_returns_parsed_count(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
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
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_HTML), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        data = json.loads(result.content)
        urls = {p["url"] for p in data["postings"]}
        assert "https://boards.greenhouse.io/acme/jobs/123" in urls

    @pytest.mark.asyncio
    async def test_skips_entries_without_ats_url(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_NO_LINKS), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        data = json.loads(result.content)
        assert data["saved"] == 0     # no outbound URL -> not stored
        assert data["fetched"] == 1   # still parsed 1 entry

    @pytest.mark.asyncio
    async def test_dedup_on_second_fetch(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_HTML), store=store)
        await plugin.call_tool("jobs_fetch_postings", {})
        result2 = await plugin.call_tool("jobs_fetch_postings", {})
        data = json.loads(result2.content)
        assert store.count() == 2     # dedup: still 2 rows

    @pytest.mark.asyncio
    async def test_dedup_within_fixture(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_DUPE), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        assert store.count() == 1    # two entries, same ATS URL -> 1 row

    @pytest.mark.asyncio
    async def test_navigate_failure_returns_error(self):
        from plugins.job_search import JobSearchPlugin
        store = _in_memory_store()
        plugin = JobSearchPlugin(navigate_fn=_make_failing_navigate(), store=store)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self):
        from plugins.job_search import JobSearchPlugin
        plugin = JobSearchPlugin(navigate_fn=_make_navigate(RRR_FIXTURE_HTML), store=None)
        result = await plugin.call_tool("jobs_fetch_postings", {})
        assert result.is_error


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
