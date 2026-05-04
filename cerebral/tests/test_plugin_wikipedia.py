"""
Wikipedia MCP plugin tests — Issue #25.

Tools: wiki_search, wiki_summary.

Uses public Wikipedia REST and action APIs:
  - https://en.wikipedia.org/w/api.php (search via opensearch)
  - https://en.wikipedia.org/api/rest_v1/page/summary/{title}

All HTTP calls are injected via fetch_fn so tests never hit the network.
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


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools and create() factory
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_two(self):
        from plugins.wikipedia import create

        names = {t.name for t in create().list_tools()}
        assert names == {"wiki_search", "wiki_summary"}

    def test_create_plugin_named_wikipedia(self):
        from plugins.wikipedia import create

        assert create().name == "wikipedia"

    def test_tools_have_required_args_in_schema(self):
        from plugins.wikipedia import create

        tools = {t.name: t for t in create().list_tools()}
        assert "query" in tools["wiki_search"].schema.get("required", [])
        assert "title" in tools["wiki_summary"].schema.get("required", [])


# ---------------------------------------------------------------------------
# Cycle 2 — Required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_search_missing_query_returns_error(self):
        from plugins.wikipedia import WikipediaPlugin

        plugin = WikipediaPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("wiki_search", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_summary_missing_title_returns_error(self):
        from plugins.wikipedia import WikipediaPlugin

        plugin = WikipediaPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("wiki_summary", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — wiki_search hits action API and shapes results
# ---------------------------------------------------------------------------

class TestWikiSearch:
    @pytest.mark.asyncio
    async def test_search_calls_api_with_query(self):
        from plugins.wikipedia import WikipediaPlugin

        captured: dict = {}
        # Wikipedia opensearch returns: [query, [titles], [descriptions], [urls]]
        plugin = WikipediaPlugin(
            fetch_fn=_make_fetch(
                response=[
                    "Python",
                    ["Python (programming language)", "Monty Python"],
                    ["High-level language", "British comedy group"],
                    [
                        "https://en.wikipedia.org/wiki/Python_(programming_language)",
                        "https://en.wikipedia.org/wiki/Monty_Python",
                    ],
                ],
                captured=captured,
            )
        )
        result = await plugin.call_tool("wiki_search", {"query": "Python"})
        assert not result.is_error
        assert captured["method"] == "GET"
        assert "wikipedia.org" in captured["url"]
        params = captured["params"] or {}
        assert params.get("search") == "Python"
        data = json.loads(result.content)
        assert "results" in data
        assert len(data["results"]) == 2
        assert data["results"][0]["title"] == "Python (programming language)"
        assert "url" in data["results"][0]

    @pytest.mark.asyncio
    async def test_search_respects_max_results(self):
        from plugins.wikipedia import WikipediaPlugin

        captured: dict = {}
        plugin = WikipediaPlugin(
            fetch_fn=_make_fetch(
                response=["q", ["A", "B", "C"], ["a", "b", "c"], ["u1", "u2", "u3"]],
                captured=captured,
            )
        )
        await plugin.call_tool(
            "wiki_search", {"query": "Python", "max_results": 3}
        )
        params = captured["params"] or {}
        assert int(params.get("limit", 0)) == 3

    @pytest.mark.asyncio
    async def test_search_default_limit_used(self):
        from plugins.wikipedia import WikipediaPlugin

        captured: dict = {}
        plugin = WikipediaPlugin(
            fetch_fn=_make_fetch(
                response=["q", [], [], []],
                captured=captured,
            )
        )
        await plugin.call_tool("wiki_search", {"query": "Felix"})
        # default should be set, anything > 0
        assert int((captured["params"] or {}).get("limit", 0)) > 0

    @pytest.mark.asyncio
    async def test_search_empty_results_returned(self):
        from plugins.wikipedia import WikipediaPlugin

        plugin = WikipediaPlugin(
            fetch_fn=_make_fetch(response=["q", [], [], []])
        )
        result = await plugin.call_tool("wiki_search", {"query": "xyzqq"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_search_network_error_returns_is_error(self):
        from plugins.wikipedia import WikipediaPlugin

        plugin = WikipediaPlugin(fetch_fn=_error_fetch())
        result = await plugin.call_tool("wiki_search", {"query": "Python"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — wiki_summary hits REST API
# ---------------------------------------------------------------------------

class TestWikiSummary:
    @pytest.mark.asyncio
    async def test_summary_returns_extract(self):
        from plugins.wikipedia import WikipediaPlugin

        captured: dict = {}
        plugin = WikipediaPlugin(
            fetch_fn=_make_fetch(
                response={
                    "title": "Python (programming language)",
                    "extract": "Python is a high-level programming language.",
                    "description": "general-purpose programming language",
                    "content_urls": {
                        "desktop": {
                            "page": "https://en.wikipedia.org/wiki/Python_(programming_language)"
                        }
                    },
                },
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "wiki_summary", {"title": "Python (programming language)"}
        )
        assert not result.is_error
        # REST URL must include URL-encoded title
        assert "page/summary/" in captured["url"]
        assert "Python" in captured["url"]
        data = json.loads(result.content)
        assert data["title"] == "Python (programming language)"
        assert data["extract"].startswith("Python is")

    @pytest.mark.asyncio
    async def test_summary_handles_missing_extract(self):
        from plugins.wikipedia import WikipediaPlugin

        plugin = WikipediaPlugin(
            fetch_fn=_make_fetch(response={"title": "Foo"})
        )
        result = await plugin.call_tool("wiki_summary", {"title": "Foo"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["extract"] == ""

    @pytest.mark.asyncio
    async def test_summary_url_encodes_title_with_spaces(self):
        from plugins.wikipedia import WikipediaPlugin

        captured: dict = {}
        plugin = WikipediaPlugin(
            fetch_fn=_make_fetch(response={"title": "x", "extract": ""}, captured=captured)
        )
        await plugin.call_tool("wiki_summary", {"title": "Albert Einstein"})
        # Spaces must not appear raw in the URL
        assert " " not in captured["url"]
        # Underscore or %20 form acceptable
        assert "Einstein" in captured["url"]

    @pytest.mark.asyncio
    async def test_summary_network_error_returns_is_error(self):
        from plugins.wikipedia import WikipediaPlugin

        plugin = WikipediaPlugin(fetch_fn=_error_fetch())
        result = await plugin.call_tool(
            "wiki_summary", {"title": "Python"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.wikipedia import WikipediaPlugin

        plugin = WikipediaPlugin(fetch_fn=_make_fetch())
        result = await plugin.call_tool("wiki_random", {"query": "x"})
        assert result.is_error
