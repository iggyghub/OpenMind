"""
News MCP plugin tests — Issue #25.

Tools: news_headlines, news_list_sources.

Aggregates configurable RSS feeds. Default sources injected at construction.
RSS parsing is delegated to feedparser via an injectable parse_fn so tests
never hit the network and don't depend on feedparser being importable.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _feed(title, entries):
    """Build a feedparser-shaped response (object with .entries / .feed)."""
    class _Entry(dict):
        def __getattr__(self, k):
            return self[k]

    class _Feed:
        def __init__(self):
            self.feed = {"title": title}
            self.entries = [_Entry(e) for e in entries]

    return _Feed()


def _make_parse_fn(feeds_by_url, captured: list | None = None):
    """parse_fn(url) -> object with .entries — keyed by url."""
    def fake_parse(url):
        if captured is not None:
            captured.append(url)
        if url not in feeds_by_url:
            raise ValueError(f"unexpected url: {url}")
        return feeds_by_url[url]
    return fake_parse


_DEFAULT_SOURCES = {
    "BBC": "http://feeds.bbci.co.uk/news/rss.xml",
    "Reuters": "https://www.reutersagency.com/feed/",
    "Hacker News": "https://hnrss.org/frontpage",
}


_BBC_ENTRIES = [
    {"title": "Headline A", "link": "https://bbc.co.uk/a", "summary": "summary a", "published": "2026-05-04"},
    {"title": "Headline B", "link": "https://bbc.co.uk/b", "summary": "summary b", "published": "2026-05-04"},
]

_HN_ENTRIES = [
    {"title": "HN Story", "link": "https://news.ycombinator.com/x", "summary": "tech", "published": "2026-05-04"},
]


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools, create()
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_two(self):
        from plugins.news import create

        names = {t.name for t in create().list_tools()}
        assert names == {"news_headlines", "news_list_sources"}

    def test_plugin_name_is_news(self):
        from plugins.news import create

        assert create().name == "news"


# ---------------------------------------------------------------------------
# Cycle 2 — list_sources returns default sources
# ---------------------------------------------------------------------------

class TestListSources:
    @pytest.mark.asyncio
    async def test_list_sources_returns_defaults(self):
        from plugins.news import NewsPlugin

        plugin = NewsPlugin(parse_fn=_make_parse_fn({}))
        result = await plugin.call_tool("news_list_sources", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert "sources" in data
        names = [s["name"] for s in data["sources"]]
        # Must contain at least the three required defaults
        for required in ("BBC", "Reuters", "Hacker News"):
            assert required in names

    @pytest.mark.asyncio
    async def test_list_sources_custom_overrides_defaults(self):
        from plugins.news import NewsPlugin

        plugin = NewsPlugin(
            sources={"My Feed": "http://example.com/rss"},
            parse_fn=_make_parse_fn({}),
        )
        result = await plugin.call_tool("news_list_sources", {})
        data = json.loads(result.content)
        names = [s["name"] for s in data["sources"]]
        assert names == ["My Feed"]


# ---------------------------------------------------------------------------
# Cycle 3 — news_headlines aggregates entries from all sources
# ---------------------------------------------------------------------------

class TestHeadlines:
    @pytest.mark.asyncio
    async def test_headlines_aggregates_all_sources(self):
        from plugins.news import NewsPlugin

        captured: list = []
        plugin = NewsPlugin(
            sources={
                "BBC": "https://bbc.example/rss",
                "HN": "https://hn.example/rss",
            },
            parse_fn=_make_parse_fn(
                {
                    "https://bbc.example/rss": _feed("BBC", _BBC_ENTRIES),
                    "https://hn.example/rss": _feed("HN", _HN_ENTRIES),
                },
                captured=captured,
            ),
        )
        result = await plugin.call_tool("news_headlines", {})
        assert not result.is_error
        # Both feeds must have been parsed
        assert "https://bbc.example/rss" in captured
        assert "https://hn.example/rss" in captured

        data = json.loads(result.content)
        titles = [h["title"] for h in data["headlines"]]
        assert "Headline A" in titles
        assert "HN Story" in titles
        # Each headline carries source tag
        sources = {h["source"] for h in data["headlines"]}
        assert "BBC" in sources
        assert "HN" in sources

    @pytest.mark.asyncio
    async def test_headlines_filters_by_source(self):
        from plugins.news import NewsPlugin

        plugin = NewsPlugin(
            sources={
                "BBC": "https://bbc.example/rss",
                "HN": "https://hn.example/rss",
            },
            parse_fn=_make_parse_fn(
                {
                    "https://bbc.example/rss": _feed("BBC", _BBC_ENTRIES),
                    "https://hn.example/rss": _feed("HN", _HN_ENTRIES),
                }
            ),
        )
        result = await plugin.call_tool(
            "news_headlines", {"source": "BBC"}
        )
        data = json.loads(result.content)
        sources = {h["source"] for h in data["headlines"]}
        assert sources == {"BBC"}

    @pytest.mark.asyncio
    async def test_headlines_unknown_source_returns_error(self):
        from plugins.news import NewsPlugin

        plugin = NewsPlugin(
            sources={"BBC": "https://bbc.example/rss"},
            parse_fn=_make_parse_fn({"https://bbc.example/rss": _feed("BBC", [])}),
        )
        result = await plugin.call_tool(
            "news_headlines", {"source": "NotARealSource"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_headlines_filters_by_topic(self):
        from plugins.news import NewsPlugin

        plugin = NewsPlugin(
            sources={"BBC": "https://bbc.example/rss"},
            parse_fn=_make_parse_fn(
                {
                    "https://bbc.example/rss": _feed("BBC", [
                        {"title": "Mars rover update", "link": "u1", "summary": "space exploration"},
                        {"title": "Election result", "link": "u2", "summary": "politics"},
                    ])
                }
            ),
        )
        result = await plugin.call_tool(
            "news_headlines", {"topic": "Mars"}
        )
        data = json.loads(result.content)
        titles = [h["title"] for h in data["headlines"]]
        assert "Mars rover update" in titles
        assert "Election result" not in titles

    @pytest.mark.asyncio
    async def test_headlines_respects_max_results(self):
        from plugins.news import NewsPlugin

        plugin = NewsPlugin(
            sources={"BBC": "https://bbc.example/rss"},
            parse_fn=_make_parse_fn(
                {
                    "https://bbc.example/rss": _feed("BBC", _BBC_ENTRIES),
                }
            ),
        )
        result = await plugin.call_tool(
            "news_headlines", {"max_results": 1}
        )
        data = json.loads(result.content)
        assert len(data["headlines"]) == 1

    @pytest.mark.asyncio
    async def test_headlines_handles_parse_exception(self):
        """A failing source must not poison the aggregate — other sources still return."""
        from plugins.news import NewsPlugin

        good_url = "https://bbc.example/rss"
        bad_url = "https://broken.example/rss"

        def parse(url):
            if url == bad_url:
                raise ConnectionError("offline")
            return _feed("BBC", _BBC_ENTRIES)

        plugin = NewsPlugin(
            sources={"BBC": good_url, "Bad": bad_url},
            parse_fn=parse,
        )
        result = await plugin.call_tool("news_headlines", {})
        assert not result.is_error
        data = json.loads(result.content)
        sources = {h["source"] for h in data["headlines"]}
        assert "BBC" in sources

    @pytest.mark.asyncio
    async def test_headlines_all_sources_fail_returns_error(self):
        from plugins.news import NewsPlugin

        def parse(url):
            raise ConnectionError("offline")

        plugin = NewsPlugin(
            sources={"BBC": "https://bbc.example/rss"},
            parse_fn=parse,
        )
        result = await plugin.call_tool("news_headlines", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.news import NewsPlugin

        plugin = NewsPlugin(parse_fn=_make_parse_fn({}))
        result = await plugin.call_tool("news_search", {"q": "x"})
        assert result.is_error
