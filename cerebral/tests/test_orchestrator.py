"""
MCP orchestrator tests — Issue #7.

Unit tests use injected fake plugins (no subprocess, no real MCP servers).
"""
import sys
import textwrap
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cerebral.mcp.orchestrator import MCPOrchestrator, Tool, ToolResult


# ---------------------------------------------------------------------------
# Helpers — fake plugins
# ---------------------------------------------------------------------------

def _make_plugin(name: str, tools: list[str]) -> MagicMock:
    """Return a fake Plugin with the given tool names."""
    plugin = MagicMock()
    plugin.name = name
    plugin.list_tools.return_value = [
        Tool(name=t, description=f"{t} description", plugin=name, schema={})
        for t in tools
    ]
    plugin.call_tool = AsyncMock(return_value=ToolResult(content="ok"))
    return plugin


# ---------------------------------------------------------------------------
# Slice 1 — list_tools() returns tools from a registered plugin
# ---------------------------------------------------------------------------

def test_list_tools_returns_registered_plugin_tools():
    orc = MCPOrchestrator()
    clock = _make_plugin("clock", ["get_time", "set_alarm"])
    orc.register(clock)
    names = [t.name for t in orc.list_tools()]
    assert "get_time" in names
    assert "set_alarm" in names


def test_list_tools_empty_when_no_plugins():
    orc = MCPOrchestrator()
    assert orc.list_tools() == []


def test_list_tools_aggregates_across_plugins():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    orc.register(_make_plugin("browser", ["open_url"]))
    names = [t.name for t in orc.list_tools()]
    assert "get_time" in names
    assert "open_url" in names


# ---------------------------------------------------------------------------
# Slice 2 — call_tool() routes to the correct plugin
# ---------------------------------------------------------------------------

async def test_call_tool_routes_to_correct_plugin():
    orc = MCPOrchestrator()
    clock = _make_plugin("clock", ["get_time"])
    browser = _make_plugin("browser", ["open_url"])
    orc.register(clock)
    orc.register(browser)

    await orc.call_tool("get_time", {})

    clock.call_tool.assert_called_once_with("get_time", {})
    browser.call_tool.assert_not_called()


async def test_call_tool_passes_args_through():
    orc = MCPOrchestrator()
    clock = _make_plugin("clock", ["set_alarm"])
    orc.register(clock)

    await orc.call_tool("set_alarm", {"time": "07:00"})

    clock.call_tool.assert_called_once_with("set_alarm", {"time": "07:00"})


async def test_call_tool_returns_plugin_result():
    orc = MCPOrchestrator()
    plugin = _make_plugin("clock", ["get_time"])
    plugin.call_tool.return_value = ToolResult(content="12:00")
    orc.register(plugin)

    result = await orc.call_tool("get_time", {})

    assert result.content == "12:00"
    assert not result.is_error


# ---------------------------------------------------------------------------
# Slice 3 — unknown tool → structured ToolResult error, no crash
# ---------------------------------------------------------------------------

async def test_unknown_tool_returns_error_result():
    orc = MCPOrchestrator()
    result = await orc.call_tool("does_not_exist", {})
    assert result.is_error
    assert "does_not_exist" in result.content


async def test_unknown_tool_does_not_raise():
    orc = MCPOrchestrator()
    try:
        result = await orc.call_tool("ghost_tool", {"x": 1})
    except Exception as exc:
        pytest.fail(f"call_tool raised unexpectedly: {exc}")
    assert result.is_error


async def test_plugin_exception_returns_error_result():
    orc = MCPOrchestrator()
    broken = _make_plugin("broken", ["bad_tool"])
    broken.call_tool.side_effect = RuntimeError("plugin exploded")
    orc.register(broken)

    result = await orc.call_tool("bad_tool", {})

    assert result.is_error
    assert "bad_tool" in result.content


# ---------------------------------------------------------------------------
# Slice 4 — unregister() removes that plugin's tools
# ---------------------------------------------------------------------------

def test_unregister_removes_tools_from_list():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    orc.register(_make_plugin("browser", ["open_url"]))
    orc.unregister("clock")
    names = [t.name for t in orc.list_tools()]
    assert "get_time" not in names
    assert "open_url" in names


async def test_unregister_prevents_routing():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    orc.unregister("clock")
    result = await orc.call_tool("get_time", {})
    assert result.is_error


def test_unregister_unknown_plugin_does_not_raise():
    orc = MCPOrchestrator()
    try:
        orc.unregister("nonexistent")
    except Exception as exc:
        pytest.fail(f"unregister raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Slice 5 — runtime registration makes tools immediately available
# ---------------------------------------------------------------------------

async def test_runtime_registration_tools_immediately_available():
    orc = MCPOrchestrator()
    # No plugins registered yet
    assert orc.list_tools() == []

    plugin = _make_plugin("notes", ["save_note"])
    orc.register(plugin)

    # Tools available without restart
    names = [t.name for t in orc.list_tools()]
    assert "save_note" in names

    result = await orc.call_tool("save_note", {"text": "hello"})
    assert not result.is_error


# ---------------------------------------------------------------------------
# Slice 6 — discover_plugins() auto-loads from directory
# ---------------------------------------------------------------------------

def test_discover_plugins_loads_valid_plugin(tmp_path):
    plugin_code = textwrap.dedent("""
        from cerebral.mcp.orchestrator import Tool, ToolResult

        PLUGIN_NAME = "test_clock"

        class _ClockPlugin:
            name = "test_clock"
            def list_tools(self):
                return [Tool(name="tick", description="tick-tock", plugin="test_clock")]
            async def call_tool(self, tool_name, args):
                return ToolResult(content="tock")

        def create():
            return _ClockPlugin()
    """)
    (tmp_path / "test_clock.py").write_text(plugin_code)

    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)

    names = [t.name for t in orc.list_tools()]
    assert "tick" in names


def test_discover_plugins_skips_files_without_create(tmp_path):
    (tmp_path / "not_a_plugin.py").write_text("x = 1\n")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.list_tools() == []


def test_discover_plugins_skips_private_files(tmp_path):
    (tmp_path / "__helper.py").write_text("def create(): pass\n")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    assert orc.list_tools() == []


def test_discover_plugins_nonexistent_dir_does_not_raise():
    orc = MCPOrchestrator()
    try:
        orc.discover_plugins(Path("/no/such/path"))
    except Exception as exc:
        pytest.fail(f"discover_plugins raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Slice 7 — tools_for_llm formats tool list for model router
# ---------------------------------------------------------------------------

def test_tools_for_llm_returns_list_of_dicts():
    orc = MCPOrchestrator()
    orc.register(_make_plugin("clock", ["get_time"]))
    result = orc.tools_for_llm
    assert isinstance(result, list)
    assert len(result) == 1


def test_tools_for_llm_has_required_fields():
    orc = MCPOrchestrator()
    plugin = _make_plugin("clock", ["get_time"])
    plugin.list_tools.return_value = [
        Tool(
            name="get_time",
            description="Returns current time",
            plugin="clock",
            schema={"type": "object", "properties": {"timezone": {"type": "string"}}},
        )
    ]
    orc.register(plugin)
    tool = orc.tools_for_llm[0]
    assert tool["name"] == "get_time"
    assert "description" in tool
    assert "input_schema" in tool


def test_tools_for_llm_empty_when_no_plugins():
    orc = MCPOrchestrator()
    assert orc.tools_for_llm == []
