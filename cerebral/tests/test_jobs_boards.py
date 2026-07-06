"""S1 #396 / S2 #397 — job_boards table CRUD and _fetch_postings loop.

All tests use in-memory SQLite and stubbed navigate/extract; no live fetches.
"""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from plugins.job_search import JobSearchStore, JobSearchPlugin


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
