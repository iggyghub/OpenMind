import asyncio

from plugins.clipboard import REQUIRED_CAPABILITIES, ClipboardPlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_read_clipboard_happy_path():
    plugin = ClipboardPlugin(read_fn=lambda: "stub clipboard text", write_fn=lambda text: None)
    result = asyncio.run(plugin.call_tool("read_clipboard", {}))
    assert result.is_error is False
    assert "stub clipboard text" in result.content
