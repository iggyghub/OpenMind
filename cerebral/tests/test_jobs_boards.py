"""S1 #396 / S2 #397 / B1 #508 / B2 #509 / B4 #511 — job_boards CRUD, _fetch_postings loop,
board-provider seam, Greenhouse/Lever API providers, JobSpy big-board provider.

All tests use in-memory SQLite and stubbed fetch fns; no live network.
"""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

import plugins.job_search as _jmod
from plugins.job_search import (
    JobSearchStore, JobSearchPlugin,
    detect_board_provider, BOARD_PROVIDERS,
    set_board_api_fetch_fn, _extract_board_slug,
    set_jobspy_fetch_fn,
    set_active_profile_id, set_store,
)


class _MemStore(JobSearchStore):
    """In-memory SQLite store — bypasses the db_path mkdir for unit tests."""
    def __init__(self):
        self._con = sqlite3.connect(":memory:", check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init()


def _mem_store() -> JobSearchStore:
    return _MemStore()


def test_list_boards_empty():
    s = _mem_store()
    assert s.list_boards() == []


def test_add_and_list_board():
    s = _mem_store()
    s.add_board("https://example.com/jobs", "Example")
    boards = s.list_boards()
    assert len(boards) == 1
    assert boards[0]["url"] == "https://example.com/jobs"
    assert boards[0]["label"] == "Example"
    assert boards[0]["enabled"] == 1


def test_add_board_default_label():
    s = _mem_store()
    s.add_board("https://example.com/jobs")
    assert s.list_boards()[0]["label"] == ""


def test_add_board_strips_whitespace():
    s = _mem_store()
    s.add_board("  https://example.com/jobs  ", "  label  ")
    b = s.list_boards()[0]
    assert b["url"] == "https://example.com/jobs"
    assert b["label"] == "label"


def test_add_board_duplicate_rolls_back():
    s = _mem_store()
    s.add_board("https://example.com/jobs")
    with pytest.raises(Exception):
        s.add_board("https://example.com/jobs")  # UNIQUE violation
    # Connection must still be usable (rolled back, not wedged)
    assert len(s.list_boards()) == 1


def test_remove_board():
    s = _mem_store()
    s.add_board("https://a.com")
    s.add_board("https://b.com")
    s.remove_board("https://a.com")
    urls = [b["url"] for b in s.list_boards()]
    assert "https://a.com" not in urls
    assert "https://b.com" in urls


def test_set_board_enabled_false():
    s = _mem_store()
    s.add_board("https://a.com")
    s.set_board_enabled("https://a.com", False)
    assert s.list_boards()[0]["enabled"] == 0


def test_set_board_enabled_true():
    s = _mem_store()
    s.add_board("https://a.com")
    s.set_board_enabled("https://a.com", False)
    s.set_board_enabled("https://a.com", True)
    assert s.list_boards()[0]["enabled"] == 1


# ── _fetch_postings multi-board loop ─────────────────────────────────────────

def _make_plugin(store, navigate_fn):
    """Minimal plugin wired with a fake navigate and in-memory store."""
    return JobSearchPlugin(navigate_fn=navigate_fn, store=store)


_FAKE_HTML = """<h2><a href="https://ratracerebellion.com/job/123">Writer at Acme</a></h2>
<p>$50,000/year remote writing gig</p>
<a href="https://greenhouse.io/apply/123">Apply</a>"""


async def test_fetch_zero_boards_returns_hint():
    store = _mem_store()
    navigated: list[str] = []

    async def fake_nav(url):
        navigated.append(url)
        return _FAKE_HTML

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    data = json.loads(result.content)
    assert not result.is_error
    assert "hint" in data
    assert data["fetched"] == 0
    assert data["saved"] == 0
    assert navigated == []  # no board → no navigate call


async def test_fetch_uses_board_url_not_rrr_url():
    from plugins.job_search import RRR_URL
    store = _mem_store()
    store.add_board("https://example.com/jobs", "Example")
    navigated: list[str] = []

    async def fake_nav(url):
        navigated.append(url)
        return _FAKE_HTML

    plugin = _make_plugin(store, fake_nav)
    await plugin._fetch_postings()
    assert "https://example.com/jobs" in navigated
    assert RRR_URL not in navigated  # hardcoded constant no longer consulted


async def test_fetch_per_board_counts():
    store = _mem_store()
    store.add_board("https://a.com/jobs")
    store.add_board("https://b.com/jobs")

    async def fake_nav(url):
        return _FAKE_HTML  # both boards return the same fixture

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    data = json.loads(result.content)
    assert len(data["per_board"]) == 2
    assert all("fetched" in b for b in data["per_board"])


async def test_fetch_failing_board_does_not_abort_others():
    store = _mem_store()
    store.add_board("https://bad.com/jobs")
    store.add_board("https://good.com/jobs")

    async def fake_nav(url):
        if "bad" in url:
            raise RuntimeError("network error")
        return _FAKE_HTML

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    assert not result.is_error
    data = json.loads(result.content)
    bad = next(b for b in data["per_board"] if "bad" in b["url"])
    good = next(b for b in data["per_board"] if "good" in b["url"])
    assert "error" in bad
    assert good["fetched"] > 0


async def test_fetch_disabled_board_skipped():
    store = _mem_store()
    store.add_board("https://disabled.com/jobs")
    store.set_board_enabled("https://disabled.com/jobs", False)
    navigated: list[str] = []

    async def fake_nav(url):
        navigated.append(url)
        return _FAKE_HTML

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    data = json.loads(result.content)
    assert navigated == []  # disabled board not fetched
    assert "hint" in data  # same as zero-boards path


# ── S2 #397 — LLM posting extractor fallback ─────────────────────────────────

_EMPTY_HTML = "<html><body><p>No jobs here.</p></body></html>"

_LLM_POSTINGS = [
    {
        "title": "Remote Writer",
        "company": "Acme Corp",
        "snapshot": "Write stuff remotely.",
        "posted_date": "2026-07-06",
        "url": "https://ats.example.com/apply/99",
    }
]


async def test_rrr_fixture_does_not_trigger_llm_extractor():
    """RRR HTML parses with the static parser; the LLM seam must NOT be called."""
    store = _mem_store()
    store.add_board("https://example.com/jobs")
    extractor_called: list[str] = []

    async def fake_nav(url):
        return _FAKE_HTML

    async def fake_extract(text):
        extractor_called.append(text)
        return _LLM_POSTINGS

    plugin = JobSearchPlugin(
        navigate_fn=fake_nav,
        store=store,
        extract_postings_fn=fake_extract,
    )
    result = await plugin._fetch_postings()
    assert not result.is_error
    assert extractor_called == [], "LLM extractor must not be called when static parser succeeds"


async def test_llm_extractor_called_when_static_parser_returns_zero():
    """Non-RRR page yields no static results → LLM fallback fires."""
    store = _mem_store()
    store.add_board("https://other-board.com/jobs")
    extractor_inputs: list[str] = []

    async def fake_extract(text):
        extractor_inputs.append(text)
        return _LLM_POSTINGS

    async def fake_nav(url):
        return _EMPTY_HTML

    plugin = JobSearchPlugin(
        navigate_fn=fake_nav, store=store, extract_postings_fn=fake_extract
    )
    result = await plugin._fetch_postings()
    assert not result.is_error
    assert len(extractor_inputs) == 1
    data = json.loads(result.content)
    assert data["fetched"] == 1
    assert data["saved"] == 1


async def test_llm_extracted_postings_upsert_with_url_dedup():
    """Two fetches of the same LLM-extracted posting → only one row in the store."""
    store = _mem_store()
    store.add_board("https://other-board.com/jobs")

    async def fake_extract(text):
        return _LLM_POSTINGS

    async def fake_nav(url):
        return _EMPTY_HTML

    plugin = JobSearchPlugin(
        navigate_fn=fake_nav, store=store, extract_postings_fn=fake_extract
    )
    await plugin._fetch_postings()
    await plugin._fetch_postings()
    assert store.count() == 1


async def test_unrecognised_board_reports_note_without_error():
    """Both parsers return zero → per-board note, no is_error, fetch continues."""
    store = _mem_store()
    store.add_board("https://unknown.com/jobs")

    async def fake_nav(url):
        return _EMPTY_HTML

    plugin = JobSearchPlugin(navigate_fn=fake_nav, store=store)  # no extractor
    result = await plugin._fetch_postings()
    assert not result.is_error
    data = json.loads(result.content)
    board = data["per_board"][0]
    assert board["fetched"] == 0
    assert "note" in board
    assert "unrecognised layout" in board["note"]


async def test_llm_input_truncated_to_cap():
    """Extractor receives at most _LLM_POSTINGS_INPUT_CAP chars."""
    store = _mem_store()
    store.add_board("https://huge-board.com/jobs")
    big_html = "x" * 100_000
    captured: list[str] = []

    async def fake_extract(text):
        captured.append(text)
        return []

    async def fake_nav(url):
        return big_html

    plugin = JobSearchPlugin(
        navigate_fn=fake_nav, store=store, extract_postings_fn=fake_extract
    )
    await plugin._fetch_postings()
    assert captured and len(captured[0]) <= JobSearchPlugin._LLM_POSTINGS_INPUT_CAP


async def test_llm_extractor_skips_entries_without_http_url():
    """The store.upsert guard filters out LLM entries missing valid URLs."""
    store = _mem_store()
    store.add_board("https://board.com/jobs")
    bad_postings = [
        {"title": "No URL job", "company": "X", "snapshot": "", "posted_date": "", "url": ""},
        {"title": "Relative URL", "company": "Y", "snapshot": "", "posted_date": "", "url": "/apply/1"},
    ]

    async def fake_extract(text):
        return bad_postings

    async def fake_nav(url):
        return _EMPTY_HTML

    plugin = JobSearchPlugin(
        navigate_fn=fake_nav, store=store, extract_postings_fn=fake_extract
    )
    result = await plugin._fetch_postings()
    assert not result.is_error
    assert store.count() == 0


# ── B1 #508 — detect_board_provider + provider seam ─────────────────────────


def test_detect_board_provider_greenhouse():
    assert detect_board_provider("https://boards.greenhouse.io/acme") == "greenhouse"
    assert detect_board_provider("https://job-boards.greenhouse.io/acme/jobs/123") == "greenhouse"


def test_detect_board_provider_lever():
    assert detect_board_provider("https://jobs.lever.co/acme") == "lever"
    assert detect_board_provider("https://jobs.lever.co/acme/123?ref=x") == "lever"


def test_detect_board_provider_scrape_fallback():
    assert detect_board_provider("https://ratracerebellion.com/job-postings") == "scrape"
    assert detect_board_provider("https://example.com/careers") == "scrape"
    assert detect_board_provider("not-a-url") == "scrape"


def test_detect_board_provider_greenhouse_subdomain_only():
    # Only the canonical Greenhouse board host — not arbitrary greenhouse.io subdomains.
    assert detect_board_provider("https://greenhouse.io") == "scrape"
    assert detect_board_provider("https://api.greenhouse.io/jobs") == "scrape"


def test_add_board_stores_detected_provider():
    s = _mem_store()
    s.add_board("https://boards.greenhouse.io/acme", "Acme")
    b = s.list_boards()[0]
    assert b["provider"] == "greenhouse"
    assert json.loads(b["config"]) == {"slug": "acme"}  # B2: slug extracted at add time


def test_add_board_scrape_provider_for_generic_url():
    s = _mem_store()
    s.add_board("https://ratracerebellion.com/job-postings")
    assert s.list_boards()[0]["provider"] == "scrape"


def test_migration_adds_columns_to_existing_schema():
    """Opening a store built without provider/config columns adds them; existing rows get defaults."""
    import sqlite3
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    # Simulate old schema (no provider, no config columns).
    con.execute("""
        CREATE TABLE job_boards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    con.execute("INSERT INTO job_boards (url, label, enabled, created_at) VALUES (?, '', 1, ?)",
                ("https://ratracerebellion.com/job-postings", "2026-01-01T00:00:00+00:00"))
    con.commit()

    # Now wire the store onto that existing connection so _init runs migrations.
    store = JobSearchStore.__new__(JobSearchStore)
    store._con = con
    store._init()

    boards = [dict(row) for row in con.execute("SELECT * FROM job_boards")]
    assert len(boards) == 1
    assert boards[0]["provider"] == "scrape"
    assert boards[0]["config"] == "{}"


async def test_scrape_board_dispatches_as_before():
    """A board with provider='scrape' fetches via navigate + static parser, same as pre-B1."""
    store = _mem_store()
    store.add_board("https://example.com/jobs", "Example")
    navigated: list[str] = []

    async def fake_nav(url):
        navigated.append(url)
        return _FAKE_HTML

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    assert not result.is_error
    assert navigated == ["https://example.com/jobs"]
    data = json.loads(result.content)
    assert data["fetched"] > 0


async def test_unknown_provider_falls_back_to_scrape_and_logs(caplog):
    """A board with an unregistered provider falls back to scrape and logs a warning."""
    import logging
    store = _mem_store()
    store.add_board("https://example.com/jobs")
    # Manually set the provider to something B1 doesn't handle yet.
    store._con.execute("UPDATE job_boards SET provider = 'future_provider' WHERE url = ?",
                       ("https://example.com/jobs",))
    store._con.commit()

    async def fake_nav(url):
        return _FAKE_HTML

    plugin = _make_plugin(store, fake_nav)
    with caplog.at_level(logging.WARNING, logger="plugins.job_search"):
        result = await plugin._fetch_postings()
    assert not result.is_error
    data = json.loads(result.content)
    assert data["fetched"] > 0
    assert any("unknown board provider" in r.message for r in caplog.records)


def test_board_providers_registry_has_scrape():
    assert "scrape" in BOARD_PROVIDERS
    import asyncio
    assert asyncio.iscoroutinefunction(BOARD_PROVIDERS["scrape"])


# ── B2 #509 — slug extraction ────────────────────────────────────────────────

def test_extract_slug_greenhouse():
    assert _extract_board_slug("https://boards.greenhouse.io/acme", "greenhouse") == "acme"
    assert _extract_board_slug("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse") == "acme"


def test_extract_slug_lever():
    assert _extract_board_slug("https://jobs.lever.co/acme", "lever") == "acme"
    assert _extract_board_slug("https://jobs.lever.co/acme/123?ref=x", "lever") == "acme"


def test_extract_slug_non_api_provider_returns_empty():
    assert _extract_board_slug("https://ratracerebellion.com/job-postings", "scrape") == ""


def test_add_board_stores_slug_in_config_greenhouse():
    s = _mem_store()
    s.add_board("https://boards.greenhouse.io/widgetco")
    b = s.list_boards()[0]
    assert json.loads(b["config"]) == {"slug": "widgetco"}


def test_add_board_stores_slug_in_config_lever():
    s = _mem_store()
    s.add_board("https://jobs.lever.co/widgetco")
    b = s.list_boards()[0]
    assert json.loads(b["config"]) == {"slug": "widgetco"}


def test_add_board_no_slug_for_scrape():
    s = _mem_store()
    s.add_board("https://ratracerebellion.com/job-postings")
    b = s.list_boards()[0]
    assert b["config"] == "{}"


# ── B2 #509 — Greenhouse provider ────────────────────────────────────────────

_GH_FIXTURE = json.dumps({
    "company_name": "Acme Inc",
    "jobs": [
        {
            "title": "Remote Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            "location": {"name": "Remote"},
            "content": "<p>Build cool stuff.</p>",
        },
        {
            "title": "Remote Designer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
            "location": {"name": "Remote"},
            "content": "<p>Design cool stuff.</p>",
        },
    ],
})


async def test_greenhouse_provider_maps_postings():
    store = _mem_store()
    store.add_board("https://boards.greenhouse.io/acme", "Acme")
    fetched_urls: list[str] = []

    async def fake_api(url):
        fetched_urls.append(url)
        return _GH_FIXTURE

    set_board_api_fetch_fn(fake_api)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._fetch_postings()
        assert not result.is_error
        data = json.loads(result.content)
        assert data["fetched"] == 2
        assert data["saved"] == 2
        postings = store.list_postings()
        assert len(postings) == 2
        titles = {p["title"] for p in postings}
        assert "Remote Engineer" in titles
        assert postings[0]["company"] == "Acme Inc"
        assert postings[0]["url"].startswith("https://boards.greenhouse.io/acme/jobs/")
        assert "boards-api.greenhouse.io" in fetched_urls[0]
    finally:
        set_board_api_fetch_fn(None)


async def test_greenhouse_dedup_on_refetch():
    store = _mem_store()
    store.add_board("https://boards.greenhouse.io/acme")

    async def fake_api(url):
        return _GH_FIXTURE

    set_board_api_fetch_fn(fake_api)
    try:
        plugin = JobSearchPlugin(store=store)
        await plugin._fetch_postings()
        await plugin._fetch_postings()
        assert store.count() == 2  # same 2 jobs, not 4
    finally:
        set_board_api_fetch_fn(None)


async def test_greenhouse_cap_at_50():
    store = _mem_store()
    store.add_board("https://boards.greenhouse.io/bigco")
    big = json.dumps({"jobs": [
        {"title": f"Job {i}", "absolute_url": f"https://boards.greenhouse.io/bigco/jobs/{i}",
         "content": ""} for i in range(80)
    ]})

    async def fake_api(url):
        return big

    set_board_api_fetch_fn(fake_api)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._fetch_postings()
        data = json.loads(result.content)
        assert data["fetched"] == 50
    finally:
        set_board_api_fetch_fn(None)


# ── B2 #509 — Lever provider ─────────────────────────────────────────────────

_LEVER_FIXTURE = json.dumps([
    {
        "text": "Senior QA Engineer",
        "hostedUrl": "https://jobs.lever.co/widgetco/abc123",
        "categories": {"location": "Remote"},
        "descriptionPlain": "Test all the things.",
    },
    {
        "text": "Product Manager",
        "hostedUrl": "https://jobs.lever.co/widgetco/def456",
        "categories": {"location": "Remote"},
        "descriptionPlain": "Own the roadmap.",
    },
])


async def test_lever_provider_maps_postings():
    store = _mem_store()
    store.add_board("https://jobs.lever.co/widgetco", "WidgetCo")
    fetched_urls: list[str] = []

    async def fake_api(url):
        fetched_urls.append(url)
        return _LEVER_FIXTURE

    set_board_api_fetch_fn(fake_api)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._fetch_postings()
        assert not result.is_error
        data = json.loads(result.content)
        assert data["fetched"] == 2
        assert data["saved"] == 2
        postings = store.list_postings()
        titles = {p["title"] for p in postings}
        assert "Senior QA Engineer" in titles
        assert postings[0]["url"].startswith("https://jobs.lever.co/widgetco/")
        assert "api.lever.co" in fetched_urls[0]
    finally:
        set_board_api_fetch_fn(None)


async def test_lever_cap_at_50():
    store = _mem_store()
    store.add_board("https://jobs.lever.co/bigco")
    big = json.dumps([
        {"text": f"Job {i}", "hostedUrl": f"https://jobs.lever.co/bigco/{i}",
         "descriptionPlain": ""} for i in range(75)
    ])

    async def fake_api(url):
        return big

    set_board_api_fetch_fn(fake_api)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._fetch_postings()
        data = json.loads(result.content)
        assert data["fetched"] == 50
    finally:
        set_board_api_fetch_fn(None)


# ── B2 #509 — API error continues other boards ───────────────────────────────

async def test_api_error_continues_other_boards():
    store = _mem_store()
    store.add_board("https://boards.greenhouse.io/badco")
    store.add_board("https://example.com/jobs")  # scrape board as the good board

    async def fake_api(url):
        raise RuntimeError("HTTP 500")

    async def fake_nav(url):
        return _FAKE_HTML

    set_board_api_fetch_fn(fake_api)
    try:
        plugin = JobSearchPlugin(navigate_fn=fake_nav, store=store)
        result = await plugin._fetch_postings()
        assert not result.is_error
        data = json.loads(result.content)
        bad = next(b for b in data["per_board"] if "badco" in b["url"])
        good = next(b for b in data["per_board"] if "example" in b["url"])
        assert "error" in bad
        assert good["fetched"] > 0
    finally:
        set_board_api_fetch_fn(None)


def test_board_providers_registry_has_greenhouse_and_lever():
    assert "greenhouse" in BOARD_PROVIDERS
    assert "lever" in BOARD_PROVIDERS
    assert asyncio.iscoroutinefunction(BOARD_PROVIDERS["greenhouse"])
    assert asyncio.iscoroutinefunction(BOARD_PROVIDERS["lever"])


# ── B4 #511 — JobSpy big-board provider ──────────────────────────────────────

# Fake jobspy rows that look like a real DataFrame row turned into a dict.
_FAKE_JOBSPY_ROWS = [
    {
        "title": "Remote Software Engineer",
        "company": "Acme",
        "job_url": "https://www.linkedin.com/jobs/view/123",
        "job_url_direct": "https://boards.greenhouse.io/acme/jobs/123",
        "description": "Build cool stuff.",
    },
    {
        "title": "Backend Developer",
        "company": "Beta Corp",
        "job_url": "https://www.linkedin.com/jobs/view/456",
        "job_url_direct": "https://lever.co/betacorp/job/456",
        "description": "Python microservices.",
    },
    # Easy Apply-only — no job_url_direct; should be skipped
    {
        "title": "Product Manager",
        "company": "Gamma",
        "job_url": "https://www.linkedin.com/jobs/view/789",
        "job_url_direct": "",
        "description": "Lead products.",
    },
]


def test_detect_board_provider_jobspy_domains():
    assert detect_board_provider("https://www.linkedin.com/jobs") == "jobspy"
    assert detect_board_provider("https://linkedin.com") == "jobspy"
    assert detect_board_provider("https://www.indeed.com/jobs?q=engineer") == "jobspy"
    assert detect_board_provider("https://www.glassdoor.com/Job/jobs.htm") == "jobspy"


def test_add_board_jobspy_stores_site_config():
    s = _mem_store()
    s.add_board("https://www.linkedin.com/jobs", "LinkedIn")
    b = s.list_boards()[0]
    assert b["provider"] == "jobspy"
    cfg = json.loads(b["config"])
    assert cfg["site"] == "linkedin"

    s2 = _mem_store()
    s2.add_board("https://www.indeed.com")
    assert json.loads(s2.list_boards()[0]["config"])["site"] == "indeed"

    s3 = _mem_store()
    s3.add_board("https://www.glassdoor.com/jobs")
    assert json.loads(s3.list_boards()[0]["config"])["site"] == "glassdoor"


def test_board_providers_registry_has_jobspy():
    assert "jobspy" in BOARD_PROVIDERS
    assert asyncio.iscoroutinefunction(BOARD_PROVIDERS["jobspy"])


async def test_jobspy_fetch_maps_rows_and_skips_easy_apply():
    """url_direct preferred; Easy Apply-only rows (no direct URL) skipped; cap enforced."""
    store = _mem_store()
    store.add_board("https://www.linkedin.com/jobs", "LinkedIn")
    store.upsert_dossier(1, {"name": "Jane", "target_titles": ["Software Engineer"]})

    calls: list[tuple] = []

    async def fake_jobspy(site, term, cap):
        calls.append((site, term, cap))
        return _FAKE_JOBSPY_ROWS

    set_jobspy_fetch_fn(fake_jobspy)
    old_pid = _jmod._active_profile_id
    old_store = _jmod._store
    set_active_profile_id(1)
    set_store(store)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._fetch_postings()
        assert not result.is_error, result.content
        data = json.loads(result.content)
        # 3 rows in fixture; 1 Easy Apply-only skipped -> 2 postings saved
        assert data["saved"] == 2
        assert data["fetched"] == 2
        # jobspy was called with linkedin, our title, and a cap
        assert calls[0][0] == "linkedin"
        assert calls[0][1] == "Software Engineer"
        # url_direct wins: first posting should have greenhouse URL as stored url
        posted_urls = [p["url"] for p in store.list_postings()]
        assert any("greenhouse.io" in u for u in posted_urls)
    finally:
        set_jobspy_fetch_fn(None)
        set_active_profile_id(old_pid)
        set_store(old_store)


async def test_jobspy_missing_target_titles_returns_per_board_hint():
    """No target_titles in dossier -> per_board error entry with hint, no crash."""
    store = _mem_store()
    store.add_board("https://www.linkedin.com/jobs", "LinkedIn")
    # Dossier exists but no target_titles
    store.upsert_dossier(1, {"name": "Jane", "target_titles": []})

    set_jobspy_fetch_fn(None)  # ensure no leak from other tests
    old_pid = _jmod._active_profile_id
    old_store = _jmod._store
    set_active_profile_id(1)
    set_store(store)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._fetch_postings()
        assert not result.is_error  # overall result is not an error
        data = json.loads(result.content)
        board_entry = data["per_board"][0]
        assert "error" in board_entry
        assert "target titles" in board_entry["error"]
    finally:
        set_active_profile_id(old_pid)
        set_store(old_store)


async def test_jobspy_cap_enforced():
    """At most 50 postings per board per fetch total (across all titles)."""
    store = _mem_store()
    store.add_board("https://www.linkedin.com/jobs", "LinkedIn")
    store.upsert_dossier(1, {"name": "Jane", "target_titles": ["Engineer", "Developer"]})

    # Each call returns 40 rows -> combined 80, but cap is 50
    big_rows = [
        {"title": f"Job {i}", "company": "Co",
         "job_url": f"https://linkedin.com/jobs/{i}",
         "job_url_direct": f"https://ats.example.com/job/{i}",
         "description": ""}
        for i in range(40)
    ]

    async def fake_jobspy(site, term, cap):
        return big_rows[:cap]

    set_jobspy_fetch_fn(fake_jobspy)
    old_pid = _jmod._active_profile_id
    old_store = _jmod._store
    set_active_profile_id(1)
    set_store(store)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._fetch_postings()
        data = json.loads(result.content)
        assert data["saved"] <= 50
    finally:
        set_jobspy_fetch_fn(None)
        set_active_profile_id(old_pid)
        set_store(old_store)


async def test_jobspy_error_continues_other_boards():
    """A failing jobspy board never breaks other boards' fetch."""
    store = _mem_store()
    store.add_board("https://www.linkedin.com/jobs", "LinkedIn")
    store.add_board("https://example.com/jobs")  # scrape board (good)
    store.upsert_dossier(1, {"name": "Jane", "target_titles": ["Engineer"]})

    async def bad_jobspy(site, term, cap):
        raise RuntimeError("rate limited")

    async def fake_nav(url):
        return _FAKE_HTML

    set_jobspy_fetch_fn(bad_jobspy)
    old_pid = _jmod._active_profile_id
    old_store = _jmod._store
    set_active_profile_id(1)
    set_store(store)
    try:
        plugin = JobSearchPlugin(navigate_fn=fake_nav, store=store)
        result = await plugin._fetch_postings()
        assert not result.is_error
        data = json.loads(result.content)
        linkedin_entry = next(b for b in data["per_board"] if "linkedin" in b["url"])
        scrape_entry = next(b for b in data["per_board"] if "example" in b["url"])
        assert "error" in linkedin_entry
        assert scrape_entry["fetched"] > 0
    finally:
        set_jobspy_fetch_fn(None)
        set_active_profile_id(old_pid)
        set_store(old_store)


# ── B4 #511 — target_titles dossier column ───────────────────────────────────

def test_dossier_stores_and_retrieves_target_titles():
    store = _mem_store()
    store.upsert_dossier(1, {"name": "Jane", "target_titles": ["Software Engineer", "Backend Dev"]})
    d = store.get_dossier(1)
    assert d["target_titles"] == ["Software Engineer", "Backend Dev"]


def test_dossier_target_titles_defaults_to_empty():
    store = _mem_store()
    store.upsert_dossier(1, {"name": "Jane"})
    d = store.get_dossier(1)
    assert d["target_titles"] == []


def test_patch_dossier_target_titles_json_array():
    store = _mem_store()
    store.upsert_dossier(1, {"name": "Jane"})
    store.patch_dossier(1, "target_titles", '["Software Engineer", "ML Engineer"]')
    d = store.get_dossier(1)
    assert d["target_titles"] == ["Software Engineer", "ML Engineer"]


def test_patch_dossier_target_titles_comma_separated():
    store = _mem_store()
    store.upsert_dossier(1, {"name": "Jane"})
    store.patch_dossier(1, "target_titles", "Software Engineer, Backend Developer")
    d = store.get_dossier(1)
    assert d["target_titles"] == ["Software Engineer", "Backend Developer"]


def test_patch_dossier_rejects_unknown_field():
    store = _mem_store()
    store.upsert_dossier(1, {"name": "Jane"})
    with pytest.raises(ValueError, match="not user-editable"):
        store.patch_dossier(1, "raw_text", "injected")


async def test_update_dossier_field_tool_target_titles():
    """jobs_update_dossier_field tool accepts target_titles as JSON array."""
    store = _mem_store()
    store.upsert_dossier(1, {"name": "Jane"})

    old_pid = _jmod._active_profile_id
    old_store = _jmod._store
    set_active_profile_id(1)
    set_store(store)
    try:
        plugin = JobSearchPlugin(store=store)
        result = await plugin._update_dossier_field(
            "target_titles", '["Data Engineer", "Analytics Engineer"]'
        )
        assert not result.is_error
        d = store.get_dossier(1)
        assert d["target_titles"] == ["Data Engineer", "Analytics Engineer"]
    finally:
        set_active_profile_id(old_pid)
        set_store(old_store)


# ── #517 — clear_postings ─────────────────────────────────────────────────────

def test_clear_postings_deletes_unapplied_keeps_applied():
    s = _mem_store()
    applied = "https://boards.greenhouse.io/acme/jobs/1"
    s.upsert({"title": "Eng", "company": "Acme", "pay": "", "snapshot": "",
              "posted_date": "", "url": applied})
    s.upsert({"title": "Dev", "company": "Bar", "pay": "", "snapshot": "",
              "posted_date": "", "url": "https://jobs.lever.co/bar/2"})
    s.upsert_application(url=applied, posting_url=applied, ats_type="greenhouse",
                         status="submitted", fields=[])
    deleted = s.clear_postings()
    assert deleted == 1
    remaining = s.list_postings()
    assert [p["url"] for p in remaining] == [applied]


def test_clear_postings_empty_store_is_noop():
    s = _mem_store()
    assert s.clear_postings() == 0


# ── S3 #404 — RRR self-link resolution ───────────────────────────────────────

_RRR_ARTICLE_URL = "https://ratracerebellion.com/job/acme-writer"

# Live 2026 labeled-listing format (#380): the only link is the RRR article —
# the employer/ATS link lives one hop deeper, on that article page.
_RRR_LISTING_HTML = (
    '<p><strong>Company</strong>: Acme'
    '<!-- rrr-job-id: RRR-20260810-001 --><br>'
    '<strong>Job/Gig</strong>: <a href="' + _RRR_ARTICLE_URL + '">Writer</a>'
    '<br><strong>Pay</strong>: $20/hr</p>'
    '<p><strong>Snapshot</strong>: Remote writing gig.</p>'
)


async def test_rrr_posting_resolves_one_hop_to_real_ats_url():
    store = _mem_store()
    store.add_board("https://example.com/jobs")

    async def fake_nav(url):
        if url == "https://example.com/jobs":
            return _RRR_LISTING_HTML
        if url == _RRR_ARTICLE_URL:
            return (
                '<a href="https://ratracerebellion.com/other">Home</a>'
                '<a href="https://boards.greenhouse.io/acme/jobs/1">Apply Now</a>'
            )
        return ""

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    assert not result.is_error
    postings = store.list_postings()
    assert len(postings) == 1
    assert postings[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert postings[0]["ats_note"] == ""
    assert store.get_posting_by_url(_RRR_ARTICLE_URL) is None  # no leftover RRR row


async def test_rrr_no_external_link_keeps_rrr_url_with_marker_and_gate_reflects_it():
    store = _mem_store()
    store.add_board("https://example.com/jobs")

    async def fake_nav(url):
        if url == "https://example.com/jobs":
            return _RRR_LISTING_HTML
        if url == _RRR_ARTICLE_URL:
            return '<a href="https://ratracerebellion.com/other">Only internal link</a>'
        return ""

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    assert not result.is_error
    postings = store.list_postings()
    assert len(postings) == 1
    p = postings[0]
    assert p["url"] == _RRR_ARTICLE_URL
    assert p["ats_note"] == "no ATS link found on board"

    # The apply gate must surface the marker, not "Unsupported ATS 'unknown'".
    store.set_score(p["url"], 7.0)
    store.set_status(p["url"], "shortlisted")
    store.upsert_dossier(1, {"name": "Jane"})
    old_pid = _jmod._active_profile_id
    old_store = _jmod._store
    set_active_profile_id(1)
    set_store(store)
    try:
        gate_result = await plugin.call_tool("jobs_apply_start", {"url": p["url"]})
        assert gate_result.is_error
        data = json.loads(gate_result.content)
        assert data["reason"] == "no ATS link found on board"
        assert "Unsupported ATS" not in data["reason"]
    finally:
        set_active_profile_id(old_pid)
        set_store(old_store)


async def test_rrr_resolution_preserves_status_and_score_no_duplicate():
    """Repairing an existing stuck RRR-url row updates it in place: no duplicate
    row, and status/fit_score (the user-decision columns) survive the url move.
    """
    store = _mem_store()
    store.upsert({"title": "Writer", "company": "Acme", "pay": "", "snapshot": "",
                  "posted_date": "", "url": _RRR_ARTICLE_URL})
    store.set_score(_RRR_ARTICLE_URL, 8.5)
    store.set_status(_RRR_ARTICLE_URL, "shortlisted")
    store.add_board("https://example.com/jobs")

    async def fake_nav(url):
        if url == "https://example.com/jobs":
            return _RRR_LISTING_HTML  # same job, same RRR article url as before
        if url == _RRR_ARTICLE_URL:
            return '<a href="https://boards.greenhouse.io/acme/jobs/1">Apply</a>'
        return ""

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    assert not result.is_error
    postings = store.list_postings()
    assert len(postings) == 1  # no duplicate row
    p = postings[0]
    assert p["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert p["status"] == "shortlisted"
    assert p["fit_score"] == 8.5
    assert store.get_posting_by_url(_RRR_ARTICLE_URL) is None  # stale row gone


async def test_rrr_resolve_navigate_cap_one_per_posting():
    """At most one extra navigate call per RRR-linked posting per fetch."""
    store = _mem_store()
    store.add_board("https://example.com/jobs")
    calls: list[str] = []

    async def fake_nav(url):
        calls.append(url)
        if url == "https://example.com/jobs":
            return _RRR_LISTING_HTML
        return '<a href="https://boards.greenhouse.io/acme/jobs/1">Apply</a>'

    plugin = _make_plugin(store, fake_nav)
    await plugin._fetch_postings()
    assert calls.count(_RRR_ARTICLE_URL) == 1
    assert calls.count("https://example.com/jobs") == 1


async def test_non_rrr_posting_untouched_no_extra_navigate():
    """A posting already on a real ATS host is untouched — no resolve hop at all."""
    store = _mem_store()
    store.add_board("https://example.com/jobs")
    calls: list[str] = []

    async def fake_nav(url):
        calls.append(url)
        return _FAKE_HTML  # already carries a direct greenhouse.io link

    plugin = _make_plugin(store, fake_nav)
    result = await plugin._fetch_postings()
    assert not result.is_error
    assert calls == ["https://example.com/jobs"]  # board fetch only, no resolve hop
    assert store.list_postings()[0]["url"] == "https://greenhouse.io/apply/123"


# ── S4 #405 — ats_type / appliable derived on every list_postings() row ───────

def test_list_postings_derives_ats_type_and_appliable():
    s = _mem_store()
    s.upsert({"url": "https://boards.greenhouse.io/acme/jobs/1", "title": "A"})
    s.upsert({"url": "https://jobs.lever.co/acme/2", "title": "B"})
    s.upsert({"url": "https://example.com/careers/3", "title": "C"})
    by_url = {p["url"]: p for p in s.list_postings()}
    gh = by_url["https://boards.greenhouse.io/acme/jobs/1"]
    assert gh["ats_type"] == "greenhouse"
    assert gh["appliable"] is True
    lever = by_url["https://jobs.lever.co/acme/2"]
    assert lever["ats_type"] == "lever"
    assert lever["appliable"] is True
    other = by_url["https://example.com/careers/3"]
    assert other["ats_type"] == "unknown"
    assert other["appliable"] is False


def test_list_postings_ats_note_case_not_appliable():
    """S3 #404 no-ATS-link posting: url stays on the RRR host, so detect_ats_type
    returns 'unknown' and appliable is False -- no special-casing needed here,
    the UI layer reads ats_note to render the explanation instead of a host.
    """
    s = _mem_store()
    s.upsert({
        "url": "https://ratracerebellion.com/job-postings/some-article",
        "title": "D",
        "ats_note": "no ATS link found on board",
    })
    row = s.list_postings()[0]
    assert row["ats_type"] == "unknown"
    assert row["appliable"] is False
    assert row["ats_note"] == "no ATS link found on board"


# ── S6 #406 — Ashby ATS detection ──────────────────────────────────────────────

def test_detect_ats_type_ashby():
    assert _jmod.detect_ats_type("https://jobs.ashbyhq.com/acme/jobs/123") == "ashby"
    assert _jmod.detect_ats_type("https://ashbyhq.com/apply/456") == "ashby"


def test_list_postings_ashby_is_appliable():
    s = _mem_store()
    s.upsert({"url": "https://jobs.ashbyhq.com/acme/jobs/1", "title": "Ashby Job"})
    postings = s.list_postings()
    ashby = postings[0]
    assert ashby["ats_type"] == "ashby"
    assert ashby["appliable"] is True


def test_list_postings_unknown_stays_unknown():
    s = _mem_store()
    s.upsert({"url": "https://custom-ats.example.com/jobs/1", "title": "Custom Job"})
    postings = s.list_postings()
    custom = postings[0]
    assert custom["ats_type"] == "unknown"
    assert custom["appliable"] is False
