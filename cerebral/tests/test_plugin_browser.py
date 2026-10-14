import asyncio

from plugins.browser import REQUIRED_CAPABILITIES, BrowserPlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_web_search_happy_path():
    async def fake_run_cli(args):
        return {"outputs": [{"result": {"results": [
            {"title": "Stub result", "url": "https://example.com", "snippet": "stub"},
        ]}}]}

    plugin = BrowserPlugin(run_cli_fn=fake_run_cli)
    result = asyncio.run(plugin.call_tool("web_search", {"query": "test"}))
    assert result.is_error is False
    assert "Stub result" in result.content
