"""Gate test for trading_strategies plugin (ADR-0034).

Mirrors test_plugin_ipo_calendar.py -- real guard-clause assertion, not a placeholder.
"""
import asyncio

from plugins.trading_strategies import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert "fs_read" in REQUIRED_CAPABILITIES
    assert "fs_write" in REQUIRED_CAPABILITIES


def test_unknown_tool_returns_error():
    plugin = create()
    result = asyncio.run(plugin.call_tool("nonexistent_tool", {}))
    assert result.is_error is True
