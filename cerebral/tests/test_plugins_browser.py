"""
Browser MCP plugin tests — Issue #17, rewritten 2026-08-25 for the CLI
transport fix (plugins/browser.py used to POST to a phantom
http://localhost:3000 endpoint that has never existed in any installed
OpenClaw version -- same "phantom-:3000" family issue #378 diagnosed and
fixed for the LLM backend, just never fixed here. Real fix: shell out to
`openclaw infer web search`/`fetch`).

TDD vertical slices for BrowserPlugin: web_search, navigate, read_pdf —
all CLI side-effects injected via run_cli_fn.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search_response(hits: list) -> dict:
    """The real shape `openclaw infer web search --json` returns (captured
    live 2026-08-25 against the duckduckgo provider)."""
    return {
        "ok": True, "capability": "web.search", "provider": "duckduckgo",
        "outputs": [{"result": {"results": hits}}],
    }


def _fetch_response(content: str) -> dict:
    return {
        "ok": True, "capability": "web.fetch",
        "outputs": [{"result": {"content": content}}],
    }


def _make_run_cli(response: dict):
    """Return an async run_cli_fn that always resolves to `response`."""
    async def _run(args: list) -> dict:
        return response
    return _run


def _make_run_cli_error(exc: Exception):
    """Return an async run_cli_fn that always raises `exc`."""
    async def _run(args: list) -> dict:
        raise exc
    return _run


# ===========================================================================
# Cycle 1 — plugin can be imported, instantiated, and lists 3 tools
# ===========================================================================

class TestBrowserPluginMeta:
    def test_plugin_name(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({}))
        assert plugin.name == "browser"

    def test_lists_three_tools(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({}))
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
        fake_response = _search_response(
            [{"title": "Python 3.13", "url": "https://python.org", "snippet": "Latest release"}]
        )
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli(fake_response))
        result = await plugin.call_tool("web_search", {"query": "latest python version"})
        assert not result.is_error
        data = json.loads(result.content)
        assert "results" in data
        assert data["results"][0]["title"] == "Python 3.13"
        assert data["results"][0]["url"] == "https://python.org"

    @pytest.mark.asyncio
    async def test_web_search_passes_query_as_a_real_cli_arg(self):
        """run_cli_fn must receive the query as its own argv element (not
        shell-interpolated -- no injection surface even with quotes)."""
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(args):
            captured["args"] = args
            return _search_response([])

        plugin = BrowserPlugin(run_cli_fn=_capture)
        await plugin.call_tool("web_search", {"query": 'hello "world"'})
        assert captured["args"] == [
            "infer", "web", "search", "--query", 'hello "world"', "--limit", "5", "--json",
        ]

    @pytest.mark.asyncio
    async def test_web_search_passes_max_results_as_limit(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(args):
            captured["args"] = args
            return _search_response([])

        plugin = BrowserPlugin(run_cli_fn=_capture)
        await plugin.call_tool("web_search", {"query": "q", "max_results": 3})
        assert "--limit" in captured["args"]
        assert captured["args"][captured["args"].index("--limit") + 1] == "3"

    @pytest.mark.asyncio
    async def test_web_search_default_max_results_is_5(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(args):
            captured["args"] = args
            return _search_response([])

        plugin = BrowserPlugin(run_cli_fn=_capture)
        await plugin.call_tool("web_search", {"query": "q"})
        assert captured["args"][captured["args"].index("--limit") + 1] == "5"

    @pytest.mark.asyncio
    async def test_web_search_error_on_cli_failure(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli_error(RuntimeError("no network")))
        result = await plugin.call_tool("web_search", {"query": "anything"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_web_search_missing_query_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli(_search_response([])))
        result = await plugin.call_tool("web_search", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_web_search_runs_the_real_openclaw_infer_web_search_command(self):
        """Regression: this used to POST to http://localhost:3000/browser/
        search, an endpoint that has never existed in any installed
        OpenClaw version. Confirms the real, working CLI command is used."""
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(args):
            captured["args"] = args
            return _search_response([])

        plugin = BrowserPlugin(run_cli_fn=_capture)
        await plugin.call_tool("web_search", {"query": "q"})
        assert captured["args"][:3] == ["infer", "web", "search"]

    @pytest.mark.asyncio
    async def test_web_search_no_results_key_in_cli_output_returns_empty_list_not_crash(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({"outputs": [{"result": {}}]}))
        result = await plugin.call_tool("web_search", {"query": "q"})
        assert not result.is_error
        assert json.loads(result.content)["results"] == []


# ===========================================================================
# Cycle 3 — navigate extracts readable content
# ===========================================================================

class TestNavigate:
    @pytest.mark.asyncio
    async def test_navigate_returns_url_and_content(self):
        from plugins.browser import BrowserPlugin
        fake_response = _fetch_response("Python 3.13 was released in October 2024.")
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli(fake_response))
        result = await plugin.call_tool("navigate", {"url": "https://python.org"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["url"] == "https://python.org"
        assert "Python 3.13" in data["content"]

    @pytest.mark.asyncio
    async def test_navigate_passes_url_as_a_real_cli_arg(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(args):
            captured["args"] = args
            return _fetch_response("ok")

        plugin = BrowserPlugin(run_cli_fn=_capture)
        await plugin.call_tool("navigate", {"url": "https://example.com"})
        assert captured["args"] == ["infer", "web", "fetch", "--url", "https://example.com", "--json"]

    @pytest.mark.asyncio
    async def test_navigate_error_on_cli_failure(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli_error(TimeoutError("timeout")))
        result = await plugin.call_tool("navigate", {"url": "https://example.com"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_navigate_missing_url_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli(_fetch_response("")))
        result = await plugin.call_tool("navigate", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_navigate_surfaces_openclaws_own_no_provider_error_honestly(self):
        """web.fetch's only provider (firecrawl) needs FIRECRAWL_API_KEY,
        unconfigured by default -- navigate must report that real error,
        not silently succeed with empty content."""
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli_error(
            RuntimeError("web.fetch is disabled or no provider is available.")
        ))
        result = await plugin.call_tool("navigate", {"url": "https://example.com"})
        assert result.is_error
        assert "no provider" in result.content


# ===========================================================================
# Cycle 4 — read_pdf extracts text from a PDF URL
# ===========================================================================

class TestReadPdf:
    @pytest.mark.asyncio
    async def test_read_pdf_returns_url_and_text(self):
        from plugins.browser import BrowserPlugin
        fake_response = {"outputs": [{"result": {"text": "This is a PDF document about Python."}}]}
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli(fake_response))
        result = await plugin.call_tool("read_pdf", {"url": "https://example.com/doc.pdf"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["url"] == "https://example.com/doc.pdf"
        assert "PDF document" in data["text"]

    @pytest.mark.asyncio
    async def test_read_pdf_passes_url_as_a_real_cli_arg(self):
        from plugins.browser import BrowserPlugin
        captured = {}

        async def _capture(args):
            captured["args"] = args
            return {"outputs": [{"result": {"text": ""}}]}

        plugin = BrowserPlugin(run_cli_fn=_capture)
        await plugin.call_tool("read_pdf", {"url": "https://example.com/doc.pdf"})
        assert "--url" in captured["args"]
        assert captured["args"][captured["args"].index("--url") + 1] == "https://example.com/doc.pdf"

    @pytest.mark.asyncio
    async def test_read_pdf_error_on_cli_failure(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli_error(OSError("unreachable")))
        result = await plugin.call_tool("read_pdf", {"url": "https://example.com/doc.pdf"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_read_pdf_missing_url_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({"outputs": [{"result": {"text": ""}}]}))
        result = await plugin.call_tool("read_pdf", {})
        assert result.is_error


# ===========================================================================
# Cycle 5 — unknown tool returns is_error
# ===========================================================================

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({}))
        result = await plugin.call_tool("nonexistent_tool", {})
        assert result.is_error


# ===========================================================================
# Cycle 6 — tool schemas are well-formed
# ===========================================================================

class TestToolSchemas:
    def test_web_search_schema_requires_query(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({}))
        tool = next(t for t in plugin.list_tools() if t.name == "web_search")
        assert "query" in tool.schema.get("required", [])

    def test_navigate_schema_requires_url(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({}))
        tool = next(t for t in plugin.list_tools() if t.name == "navigate")
        assert "url" in tool.schema.get("required", [])

    def test_read_pdf_schema_requires_url(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({}))
        tool = next(t for t in plugin.list_tools() if t.name == "read_pdf")
        assert "url" in tool.schema.get("required", [])

    def test_all_tools_have_descriptions(self):
        from plugins.browser import BrowserPlugin
        plugin = BrowserPlugin(run_cli_fn=_make_run_cli({}))
        for tool in plugin.list_tools():
            assert tool.description, f"Tool '{tool.name}' missing description"


# ===========================================================================
# Cycle 7 — the real default run_cli_fn actually invokes openclaw
# ===========================================================================

class TestDefaultRunCli:
    @pytest.mark.asyncio
    async def test_default_run_cli_invokes_real_openclaw_web_search(self):
        """Live integration check, no mocking: the default transport must
        actually be able to run `openclaw infer web search` on this host
        (Windows: openclaw is an npm .cmd shim -- create_subprocess_exec
        can't resolve it directly, confirmed empirically, hence cmd /c)."""
        import shutil
        if shutil.which("openclaw") is None:
            pytest.skip("openclaw CLI not installed on this host")
        from plugins.browser import _default_run_cli
        data = await _default_run_cli(["infer", "web", "search", "--query", "python", "--limit", "1", "--json"])
        assert data.get("ok") is True
        assert data["capability"] == "web.search"
