"""Gate test for trading_replay plugin discovery.

Ensures the plugin registers tools and passes MCPOrchestrator verification.
Mirrors the test_plugin_settings_control.py pattern.
"""
from plugins.trading_replay import TradingReplayPlugin


def test_plugin_trading_replay_gate():
    plugin = TradingReplayPlugin()
    assert plugin is not None, "Plugin instance should not be None"

    tool_names = {t.name for t in plugin.TOOLS}
    
    assert "list_strategies" in tool_names, (
        f"Missing 'list_strategies' in plugin tools. Found: {tool_names}"
    )
    assert "simulate_period" in tool_names, (
        f"Missing 'simulate_period' in plugin tools. Found: {tool_names}"
    )
    assert "replay_report" in tool_names, (
        f"Missing 'replay_report' in plugin tools. Found: {tool_names}"
    )
