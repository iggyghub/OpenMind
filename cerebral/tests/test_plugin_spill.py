import asyncio

from plugins.spill import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    # spill deliberately declares an empty frozenset (the memory.py posture,
    # ADR-0005) -- assert the type and the deliberate emptiness, not "non-empty".
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert REQUIRED_CAPABILITIES == frozenset()


def test_retrieve_unknown_locator():
    # Real, deterministic path: an unknown locator is a clean is_error result,
    # not a crash -- exercises _get_store() without needing a real spilled value.
    plugin = create()
    result = asyncio.run(plugin.call_tool("retrieve_spilled", {"locator": "does-not-exist"}))
    assert result.is_error is True
