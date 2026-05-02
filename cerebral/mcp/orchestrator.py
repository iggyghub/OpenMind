"""
MCP Orchestrator — Issue #7.

Manages the registry of MCP plugin servers, routes tool calls, and exposes a
unified tool list to the model router.

Public interface:
  orc = MCPOrchestrator()
  orc.register(plugin)                        # add a plugin
  orc.unregister("clock")                     # remove by name
  orc.discover_plugins(Path("../plugins"))    # auto-load from directory
  orc.list_tools()                            # → list[Tool]
  await orc.call_tool("get_time", {})         # → ToolResult (never raises)
  orc.tools_for_llm                           # → list[dict] for LLM tool use

Plugin convention (plugins/*.py):
  PLUGIN_NAME: str = "clock"
  def create() -> Plugin: ...
"""

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    plugin: str
    schema: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@runtime_checkable
class Plugin(Protocol):
    name: str

    def list_tools(self) -> list[Tool]: ...
    async def call_tool(self, tool_name: str, args: dict) -> ToolResult: ...


class MCPOrchestrator:
    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        # tool_name → plugin_name for fast routing
        self._tool_index: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register(self, plugin: Plugin) -> None:
        if plugin.name in self._plugins:
            logger.warning("[mcp] Plugin '%s' already registered — replacing", plugin.name)
            self._remove_from_index(plugin.name)
        self._plugins[plugin.name] = plugin
        for tool in plugin.list_tools():
            if tool.name in self._tool_index:
                existing = self._tool_index[tool.name]
                logger.warning(
                    "[mcp] Tool '%s' already registered by '%s'; '%s' takes over",
                    tool.name, existing, plugin.name,
                )
            self._tool_index[tool.name] = plugin.name
        logger.info("[mcp] Registered plugin '%s' with %d tool(s)", plugin.name, len(plugin.list_tools()))

    def unregister(self, plugin_name: str) -> None:
        if plugin_name not in self._plugins:
            logger.warning("[mcp] Tried to unregister unknown plugin '%s'", plugin_name)
            return
        self._remove_from_index(plugin_name)
        del self._plugins[plugin_name]
        logger.info("[mcp] Unregistered plugin '%s'", plugin_name)

    def _remove_from_index(self, plugin_name: str) -> None:
        to_remove = [k for k, v in self._tool_index.items() if v == plugin_name]
        for key in to_remove:
            del self._tool_index[key]

    # ------------------------------------------------------------------
    # Tool access
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for plugin in self._plugins.values():
            tools.extend(plugin.list_tools())
        return tools

    async def call_tool(self, name: str, args: dict) -> ToolResult:
        if name not in self._tool_index:
            logger.warning("[mcp] Unknown tool '%s'", name)
            return ToolResult(content=f"Unknown tool: '{name}'", is_error=True)
        plugin_name = self._tool_index[name]
        plugin = self._plugins[plugin_name]
        try:
            result = await plugin.call_tool(name, args)
        except Exception as exc:
            logger.error("[mcp] Tool '%s' raised: %s", name, exc)
            return ToolResult(content=f"Tool '{name}' error: {exc}", is_error=True)
        return result

    @property
    def tools_for_llm(self) -> list[dict]:
        """Format tools as the LLM tool-use schema (Anthropic/OpenAI compatible)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.schema or {"type": "object", "properties": {}},
            }
            for t in self.list_tools()
        ]

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    def discover_plugins(self, plugins_dir: Path) -> None:
        """Import every *.py in plugins_dir, call create(), register the result."""
        if not plugins_dir.is_dir():
            logger.warning("[mcp] plugins_dir '%s' does not exist — skipping discovery", plugins_dir)
            return
        for path in sorted(plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            self._load_plugin_file(path)

    def _load_plugin_file(self, path: Path) -> None:
        module_name = f"openmind_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning("[mcp] Could not load spec for '%s'", path)
            return
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.error("[mcp] Failed to load plugin '%s': %s", path.name, exc)
            return
        if not hasattr(module, "create"):
            logger.warning("[mcp] Plugin '%s' has no create() — skipping", path.name)
            return
        try:
            plugin = module.create()
        except Exception as exc:
            logger.error("[mcp] create() failed in '%s': %s", path.name, exc)
            return
        self.register(plugin)
