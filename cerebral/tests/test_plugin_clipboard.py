import asyncio
import json

from plugins.clipboard import REQUIRED_CAPABILITIES, ClipboardPlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_read_clipboard_happy_path():
    plugin = ClipboardPlugin(read_fn=lambda: "stub clipboard text", write_fn=lambda text: None)
    result = asyncio.run(plugin.call_tool("read_clipboard", {}))
    assert result.is_error is False
    assert "stub clipboard text" in result.content


def _fakes():
    box = {"t": ""}
    timer_calls = []
    plugin = ClipboardPlugin(
        read_fn=lambda: box["t"],
        write_fn=lambda t: box.__setitem__("t", t),
        timer_fn=lambda seconds, fn: timer_calls.append((seconds, fn)),
    )
    return plugin, box, timer_calls


async def test_write_adds_to_history():
    plugin, box, _ = _fakes()
    await plugin.call_tool("write_clipboard", {"text": "hello"})
    history = json.loads((await plugin.call_tool("list_clipboard_history", {})).content)["history"]
    assert "hello" in history
    assert box["t"] == "hello"


async def test_clear_after_clears_when_unchanged():
    plugin, box, timer_calls = _fakes()
    await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": 30})
    assert box["t"] == "secret"
    assert [c[0] for c in timer_calls] == [30]
    timer_calls[0][1]()
    assert box["t"] == ""


async def test_clear_after_keeps_newer_copy():
    plugin, box, timer_calls = _fakes()
    await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": 30})
    box["t"] = "user text"
    timer_calls[0][1]()
    assert box["t"] == "user text"


async def test_clear_after_not_in_history():
    plugin, _, _ = _fakes()
    await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": 10})
    history = json.loads((await plugin.call_tool("list_clipboard_history", {})).content)["history"]
    assert "secret" not in history


async def test_clear_after_out_of_range_is_error():
    plugin, box, timer_calls = _fakes()
    for val in [0, 601, "10", True]:
        res = await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": val})
        assert res.is_error is True
    assert box["t"] == ""
    assert timer_calls == []
