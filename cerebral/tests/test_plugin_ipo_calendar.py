"""Gate test for ipo_calendar plugin (ADR-0034).

Mirrors cerebral/tests/test_plugin_discovery.py's pattern -- a real
guard-clause assertion, not a placeholder, so this file both satisfies the
orchestrator's REASON_NO_TEST_FILE gate and actually exercises something.
"""
import asyncio

from plugins.ipo_calendar import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert "fs_write" in REQUIRED_CAPABILITIES


def test_unknown_tool_returns_error():
    plugin = create()
    result = asyncio.run(plugin.call_tool("nonexistent_tool", {}))
    assert result.is_error is True
