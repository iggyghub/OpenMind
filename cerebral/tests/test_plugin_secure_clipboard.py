import json
import asyncio
import pytest
from plugins.secure_clipboard import create


@pytest.fixture
def plugin():
    write_calls = []
    timer_calls = []

    def mock_write(text):
        write_calls.append(text)

    def mock_timer(seconds, fn):
        timer_calls.append((seconds, fn))

    p = create(write_fn=mock_write, timer_fn=mock_timer)
    p.write_calls = write_calls
    p.timer_calls = timer_calls
    return p


def test_copy_temporarily_writes_and_schedules_clear(plugin):
    result = asyncio.run(plugin.call_tool("clipboard_copy_temporarily", {"text": "hello", "seconds": 10}))
    data = json.loads(result.content)
    assert data == {"copied": True, "clears_in_seconds": 10}
    assert plugin.write_calls == ["hello"]
    assert len(plugin.timer_calls) == 1
    assert plugin.timer_calls[0][0] == 10


def test_scheduled_clear_runs(plugin):
    asyncio.run(plugin.call_tool("clipboard_copy_temporarily", {"text": "temp", "seconds": 5}))
    assert plugin._armed is True

    seconds, fn = plugin.timer_calls[0]
    assert seconds == 5
    fn()

    assert plugin.write_calls == ["temp", ""]
    assert plugin._armed is False


def test_clear_empties_clipboard(plugin):
    result = asyncio.run(plugin.call_tool("clipboard_clear", {}))
    data = json.loads(result.content)
    assert data == {"cleared": True}
    assert plugin.write_calls == [""]


def test_clear_if_needed_only_when_armed(plugin):
    result1 = asyncio.run(plugin.call_tool("clipboard_clear_if_needed", {}))
    assert json.loads(result1.content) == {"cleared": False}

    asyncio.run(plugin.call_tool("clipboard_copy_temporarily", {"text": "temp", "seconds": 5}))
    assert plugin._armed is True

    result2 = asyncio.run(plugin.call_tool("clipboard_clear_if_needed", {}))
    assert json.loads(result2.content) == {"cleared": True}
    assert plugin._armed is False


def test_rejects_bad_seconds(plugin):
    for bad_sec in [0, 601, -1, "ten", 3.5]:
        result = asyncio.run(plugin.call_tool("clipboard_copy_temporarily", {"text": "x", "seconds": bad_sec}))
        assert json.loads(result.content).get("error") is not None
