"""
Reddit MCP plugin tests — Issue #97.

Tools: reddit_subreddit, reddit_search.

Stateless, read-only over Reddit's public JSON API. All HTTP calls are
injected via fetch_fn so tests never hit the network. The default transport's
User-Agent injection is exercised separately via a fake aiohttp module.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_fetch(*, response=None, captured: dict | None = None):
    """Build an injectable fetch_fn that records calls and returns a canned dict."""
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        if captured is not None:
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            captured["json"] = json
        return response if response is not None else {}
    return fake_fetch


def _error_fetch(exc=None):
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        raise (exc or ConnectionError("network down"))
    return fake_fetch


def _listing(*posts: dict) -> dict:
    """Wrap post-data dicts in Reddit's listing envelope."""
    return {"data": {"children": [{"data": p} for p in posts]}}


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_two(self):
        from plugins.reddit import create

        names = {t.name for t in create().list_tools()}
        assert names == {"reddit_subreddit", "reddit_search"}

    def test_create_plugin_named_reddit(self):
        from plugins.reddit import create

        assert create().name == "reddit"

    def test_required_capabilities_are_cloud_read(self):
        from plugins.reddit import REQUIRED_CAPABILITIES

        assert REQUIRED_CAPABILITIES == frozenset(
            {"external_data_read", "network_egress_cloud"}
        )

    def test_tools_have_required_args_in_schema(self):
        from plugins.reddit import create

        tools = {t.name: t for t in create().list_tools()}
        assert "subreddit" in tools["reddit_subreddit"].schema.get("required", [])
        assert "query" in tools["reddit_search"].schema.get("required", [])


# ---------------------------------------------------------------------------
# Cycle 2 — Required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_subreddit_missing_name_returns_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("reddit_subreddit", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_search_missing_query_returns_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("reddit_search", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — reddit_subreddit listing + shaping
# ---------------------------------------------------------------------------

class TestSubreddit:
    @pytest.mark.asyncio
    async def test_subreddit_default_sort_hot_and_shapes_posts(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(
                response=_listing({
                    "title": "Hello",
                    "author": "alice",
                    "score": 42,
                    "num_comments": 7,
                    "permalink": "/r/python/comments/abc/hello/",
                    "url": "https://example.com/x",
                    "subreddit": "python",
                    "created_utc": 1700000000,
                    "selftext": "body text",
                }),
                captured=captured,
            )
        )
        result = await plugin.call_tool("reddit_subreddit", {"subreddit": "python"})
        assert not result.is_error
        assert captured["method"] == "GET"
        assert captured["url"] == "https://www.reddit.com/r/python/hot.json"
        data = json.loads(result.content)
        post = data["posts"][0]
        assert post["title"] == "Hello"
        assert post["author"] == "alice"
        assert post["score"] == 42
        assert post["num_comments"] == 7
        assert post["permalink"] == "https://reddit.com/r/python/comments/abc/hello/"
        assert post["subreddit"] == "python"
        assert post["selftext"] == "body text"

    @pytest.mark.asyncio
    async def test_subreddit_top_sends_time_window(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(response=_listing(), captured=captured)
        )
        await plugin.call_tool(
            "reddit_subreddit",
            {"subreddit": "news", "sort": "top", "time": "week"},
        )
        assert captured["url"] == "https://www.reddit.com/r/news/top.json"
        assert (captured["params"] or {}).get("t") == "week"

    @pytest.mark.asyncio
    async def test_subreddit_top_default_time_is_day(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(response=_listing(), captured=captured)
        )
        await plugin.call_tool(
            "reddit_subreddit", {"subreddit": "news", "sort": "top"}
        )
        assert (captured["params"] or {}).get("t") == "day"

    @pytest.mark.asyncio
    async def test_subreddit_non_top_sort_omits_time(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(response=_listing(), captured=captured)
        )
        await plugin.call_tool(
            "reddit_subreddit", {"subreddit": "news", "sort": "new"}
        )
        assert "t" not in (captured["params"] or {})
        assert captured["url"] == "https://www.reddit.com/r/news/new.json"

    @pytest.mark.asyncio
    async def test_subreddit_invalid_sort_returns_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch(response=_listing()))
        result = await plugin.call_tool(
            "reddit_subreddit", {"subreddit": "x", "sort": "controversial"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_subreddit_invalid_time_returns_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch(response=_listing()))
        result = await plugin.call_tool(
            "reddit_subreddit",
            {"subreddit": "x", "sort": "top", "time": "decade"},
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_subreddit_respects_max_results(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(
                response=_listing(
                    {"title": "a"}, {"title": "b"}, {"title": "c"}
                ),
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "reddit_subreddit", {"subreddit": "x", "max_results": 2}
        )
        assert int((captured["params"] or {}).get("limit", 0)) == 2
        data = json.loads(result.content)
        assert len(data["posts"]) == 2

    @pytest.mark.asyncio
    async def test_subreddit_default_limit_used(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(response=_listing(), captured=captured)
        )
        await plugin.call_tool("reddit_subreddit", {"subreddit": "x"})
        assert int((captured["params"] or {}).get("limit", 0)) > 0

    @pytest.mark.asyncio
    async def test_subreddit_truncates_long_selftext(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(
            fetch_fn=_make_fetch(
                response=_listing({"title": "t", "selftext": "x" * 999})
            )
        )
        result = await plugin.call_tool("reddit_subreddit", {"subreddit": "x"})
        data = json.loads(result.content)
        assert len(data["posts"][0]["selftext"]) == 500

    @pytest.mark.asyncio
    async def test_subreddit_empty_listing_returns_empty(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch(response=_listing()))
        result = await plugin.call_tool("reddit_subreddit", {"subreddit": "x"})
        assert not result.is_error
        assert json.loads(result.content)["posts"] == []

    @pytest.mark.asyncio
    async def test_subreddit_non_dict_response_is_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch(response=["nope"]))
        result = await plugin.call_tool("reddit_subreddit", {"subreddit": "x"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_subreddit_network_error_is_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_error_fetch())
        result = await plugin.call_tool("reddit_subreddit", {"subreddit": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — reddit_search
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_search_global_endpoint_and_query(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(
                response=_listing({"title": "match"}), captured=captured
            )
        )
        result = await plugin.call_tool("reddit_search", {"query": "felix"})
        assert not result.is_error
        assert captured["url"] == "https://www.reddit.com/search.json"
        params = captured["params"] or {}
        assert params.get("q") == "felix"
        assert params.get("sort") == "relevance"
        assert "restrict_sr" not in params
        assert json.loads(result.content)["posts"][0]["title"] == "match"

    @pytest.mark.asyncio
    async def test_search_with_subreddit_restricts(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(response=_listing(), captured=captured)
        )
        await plugin.call_tool(
            "reddit_search", {"query": "felix", "subreddit": "python"}
        )
        assert captured["url"] == "https://www.reddit.com/r/python/search.json"
        assert (captured["params"] or {}).get("restrict_sr") == 1

    @pytest.mark.asyncio
    async def test_search_custom_sort(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(response=_listing(), captured=captured)
        )
        await plugin.call_tool(
            "reddit_search", {"query": "x", "sort": "top"}
        )
        assert (captured["params"] or {}).get("sort") == "top"

    @pytest.mark.asyncio
    async def test_search_invalid_sort_returns_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch(response=_listing()))
        result = await plugin.call_tool(
            "reddit_search", {"query": "x", "sort": "spicy"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_search_respects_max_results(self):
        from plugins.reddit import RedditPlugin

        captured: dict = {}
        plugin = RedditPlugin(
            fetch_fn=_make_fetch(
                response=_listing({"title": "a"}, {"title": "b"}),
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "reddit_search", {"query": "x", "max_results": 1}
        )
        assert int((captured["params"] or {}).get("limit", 0)) == 1
        assert len(json.loads(result.content)["posts"]) == 1

    @pytest.mark.asyncio
    async def test_search_non_dict_response_is_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch(response="oops"))
        result = await plugin.call_tool("reddit_search", {"query": "x"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_search_network_error_is_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_error_fetch())
        result = await plugin.call_tool("reddit_search", {"query": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.reddit import RedditPlugin

        plugin = RedditPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("reddit_random", {"query": "x"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — default transport injects a User-Agent (the Reddit 429 gotcha)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, captured, payload):
        self._captured = captured
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def request(self, method, url, *, headers=None, params=None, json=None):
        self._captured["headers"] = headers
        return _FakeResp(self._payload)


class TestDefaultTransportUserAgent:
    @pytest.mark.asyncio
    async def test_default_fetch_injects_user_agent(self, monkeypatch):
        import types

        from plugins import reddit

        captured: dict = {}

        fake_aiohttp = types.ModuleType("aiohttp")
        fake_aiohttp.ClientSession = lambda: _FakeSession(captured, {"ok": True})
        monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

        out = await reddit._default_fetch(
            "GET", "https://www.reddit.com/r/x/hot.json"
        )
        assert out == {"ok": True}
        assert captured["headers"]["User-Agent"] == "OpenMind-Reddit/1.0"

    @pytest.mark.asyncio
    async def test_default_fetch_merges_caller_headers(self, monkeypatch):
        import types

        from plugins import reddit

        captured: dict = {}
        fake_aiohttp = types.ModuleType("aiohttp")
        fake_aiohttp.ClientSession = lambda: _FakeSession(captured, {})
        monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

        await reddit._default_fetch(
            "GET", "https://www.reddit.com/r/x/hot.json",
            headers={"X-Test": "1"},
        )
        assert captured["headers"]["User-Agent"] == "OpenMind-Reddit/1.0"
        assert captured["headers"]["X-Test"] == "1"

    @pytest.mark.asyncio
    async def test_default_fetch_no_http_lib_raises(self, monkeypatch):
        from plugins import reddit

        monkeypatch.setitem(sys.modules, "aiohttp", None)
        monkeypatch.setitem(sys.modules, "httpx", None)
        with pytest.raises(RuntimeError):
            await reddit._default_fetch("GET", "https://www.reddit.com/x.json")
