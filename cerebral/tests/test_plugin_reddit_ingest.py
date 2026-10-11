"""reddit_ingest (Library "Reddit" tab): stubbed Atom feeds + no-op pacing.
A thread link reads post + discussion now; a subreddit or keyword search lists
threads, then a background crawl opens EACH thread and reads its comments."""
from __future__ import annotations

import asyncio
import json
import urllib.error
from pathlib import Path

import pytest

import plugins.reddit_ingest as ri
import plugins.video as video_mod
from cerebral.video import web_source as ws
from cerebral.video.store import VideoStore
from plugins.reddit_ingest import REQUIRED_CAPABILITIES, RedditIngestPlugin

_seen: list = []


def _entry(eid, title, author, html, link):
    return (f"<entry><id>{eid}</id><title>{title}</title><author><name>{author}</name></author>"
            f'<link href="{link}"/><content type="html">{html}</content></entry>')


def _feed(*entries):
    return '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>' + "".join(entries) + "</feed>"


def _thread(tid, title, comment):
    link = f"https://www.reddit.com/r/algotrading/comments/{tid}/x/"
    return _feed(
        _entry(f"t3_{tid}", title, "/u/op", "&lt;p&gt;Post body.&lt;/p&gt;", link),
        _entry(f"t1_{tid}c", "/u/a on x", "/u/a", f"&lt;p&gt;{comment}&lt;/p&gt;", link + "c/"),
    )


LISTING = _feed(
    _entry("t3_aaa", "Thread A", "/u/1", "", "https://www.reddit.com/r/algotrading/comments/aaa/x/"),
    _entry("t3_bbb", "Thread B", "/u/2", "", "https://www.reddit.com/r/algotrading/comments/bbb/x/"),
)


def _router(urls):
    def fetch(u):
        urls.append(u)
        if "/comments/aaa" in u:
            return _thread("aaa", "Thread A", "Comment on A.")
        if "/comments/bbb" in u:
            return _thread("bbb", "Thread B", "Comment on B.")
        return LISTING
    return fetch


async def _fake_extract(transcript, ocr, vis, labels, category=""):
    _seen.append(transcript)
    return {"idea": "idea", "cluster_label": "Breadth", "people_required": 1}


async def _no_sleep(_s):
    return None


@pytest.fixture(autouse=True)
def _wire():
    store = VideoStore(db_path=Path(":memory:"))
    video_mod.set_store(store)
    video_mod.set_extract_fn(_fake_extract)
    ri._sleep = _no_sleep
    ri._CRAWL.update(running=False, done=0, total=0)
    _seen.clear()
    yield store
    video_mod.set_store(None)
    video_mod.set_extract_fn(None)
    ws.set_fetch_fn(None)
    ri._sleep = asyncio.sleep


def test_required_capabilities():
    assert REQUIRED_CAPABILITIES == frozenset({"external_data_read"})


def test_reddit_target():
    assert ws.reddit_target("https://old.reddit.com/r/algotrading/comments/abc/slug/?x=1") == {
        "kind": "thread", "sub": "algotrading",
        "url": "https://www.reddit.com/r/algotrading/comments/abc/slug"}
    assert ws.reddit_target("https://www.reddit.com/r/algotrading/")["kind"] == "subreddit"
    assert ws.reddit_target("r/algotrading")["kind"] == "subreddit"
    assert ws.reddit_target("r/algotrading breadth thrust") == {
        "kind": "search", "sub": "algotrading", "query": "breadth thrust"}
    assert ws.reddit_target("breadth thrust")["sub"] is None
    assert ws.reddit_target("https://example.com/r/x") is None
    assert "restrict_sr=1" in ws.listing_url(ws.reddit_target("r/a b c"), 5)
    assert ws.listing_url(ws.reddit_target("b c"), 5).startswith("https://www.reddit.com/search.rss?q=b+c")


async def test_thread_link_reads_discussion_now(_wire):
    urls = []
    ws.set_fetch_fn(_router(urls))
    r = await RedditIngestPlugin().call_tool(
        "reddit_ingest", {"url": "https://www.reddit.com/r/algotrading/comments/aaa/x/"})
    assert not r.is_error, r.content
    assert urls == ["https://www.reddit.com/r/algotrading/comments/aaa/x/.rss?limit=25&sort=top"]
    assert "Post body." in _seen[0] and "/u/a: Comment on A." in _seen[0]
    row = _wire.get_by_url("https://www.reddit.com/r/algotrading/comments/aaa/x")
    assert row.source_type == "reddit" and row.channel == "r/algotrading"


async def test_search_crawls_every_thread_discussion(_wire):
    urls = []
    ws.set_fetch_fn(_router(urls))
    r = await RedditIngestPlugin().call_tool(
        "reddit_ingest", {"url": "r/algotrading breadth thrust", "category": "trading"})
    body = json.loads(r.content)
    assert body["status"] == "started" and body["threads"] == 2
    assert "search.rss?q=breadth+thrust&restrict_sr=1" in urls[0]
    assert ri.RedditIngestPlugin().panel_spec()["widgets"][1]["items"][0]["title"].startswith("Reading search")
    await ri._shared["reddit.crawl_task"]
    assert any("Comment on A." in t for t in _seen) and any("Comment on B." in t for t in _seen)
    assert ri._CRAWL["done"] == 2 and not ri._CRAWL["running"]
    # second crawl: both threads already read -> nothing new, no thread requests
    urls.clear()
    r = await RedditIngestPlugin().call_tool("reddit_ingest", {"url": "r/algotrading"})
    assert json.loads(r.content)["status"] == "nothing_new" and len(urls) == 1


async def test_429_backs_off_then_succeeds():
    calls = []

    def flaky(u):
        calls.append(u)
        if len(calls) == 1:
            raise urllib.error.HTTPError(u, 429, "Too Many Requests", {}, None)
        return _thread("aaa", "Thread A", "ok")
    ws.set_fetch_fn(flaky)
    r = await RedditIngestPlugin().call_tool(
        "reddit_ingest", {"url": "https://www.reddit.com/r/algotrading/comments/aaa/x/"})
    assert not r.is_error and len(calls) == 2


async def test_stop_cancels_crawl():
    ws.set_fetch_fn(_router([]))
    gate = asyncio.Event()

    async def slow(_s):
        await gate.wait()
    ri._sleep = slow
    await RedditIngestPlugin().call_tool("reddit_ingest", {"url": "r/algotrading"})
    await asyncio.sleep(0)
    await RedditIngestPlugin().call_tool("reddit_stop", {})
    with pytest.raises(asyncio.CancelledError):
        await ri._shared["reddit.crawl_task"]
    assert not ri._CRAWL["running"]


async def test_bad_input():
    r = await RedditIngestPlugin().call_tool("reddit_ingest", {"url": "https://example.com/r/s"})
    assert r.is_error
