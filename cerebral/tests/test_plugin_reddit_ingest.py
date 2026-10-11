"""reddit_ingest (Library "Reddit" tab): stubbed Atom feeds, no network.
A thread link -> one item (post + comments); a subreddit -> one item per post;
a 429 becomes a plain retry message."""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

import plugins.video as video_mod
from cerebral.video import web_source as ws
from cerebral.video.store import VideoStore
from plugins.reddit_ingest import REQUIRED_CAPABILITIES, RedditIngestPlugin

_seen: list = []


def _entry(eid, title, author, html, link="https://www.reddit.com/r/algotrading/comments/abc/x/"):
    return (f"<entry><id>{eid}</id><title>{title}</title><author><name>{author}</name></author>"
            f'<link href="{link}"/><content type="html">{html}</content></entry>')


def _feed(*entries):
    return '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>' + "".join(entries) + "</feed>"


THREAD = _feed(
    _entry("t3_abc", "Breadth thrust works?", "/u/op", "&lt;p&gt;My backtest says yes.&lt;/p&gt;"),
    _entry("t1_c1", "/u/a on x", "/u/a", "&lt;div class=md&gt;&lt;p&gt;Survivorship bias.&lt;/p&gt;&lt;/div&gt;"),
    _entry("t1_c2", "/u/b on x", "/u/b", "&lt;p&gt;Check 2008.&lt;/p&gt;"),
)


async def _fake_extract(transcript, ocr, vis, labels, category=""):
    _seen.append(transcript)
    return {"idea": "idea", "cluster_label": "Breadth", "people_required": 1}


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


def test_parse_reddit_url():
    assert ws.parse_reddit_url("https://old.reddit.com/r/algotrading/comments/abc/slug/?x=1") == {
        "kind": "thread", "sub": "algotrading",
        "url": "https://www.reddit.com/r/algotrading/comments/abc/slug"}
    assert ws.parse_reddit_url("reddit.com/r/x") is None  # no scheme
    assert ws.parse_reddit_url("https://www.reddit.com/r/algotrading/")["kind"] == "subreddit"
    assert ws.parse_reddit_url("https://example.com/r/x") is None


async def test_thread_is_one_item_with_comments(_wire):
    urls = []
    ws.set_fetch_fn(lambda u: urls.append(u) or THREAD)
    r = await RedditIngestPlugin().call_tool(
        "reddit_ingest", {"url": "https://www.reddit.com/r/algotrading/comments/abc/x/"})
    assert not r.is_error, r.content
    assert len(urls) == 1 and urls[0].endswith("/.rss?limit=50")
    assert json.loads(r.content)["extracted"] == 1
    t = _seen[0]
    assert "Breadth thrust works?" in t and "My backtest says yes." in t
    assert "/u/a: Survivorship bias." in t and "/u/b: Check 2008." in t
    row = _wire.get_by_url("https://www.reddit.com/r/algotrading/comments/abc/x/")
    assert row.source_type == "reddit" and row.channel == "r/algotrading"


async def test_subreddit_is_one_item_per_post():
    ws.set_fetch_fn(lambda u: _feed(
        _entry("t3_a", "A", "/u/1", "&lt;p&gt;aaa&lt;/p&gt;", "https://www.reddit.com/r/s/comments/a/"),
        _entry("t3_b", "B", "/u/2", "&lt;p&gt;bbb&lt;/p&gt;", "https://www.reddit.com/r/s/comments/b/"),
    ))
    r = await RedditIngestPlugin().call_tool("reddit_ingest", {"url": "https://www.reddit.com/r/s"})
    assert json.loads(r.content)["extracted"] == 2


async def test_rate_limit_and_bad_url():
    def _429(u):
        raise urllib.error.HTTPError(u, 429, "Too Many Requests", {}, None)
    ws.set_fetch_fn(_429)
    r = await RedditIngestPlugin().call_tool("reddit_ingest", {"url": "https://www.reddit.com/r/s"})
    assert r.is_error and "minute" in r.content
    r = await RedditIngestPlugin().call_tool("reddit_ingest", {"url": "https://example.com/r/s"})
    assert r.is_error and "Not a Reddit URL" in r.content
