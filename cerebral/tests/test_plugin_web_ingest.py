"""web_ingest (Library "Webpage" tab): stubbed fetch + extract, no network.
A page becomes a source_type='webpage' row clustered into the shared spine,
and its chrome (nav/script/footer) is stripped before extraction."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import plugins.video as video_mod
from cerebral.video import web_source as ws
from cerebral.video.store import VideoStore
from plugins.web_ingest import REQUIRED_CAPABILITIES, WebIngestPlugin

_seen: dict = {}

PAGE = (
    "<html><head><title>Momentum</title><script>evil()</script></head><body>"
    "<nav>Home | About</nav><article><h1>Momentum</h1>"
    + "".join(f"<p>Para {i} about <a href='#'>trend</a> following works.</p>" for i in range(20))
    + "</article><footer>(c) site</footer></body></html>"
)


async def _fake_extract(transcript, ocr, vis, labels, category=""):
    _seen["transcript"] = transcript
    return {"idea": "trend idea", "cluster_label": "Trend", "people_required": 1}


@pytest.fixture(autouse=True)
def _wire():
    store = VideoStore(db_path=Path(":memory:"))
    video_mod.set_store(store)
    video_mod.set_extract_fn(_fake_extract)
    _seen.clear()
    yield store
    video_mod.set_store(None)
    video_mod.set_extract_fn(None)
    ws.set_fetch_fn(None)


def test_required_capabilities():
    assert REQUIRED_CAPABILITIES == frozenset({"external_data_read"})


async def test_webpage_ingest_requires_url():
    r = await WebIngestPlugin().call_tool("webpage_ingest", {})
    assert r.is_error and "url" in r.content


async def test_webpage_ingest_extracts_clean_text(_wire):
    ws.set_fetch_fn(lambda url: PAGE)
    r = await WebIngestPlugin().call_tool(
        "webpage_ingest", {"url": "https://example.com/a", "category": "trading"})
    assert not r.is_error, r.content
    page = json.loads(r.content)["pages"][0]
    assert page["extracted"] == 1
    t = _seen["transcript"]
    assert "Para 3 about trend following" in t  # inline <a> did not split the line
    assert "evil()" not in t and "Home | About" not in t and "(c) site" not in t
    row = _wire.get_by_url("https://example.com/a")
    assert row.source_type == "webpage" and row.channel == "example.com"
    # idempotent re-read
    r2 = await WebIngestPlugin().call_tool("webpage_ingest", {"url": "https://example.com/a", "category": "trading"})
    assert json.loads(r2.content)["pages"][0]["skipped"] == 1
    spec = WebIngestPlugin().panel_spec()
    assert spec["title"] == "Webpage" and any(w.get("label") == "Trading" for w in spec["widgets"])


async def test_webpage_ingest_rejects_empty_shell():
    ws.set_fetch_fn(lambda url: "<html><body><div id=root></div></body></html>")
    r = await WebIngestPlugin().call_tool("webpage_ingest", {"url": "https://spa.example/"})
    assert r.is_error and "readable text" in r.content
