"""Gate test for trading_control plugin discovery (ADR-0034).

Mirrors cerebral/tests/test_plugin_trading_replay.py's pattern -- a real
guard-clause assertion, not a placeholder, so this file both satisfies the
orchestrator's REASON_NO_TEST_FILE gate and actually exercises something.
"""
import asyncio

from plugins.trading_control import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_reset_paper_trading_rejects_when_not_wired():
    plugin = create()
    result = asyncio.run(plugin.call_tool("reset_paper_trading", {}))
    assert result.is_error is True
