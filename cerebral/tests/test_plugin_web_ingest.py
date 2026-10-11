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


async def test_reddit_link_is_handed_to_reddit_reader():
    import plugins.reddit_ingest as ri

    async def _no_sleep(_s):
        return None
    ri._sleep = _no_sleep
    ri._CRAWL.update(running=False)
    urls = []
    ws.set_fetch_fn(lambda u: urls.append(u) or (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>t3_q</id><title>Q</title>'
        '<link href="https://www.reddit.com/r/AI_Agents/comments/q/x/"/><content type="html"></content></entry></feed>'))
    try:
        r = await WebIngestPlugin().call_tool("webpage_ingest", {"url": "https://www.reddit.com/r/AI_Agents/"})
        page = json.loads(r.content)["pages"][0]
        assert page["handed_to"] == "reddit_ingest" and page["status"] == "started"
        assert urls[0] == "https://www.reddit.com/r/AI_Agents/top/.rss?t=all&limit=10"
        await ri._crawl_task
    finally:
        ri._sleep = __import__("asyncio").sleep


async def test_read_page_is_saved_as_foldered_document(tmp_path):
    import plugins.documents as docs

    store = docs.DocumentStore(db_path=tmp_path / "d.db", store_root=tmp_path / "lib")
    docs.set_store(store)
    docs.set_active_profile_id(7)
    try:
        ws.set_fetch_fn(lambda url: PAGE)
        await WebIngestPlugin().call_tool("webpage_ingest", {"url": "https://example.com/a", "category": "trading"})
        [d] = store.list_docs(7)
        assert d["folder"] == "Webpages/example.com" and d["name"] == "Momentum" and d["kind"] == "md"
        body = Path(d["path"]).read_text(encoding="utf-8")
        assert "Source: https://example.com/a" in body
        assert "## What Felix took from it" in body and "trend idea" in body
        assert "## What Felix read" in body and "Para 3 about trend following" in body
        # same folder + name overwrites instead of duplicating
        store.save_text(7, "Webpages/example.com", "Momentum", "v2")
        assert len(store.list_docs(7)) == 1 and Path(d["path"]).read_text(encoding="utf-8") == "v2"
    finally:
        docs.set_store(None)
        docs.set_active_profile_id(None)


async def test_webpage_ingest_rejects_empty_shell():
    ws.set_fetch_fn(lambda url: "<html><body><div id=root></div></body></html>")
    r = await WebIngestPlugin().call_tool("webpage_ingest", {"url": "https://spa.example/"})
    assert r.is_error and "readable text" in r.content
