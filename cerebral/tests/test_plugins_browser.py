"""
Browser MCP plugin tests — Issue #17.

TDD vertical slices for BrowserPlugin:
  web_search, navigate, read_pdf — all HTTP side-effects injected via fetch_fn.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fetch(response: dict):
    """Return an async fetch_fn that always resolves to `response`."""
    async def _fetch(url: str, body: dict) -> dict:
        return response
    return _fetch


def _make_fetch_error(exc: Exception):
    """Return an async fetch_fn that always raises `exc`."""
    async def _fetch(url: str, body: dict) -> dict:
        raise exc
    return _fetch


# ===========================================================================
# Cycle 1 — plugin can be imported, instantiated, and lists 3 tools
# ===========================================================================

class TestBrowserPluginMeta:
    def test_plugin_name(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({}))
        assert plugin.name == "browser"

    def test_lists_three_tools(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({}))
        names = {t.name for t in plugin.list_tools()}
        assert names == {"web_search", "navigate", "read_pdf"}

    def test_create_returns_browser_plugin(self):
        from plugins.browser import create, BrowserPlugin
        plugin = create()
        assert isinstance(plugin, BrowserPlugin)


# ===========================================================================
# Cycle 2 — web_search returns results
# ===========================================================================

class TestWebSearch:
    @pytest.mark.asyncio
    async def test_web_search_returns_results(self):
        from plugins.browser import BrowserPlugin
        fake_response = {
            "results": [
                {"title": "Python 3.13", "url": "https://python.org", "snippet": "Latest release"},
            ]
        }
        plugin = BrowserPlugin(fetch_fn=_make_fetch(fake_response))
        result = await plugin.call_tool("web_search", {"query": "latest python version"})
        assert not result.is_error
        data = json.loads(result.content)
        assert "results" in data
        assert data["results"][0]["title"] == "Python 3.13"

    @pytest.mark.asyncio
    async def test_web_search_passes_query_to_fetch(self):
        """fetch_fn must receive the query in the body."""
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured.update({"url": url, "body": body})
            return {"results": []}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("web_search", {"query": "hello world"})
        assert captured["body"]["query"] == "hello world"

    @pytest.mark.asyncio
    async def test_web_search_passes_max_results(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured.update(body)
            return {"results": []}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("web_search", {"query": "q", "max_results": 3})
        assert captured["max_results"] == 3

    @pytest.mark.asyncio
    async def test_web_search_default_max_results_is_5(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured.update(body)
            return {"results": []}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("web_search", {"query": "q"})
        assert captured["max_results"] == 5

    @pytest.mark.asyncio
    async def test_web_search_error_on_fetch_failure(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch_error(ConnectionError("no network")))
        result = await plugin.call_tool("web_search", {"query": "anything"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_web_search_missing_query_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({"results": []}))
        result = await plugin.call_tool("web_search", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_web_search_posts_to_browser_search_endpoint(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured["url"] = url
            return {"results": []}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("web_search", {"query": "q"})
        assert "/browser/search" in captured["url"]


# ===========================================================================
# Cycle 3 — navigate extracts readable content
# ===========================================================================

class TestNavigate:
    @pytest.mark.asyncio
    async def test_navigate_returns_url_and_content(self):
        from plugins.browser import BrowserPlugin
        fake_response = {"content": "Python 3.13 was released in October 2024."}
        plugin = BrowserPlugin(fetch_fn=_make_fetch(fake_response))
        result = await plugin.call_tool("navigate", {"url": "https://python.org"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["url"] == "https://python.org"
        assert "Python 3.13" in data["content"]

    @pytest.mark.asyncio
    async def test_navigate_passes_url_to_fetch(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured.update(body)
            return {"content": "ok"}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("navigate", {"url": "https://example.com"})
        assert captured["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_navigate_posts_to_browser_navigate_endpoint(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured["endpoint"] = url
            return {"content": ""}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("navigate", {"url": "https://example.com"})
        assert "/browser/navigate" in captured["endpoint"]

    @pytest.mark.asyncio
    async def test_navigate_error_on_fetch_failure(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch_error(TimeoutError("timeout")))
        result = await plugin.call_tool("navigate", {"url": "https://example.com"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_navigate_missing_url_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({"content": ""}))
        result = await plugin.call_tool("navigate", {})
        assert result.is_error


# ===========================================================================
# Cycle 4 — read_pdf extracts text from a PDF URL
# ===========================================================================

class TestReadPdf:
    @pytest.mark.asyncio
    async def test_read_pdf_returns_url_and_text(self):
        from plugins.browser import BrowserPlugin
        fake_response = {"text": "This is a PDF document about Python."}
        plugin = BrowserPlugin(fetch_fn=_make_fetch(fake_response))
        result = await plugin.call_tool("read_pdf", {"url": "https://example.com/doc.pdf"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["url"] == "https://example.com/doc.pdf"
        assert "PDF document" in data["text"]

    @pytest.mark.asyncio
    async def test_read_pdf_passes_url_to_fetch(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured.update(body)
            return {"text": ""}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("read_pdf", {"url": "https://example.com/doc.pdf"})
        assert captured["url"] == "https://example.com/doc.pdf"

    @pytest.mark.asyncio
    async def test_read_pdf_posts_to_browser_pdf_endpoint(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(url, body):
            captured["endpoint"] = url
            return {"text": ""}

        plugin = BrowserPlugin(fetch_fn=_capture)
        await plugin.call_tool("read_pdf", {"url": "https://example.com/doc.pdf"})
        assert "/browser/pdf" in captured["endpoint"]

    @pytest.mark.asyncio
    async def test_read_pdf_error_on_fetch_failure(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch_error(OSError("unreachable")))
        result = await plugin.call_tool("read_pdf", {"url": "https://example.com/doc.pdf"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_read_pdf_missing_url_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({"text": ""}))
        result = await plugin.call_tool("read_pdf", {})
        assert result.is_error


# ===========================================================================
# Cycle 5 — unknown tool returns is_error
# ===========================================================================

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({}))
        result = await plugin.call_tool("nonexistent_tool", {})
        assert result.is_error


# ===========================================================================
# Cycle 6 — tool schemas are well-formed
# ===========================================================================

class TestToolSchemas:
    def test_web_search_schema_requires_query(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({}))
        tool = next(t for t in plugin.list_tools() if t.name == "web_search")
        assert "query" in tool.schema.get("required", [])

    def test_navigate_schema_requires_url(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({}))
        tool = next(t for t in plugin.list_tools() if t.name == "navigate")
        assert "url" in tool.schema.get("required", [])

    def test_read_pdf_schema_requires_url(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({}))
        tool = next(t for t in plugin.list_tools() if t.name == "read_pdf")
        assert "url" in tool.schema.get("required", [])

    def test_all_tools_have_descriptions(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(fetch_fn=_make_fetch({}))
        for tool in plugin.list_tools():
            assert tool.description, f"Tool '{tool.name}' missing description"
