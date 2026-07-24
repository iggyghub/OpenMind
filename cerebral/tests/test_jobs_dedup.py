"""B3 #510 — duplicate-application guard + URL canonicalization.

All tests use in-memory SQLite and stubbed fns; no live network.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from plugins.job_search import (
    JobSearchPlugin, JobSearchStore,
    canonicalize_posting_url, check_duplicate_application,
    check_auto_submit_gate,
)


class _MemStore(JobSearchStore):
    def __init__(self):
        self._con = sqlite3.connect(":memory:", check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init()


def _mem_store() -> JobSearchStore:
    return _MemStore()


# ── canonicalize_posting_url ─────────────────────────────────────────────────


def test_canon_strips_utm_params():
    url = "https://boards.greenhouse.io/acme/jobs/1?utm_source=linkedin&utm_medium=job"
    assert "utm_source" not in canonicalize_posting_url(url)
    assert "utm_medium" not in canonicalize_posting_url(url)


def test_canon_strips_gh_src():
    url = "https://boards.greenhouse.io/acme/jobs/1?gh_src=abc123"
    assert "gh_src" not in canonicalize_posting_url(url)


def test_canon_strips_source_ref():
    url = "https://jobs.lever.co/acme/123?source=indeed&ref=homepage"
    out = canonicalize_posting_url(url)
    assert "source" not in out
    assert "ref" not in out


def test_canon_strips_lever_origin_and_lever_source():
    url = "https://jobs.lever.co/acme/123?lever-origin=applied&lever-source%5B%5D=x"
    out = canonicalize_posting_url(url)
    assert "lever-origin" not in out
    assert "lever-source" not in out


def test_canon_strips_fragment():
    url = "https://example.com/jobs/1#apply"
    assert "#" not in canonicalize_posting_url(url)


def test_canon_strips_trailing_slash():
    assert canonicalize_posting_url("https://example.com/jobs/1/") == "https://example.com/jobs/1"


def test_canon_lowercases_scheme_and_host():
    url = "HTTPS://Boards.Greenhouse.IO/acme/jobs/1"
    out = canonicalize_posting_url(url)
    assert out.startswith("https://boards.greenhouse.io/")


def test_canon_preserves_non_tracking_params():
    url = "https://example.com/jobs?content=true&page=2"
    out = canonicalize_posting_url(url)
    assert "content=true" in out
    assert "page=2" in out


def test_canon_idempotent():
    url = "https://boards.greenhouse.io/acme/jobs/42"
    assert canonicalize_posting_url(url) == canonicalize_posting_url(canonicalize_posting_url(url))


# ── same job from two sources with different tracking params → one row ────────


async def test_two_sources_same_tracking_dedup():
    store = _mem_store()
    store.add_board("https://example.com/jobs")

    urls_fetched: list[str] = []

    async def fake_nav(url):
        urls_fetched.append(url)
        return ""

    # Simulate two postings that differ only by tracking param
    postings_seq = [
        [{"title": "SRE", "company": "Acme", "pay": "", "snapshot": "", "posted_date": "",
          "url": "https://boards.greenhouse.io/acme/jobs/1?gh_src=linkedin"}],
        [{"title": "SRE", "company": "Acme", "pay": "", "snapshot": "", "posted_date": "",
          "url": "https://boards.greenhouse.io/acme/jobs/1?utm_source=indeed"}],
    ]
    call_idx = 0

    from plugins.job_search import BOARD_PROVIDERS
    original = BOARD_PROVIDERS.get("scrape")

    async def fake_scrape(board, nav, ext, cap):
        nonlocal call_idx
        result = postings_seq[min(call_idx, len(postings_seq) - 1)]
        call_idx += 1
        return result

    BOARD_PROVIDERS["scrape"] = fake_scrape
    try:
        plugin = JobSearchPlugin(navigate_fn=fake_nav, store=store)
        await plugin._fetch_postings()
        await plugin._fetch_postings()
        assert store.count() == 1
    finally:
        BOARD_PROVIDERS["scrape"] = original


# ── url_direct preference ─────────────────────────────────────────────────────


async def test_url_direct_wins_over_url():
    store = _mem_store()
    store.add_board("https://example.com/jobs")

    from plugins.job_search import BOARD_PROVIDERS
    original = BOARD_PROVIDERS.get("scrape")

    async def fake_scrape(board, nav, ext, cap):
        return [{"title": "PM", "company": "Acme", "pay": "", "snapshot": "", "posted_date": "",
                 "url": "https://example.com/board/job/99",
                 "url_direct": "https://boards.greenhouse.io/acme/jobs/99"}]

    BOARD_PROVIDERS["scrape"] = fake_scrape
    try:
        plugin = JobSearchPlugin(store=store)
        await plugin._fetch_postings()
        postings = store.list_postings()
        assert len(postings) == 1
        assert postings[0]["url"] == "https://boards.greenhouse.io/acme/jobs/99"
    finally:
        BOARD_PROVIDERS["scrape"] = original


# ── check_duplicate_application ───────────────────────────────────────────────


def _seed_application(store: JobSearchStore, company: str, title: str, status: str = "submitted") -> None:
    now = datetime.now(timezone.utc).isoformat()
    url = f"https://ats.example.com/apply/{hash(company + title) & 0xFFFF}"
    store.upsert({"url": url, "title": title, "company": company, "pay": "",
                  "snapshot": "", "posted_date": "", "fetched_at": now})
    store.upsert_application(url=url, posting_url=url, ats_type="greenhouse",
                             status=status, fields=[])
    if status == "submitted":
        store.set_application_status(url, "submitted", submitted_at=now)


def test_dup_same_company_identical_title():
    store = _mem_store()
    _seed_application(store, "Acme Inc", "Senior Software Engineer")
    dup = check_duplicate_application(store, "Acme Inc", "Senior Software Engineer")
    assert dup is not None
    assert "Senior Software Engineer" in dup["title"]


def test_dup_same_company_near_identical_title():
    store = _mem_store()
    _seed_application(store, "Acme Inc", "Senior Software Engineer")
    dup = check_duplicate_application(store, "Acme Inc", "Senior Software Engineer II")
    assert dup is not None


def test_no_dup_different_company():
    store = _mem_store()
    _seed_application(store, "Acme Inc", "Senior Software Engineer")
    dup = check_duplicate_application(store, "Other Corp", "Senior Software Engineer")
    assert dup is None


def test_no_dup_dissimilar_title():
    store = _mem_store()
    _seed_application(store, "Acme Inc", "Senior Software Engineer")
    dup = check_duplicate_application(store, "Acme Inc", "Product Manager")
    assert dup is None


def test_no_dup_empty_store():
    store = _mem_store()
    assert check_duplicate_application(store, "Acme", "Engineer") is None


def test_dup_company_case_insensitive():
    store = _mem_store()
    _seed_application(store, "Acme Inc", "Engineer")
    dup = check_duplicate_application(store, "ACME INC", "Engineer")
    assert dup is not None


# ── auto-submit gate respects duplicate_warning ───────────────────────────────


def test_gate_fails_on_duplicate_warning():
    store = _mem_store()
    store.set_auto_submit(1, True)
    # Manually set reviewed_count above ramp
    store._con.execute(
        "UPDATE job_search_settings SET reviewed_count = 10 WHERE profile_id = ?", (1,)
    )
    store._con.commit()
    pending = {"fields": [], "duplicate_warning": "possible duplicate of 'X' applied 2026-07-01"}
    ok, reason = check_auto_submit_gate(1, store, pending)
    assert not ok
    assert "duplicate" in reason


def test_gate_passes_without_duplicate_warning():
    store = _mem_store()
    store.set_auto_submit(1, True)
    store._con.execute(
        "UPDATE job_search_settings SET reviewed_count = 10 WHERE profile_id = ?", (1,)
    )
    store._con.commit()
    pending = {"fields": [], "duplicate_warning": None}
    ok, reason = check_auto_submit_gate(1, store, pending)
    assert ok


# ── _apply_start: duplicate_warning surfaced in payload ──────────────────────


async def test_apply_start_surfaces_duplicate_warning():
    store = _mem_store()
    now = datetime.now(timezone.utc).isoformat()

    # Seed the posting to apply to (status=shortlisted)
    url = "https://boards.greenhouse.io/acme/jobs/99"
    store.upsert({"url": url, "title": "Senior Engineer", "company": "Acme",
                  "pay": "", "snapshot": "", "posted_date": "", "fetched_at": now})
    store.set_status(url, "shortlisted")
    store.upsert_dossier(1, {"name": "Jane", "email": "jane@example.com", "phone": "",
                             "location": "", "linkedin": "", "github": "", "website": "",
                             "work_history": [], "education": [], "skills": [],
                             "raw_text": "engineer"})
    store.upsert_resume_artifact(1, "/tmp/resume.pdf")

    # Seed a prior application with same company + similar title
    _seed_application(store, "Acme", "Senior Engineer")

    async def fake_driver(url, dossier, resume_path):
        return {"fields": [{"selector": "#name", "label": "Name", "value": "Jane",
                            "required": True, "is_known": True}],
                "submit_selector": "#submit"}

    from plugins import job_search as js
    orig_profile = js._active_profile_id
    js._active_profile_id = 1
    try:
        plugin = JobSearchPlugin(store=store, apply_driver_fn=fake_driver)
        result = await plugin._apply_start(url)
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "ready_to_submit"
        assert "duplicate_warning" in data
        assert "Acme" in data["duplicate_warning"] or "Senior Engineer" in data["duplicate_warning"]
    finally:
        js._active_profile_id = orig_profile


async def test_apply_start_no_warning_without_prior_application():
    store = _mem_store()
    now = datetime.now(timezone.utc).isoformat()
    url = "https://boards.greenhouse.io/acme/jobs/55"
    store.upsert({"url": url, "title": "Data Analyst", "company": "FreshCo",
                  "pay": "", "snapshot": "", "posted_date": "", "fetched_at": now})
    store.set_status(url, "shortlisted")
    store.upsert_dossier(1, {"name": "Jane", "email": "jane@example.com", "phone": "",
                             "location": "", "linkedin": "", "github": "", "website": "",
                             "work_history": [], "education": [], "skills": [],
                             "raw_text": "analyst"})
    store.upsert_resume_artifact(1, "/tmp/resume.pdf")

    async def fake_driver(url, dossier, resume_path):
        return {"fields": [{"selector": "#name", "label": "Name", "value": "Jane",
                            "required": True, "is_known": True}],
                "submit_selector": "#submit"}

    from plugins import job_search as js
    orig_profile = js._active_profile_id
    js._active_profile_id = 1
    try:
        plugin = JobSearchPlugin(store=store, apply_driver_fn=fake_driver)
        result = await plugin._apply_start(url)
        assert not result.is_error
        data = json.loads(result.content)
        assert "duplicate_warning" not in data
    finally:
        js._active_profile_id = orig_profile
