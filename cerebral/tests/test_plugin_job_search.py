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

    def test_lists_one_tool(self):
        from plugins.job_search import JobSearchPlugin
        names = {t.name for t in JobSearchPlugin().list_tools()}
        assert names == {"jobs_fetch_postings"}

    def test_required_capabilities(self):
        from plugins.job_search import REQUIRED_CAPABILITIES
        assert REQUIRED_CAPABILITIES == frozenset({
            "external_data_read",
            "network_egress_cloud",
            "fs_write",
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
