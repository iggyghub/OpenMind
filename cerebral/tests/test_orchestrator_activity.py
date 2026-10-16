"""Tests for MCPOrchestrator activity tracking (Issue #1510)."""

from unittest.mock import MagicMock, AsyncMock

import pytest

from cerebral.mcp.orchestrator import MCPOrchestrator, Tool, ToolResult, GATE_EXEMPT
from cerebral.security import Decision, Capability


def _make_plugin(name: str, tool_name: str | None = None) -> MagicMock:
    """Helper to create a mock plugin, mirroring test_orchestrator.py."""
    plugin = MagicMock()
    plugin.name = name
    if tool_name:
        plugin.list_tools.return_value = [Tool(name=tool_name, description="test tool", plugin=name)]
    else:
        plugin.list_tools.return_value = []
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="ok"))
    return plugin


@pytest.mark.asyncio
async def test_activity_reports_start_and_end():
    orc = MCPOrchestrator()
    plugin = _make_plugin("p1", "t")
    orc.register(plugin)
    
    events = []
    def cb(count, tool):
        events.append((count, tool))
    orc.set_on_activity(cb)
    
    await orc.call_tool("t", {}, capability=GATE_EXEMPT)
    
    assert events == [(1, "t"), (0, None)]


@pytest.mark.asyncio
async def test_activity_reports_count_while_running():
    orc = MCPOrchestrator()
    plugin = _make_plugin("p1", "t")
    orc.register(plugin)
    events = []
    def cb(count, tool):
        events.append((count, tool))
    orc.set_on_activity(cb)
    
    async def side_effect(*args, **kwargs):
        # At this point, _notify_activity(1, "t") has already run synchronously.
        assert events[-1] == (1, "t")
        return ToolResult(content="ok")
    plugin.call_tool.side_effect = side_effect
    
    await orc.call_tool("t", {}, capability=GATE_EXEMPT)
    assert events == [(1, "t"), (0, None)]


@pytest.mark.asyncio
async def test_activity_ends_when_tool_raises():
    orc = MCPOrchestrator()
    plugin = _make_plugin("p1", "t")
    plugin.call_tool.side_effect = RuntimeError("boom")
    orc.register(plugin)
    
    events = []
    def cb(count, tool):
        events.append((count, tool))
    orc.set_on_activity(cb)
    
    result = await orc.call_tool("t", {}, capability=GATE_EXEMPT)
    
    assert result.is_error is True
    assert events[-1] == (0, None)


@pytest.mark.asyncio
async def test_activity_not_reported_for_unknown_tool():
    orc = MCPOrchestrator()
    # Don't register any plugin
    
    events = []
    def cb(count, tool):
        events.append((count, tool))
    orc.set_on_activity(cb)
    
    await orc.call_tool("unknown", {}, capability=GATE_EXEMPT)
    
    assert events == []


@pytest.mark.asyncio
async def test_activity_not_reported_when_gate_denies():
    gate = MagicMock()
    gate.check.return_value = Decision.DENY
    orc = MCPOrchestrator(gate=gate)
    plugin = _make_plugin("p1", "t")
    orc.register(plugin)
    
    events = []
    def cb(count, tool):
        events.append((count, tool))
    orc.set_on_activity(cb)
    
    result = await orc.call_tool("t", {}, capability=Capability.FS_READ)
    
    assert result.is_error is True
    assert events == []


@pytest.mark.asyncio
async def test_callback_error_does_not_break_call():
    orc = MCPOrchestrator()
    plugin = _make_plugin("p1", "t")
    orc.register(plugin)
    
    def bad_cb(count, tool):
        raise ValueError("callback broke")
    orc.set_on_activity(bad_cb)
    
    result = await orc.call_tool("t", {}, capability=GATE_EXEMPT)
    
    assert result.content == "ok"
    assert result.is_error is False
