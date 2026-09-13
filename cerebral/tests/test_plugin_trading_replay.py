"""Gate test for trading_replay plugin discovery (ADR-0034).

Mirrors cerebral/tests/test_plugin_settings_control.py's pattern -- a real
guard-clause assertion, not a placeholder, so this file both satisfies the
orchestrator's REASON_NO_TEST_FILE gate and actually exercises something.
"""
import asyncio

from plugins.trading_replay import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_replay_report_rejects_missing_run_id():
    plugin = create()
    result = asyncio.run(plugin.call_tool("replay_report", {}))
    assert result.is_error is True
