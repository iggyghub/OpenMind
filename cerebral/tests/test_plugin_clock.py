import asyncio

from plugins.clock import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_get_time_happy_path():
    plugin = create()
    result = asyncio.run(plugin.call_tool("get_time", {}))
    assert result.is_error is False
