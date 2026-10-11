"""
Reddit MCP plugin tests — Issue #97.

Tools: reddit_subreddit, reddit_search. Since 2026-10-11 the transport is
Reddit's public Atom feeds (the .json endpoints 403). fetch_fn(url) -> XML is
injected so tests never hit the network.
"""
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _entry(eid, title, author="/u/alice", html="", link="https://www.reddit.com/r/python/comments/abc/hello/"):
    return (f"<entry><id>{eid}</id><title>{title}</title><author><name>{author}</name></author>"
            f'<link href="{link}"/><content type="html">{html}</content></entry>')


def _feed(*entries):
    return '<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>' + "".join(entries) + "</feed>"


def _make_fetch(xml="", captured=None):
    async def fake_fetch(url):
        if captured is not None:
            base, _, q = url.partition("?")
            captured["url"] = base
            captured["params"] = dict(urllib.parse.parse_qsl(q))
        return xml or _feed()
    return fake_fetch


def _error_fetch():
    async def fake_fetch(url):
        raise ConnectionError("network down")
    return fake_fetch


# -- list_tools / capabilities -------------------------------------------------

def test_list_tools_and_capabilities():
    from plugins.reddit import REQUIRED_CAPABILITIES, create

    plugin = create()
    tools = {t.name: t for t in plugin.list_tools()}
    assert plugin.name == "reddit"
    assert set(tools) == {"reddit_subreddit", "reddit_search"}
    assert "subreddit" in tools["reddit_subreddit"].schema["required"]
    assert "query" in tools["reddit_search"].schema["required"]
    assert REQUIRED_CAPABILITIES == frozenset({"external_data_read", "network_egress_cloud"})


# -- required args / validation ------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("reddit_subreddit", {}),
    ("reddit_search", {}),
    ("reddit_subreddit", {"subreddit": "x", "sort": "controversial"}),
    ("reddit_subreddit", {"subreddit": "x", "sort": "top", "time": "decade"}),
    ("reddit_search", {"query": "x", "sort": "spicy"}),
    ("reddit_random", {"query": "x"}),
])
async def test_bad_args_are_errors(tool, args):
    from plugins.reddit import RedditPlugin

    assert (await RedditPlugin(fetch_fn=_make_fetch()).call_tool(tool, args)).is_error


# -- reddit_subreddit ----------------------------------------------------------

@pytest.mark.asyncio
async def test_subreddit_hot_feed_and_shapes_posts():
    from plugins.reddit import RedditPlugin

    captured: dict = {}
    xml = _feed(
        _entry("t3_abc", "Hello", html="&lt;p&gt;body text&lt;/p&gt;"),
        _entry("t1_c", "/u/b on Hello"),  # a comment entry is not a post
    )
    result = await RedditPlugin(fetch_fn=_make_fetch(xml, captured)).call_tool(
        "reddit_subreddit", {"subreddit": "r/python"})
    assert not result.is_error
    assert captured["url"] == "https://www.reddit.com/r/python/hot/.rss"
    posts = json.loads(result.content)["posts"]
    assert posts == [{
        "title": "Hello", "author": "alice",
        "permalink": "https://www.reddit.com/r/python/comments/abc/hello/",
        "subreddit": "python", "selftext": "body text",
    }]


@pytest.mark.asyncio
async def test_subreddit_top_time_window():
    from plugins.reddit import RedditPlugin

    captured: dict = {}
    plugin = RedditPlugin(fetch_fn=_make_fetch(captured=captured))
    await plugin.call_tool("reddit_subreddit", {"subreddit": "news", "sort": "top", "time": "week"})
    assert captured["url"] == "https://www.reddit.com/r/news/top/.rss"
    assert captured["params"]["t"] == "week"
    await plugin.call_tool("reddit_subreddit", {"subreddit": "news", "sort": "top"})
    assert captured["params"]["t"] == "day"
    await plugin.call_tool("reddit_subreddit", {"subreddit": "news", "sort": "new"})
    assert "t" not in captured["params"]


@pytest.mark.asyncio
async def test_subreddit_max_results_and_selftext_cap():
    from plugins.reddit import RedditPlugin

    captured: dict = {}
    xml = _feed(*(_entry(f"t3_{i}", f"p{i}", html="x" * 999) for i in range(3)))
    result = await RedditPlugin(fetch_fn=_make_fetch(xml, captured)).call_tool(
        "reddit_subreddit", {"subreddit": "x", "max_results": 2})
    posts = json.loads(result.content)["posts"]
    assert captured["params"]["limit"] == "2"
    assert len(posts) == 2 and len(posts[0]["selftext"]) == 500


@pytest.mark.asyncio
async def test_network_and_parse_errors_are_errors():
    from plugins.reddit import RedditPlugin

    assert (await RedditPlugin(fetch_fn=_error_fetch()).call_tool(
        "reddit_subreddit", {"subreddit": "x"})).is_error
    assert (await RedditPlugin(fetch_fn=_make_fetch("<html>not a feed")).call_tool(
        "reddit_search", {"query": "x"})).is_error


# -- reddit_search ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_global_and_restricted():
    from plugins.reddit import RedditPlugin

    captured: dict = {}
    plugin = RedditPlugin(fetch_fn=_make_fetch(_feed(_entry("t3_m", "match")), captured))
    result = await plugin.call_tool("reddit_search", {"query": "felix"})
    assert captured["url"] == "https://www.reddit.com/search.rss"
    assert captured["params"]["q"] == "felix" and captured["params"]["sort"] == "relevance"
    assert "restrict_sr" not in captured["params"]
    assert json.loads(result.content)["posts"][0]["title"] == "match"

    await plugin.call_tool("reddit_search", {"query": "felix", "subreddit": "python", "sort": "top"})
    assert captured["url"] == "https://www.reddit.com/r/python/search.rss"
    assert captured["params"]["restrict_sr"] == "1" and captured["params"]["sort"] == "top"
