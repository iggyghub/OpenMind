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


    import json


    async def test_write_adds_to_history():
    box = {"t": ""}
    plugin = ClipboardPlugin(
    read_fn=lambda: box["t"],
    write_fn=lambda t: box.__setitem__("t", t),
    )
    await plugin.call_tool("write_clipboard", {"text": "hello"})
    history_result = await plugin.call_tool("list_clipboard_history", {})
    history = json.loads(history_result.content)["history"]
    assert "hello" in history


    async def test_clear_after_clears_when_unchanged():
    box = {"t": ""}
    timer_calls = []
    def fake_timer(seconds, fn):
    timer_calls.append((seconds, fn))
    plugin = ClipboardPlugin(
    read_fn=lambda: box["t"],
    write_fn=lambda t: box.__setitem__("t", t),
    timer_fn=fake_timer,
    )
    await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": 30})
    assert len(timer_calls) == 1
    assert timer_calls[0][0] == 30
    box["t"] = "secret"
    timer_calls[0][1]()
    assert box["t"] == ""


    async def test_clear_after_keeps_newer_copy():
    box = {"t": ""}
    timer_calls = []
    def fake_timer(seconds, fn):
    timer_calls.append((seconds, fn))
    plugin = ClipboardPlugin(
    read_fn=lambda: box["t"],
    write_fn=lambda t: box.__setitem__("t", t),
    timer_fn=fake_timer,
    )
    await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": 30})
    box["t"] = "user text"
    timer_calls[0][1]()
    assert box["t"] == "user text"


    async def test_clear_after_not_in_history():
    box = {"t": ""}
    timer_calls = []
    def fake_timer(seconds, fn):
    timer_calls.append((seconds, fn))
    plugin = ClipboardPlugin(
    read_fn=lambda: box["t"],
    write_fn=lambda t: box.__setitem__("t", t),
    timer_fn=fake_timer,
    )
    await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": 10})
    history_result = await plugin.call_tool("list_clipboard_history", {})
    history = json.loads(history_result.content)["history"]
    assert "secret" not in history


    async def test_clear_after_out_of_range_is_error():
    box = {"t": ""}
    timer_calls = []
    def fake_timer(seconds, fn):
    timer_calls.append((seconds, fn))
    plugin = ClipboardPlugin(
    read_fn=lambda: box["t"],
    write_fn=lambda t: box.__setitem__("t", t),
    timer_fn=fake_timer,
    )
    for val in [0, 601, "10", True]:
    res = await plugin.call_tool("write_clipboard", {"text": "secret", "clear_after_seconds": val})
    assert res.is_error is True
    assert box["t"] == ""
    assert len(timer_calls) == 0
