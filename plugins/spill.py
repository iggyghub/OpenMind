"""Spill retrieval plugin (harness parity H5-S1 / #736).

One tool -- retrieve_spilled(locator) -- pulls back the full text of a tool
result that ChainEngine spilled to the SpillStore (cerebral/llm/spill_store.py)
because it was too large for the model context. The model calls this on demand
when it actually needs the full payload behind a locator hint.

Read-only over Felix's own spill store: no shell-out, no network, and all SQLite
lives inside SpillStore (not this body), so the plugin declares
REQUIRED_CAPABILITIES = frozenset() -- the memory.py posture (ADR-0005).
"""

from __future__ import annotations

from cerebral.llm.spill_store import SpillStore
from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "spill"

# Empty: the plugin body contains no AST-tracked primitives -- the SQLite calls
# live inside SpillStore, mirroring plugins/memory.py (storage in MemoryManager).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset()


class SpillPlugin:
    name = PLUGIN_NAME

    def __init__(self, store: "SpillStore | None" = None) -> None:
        # Lazy: constructing the plugin must not open the DB (memory.py rule 3).
        self._store = store

    def _get_store(self) -> SpillStore:
        if self._store is None:
            self._store = SpillStore()
        return self._store

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="retrieve_spilled",
                description=(
                    "Retrieve the full text of a tool result that was spilled to "
                    "the store because it was too large for the context window. "
                    "Pass the locator (e.g. 'spill:ab12cd34ef56') from the hint "
                    "that replaced the oversized output."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "locator": {
                            "type": "string",
                            "description": "The spill locator, e.g. 'spill:ab12cd34ef56'.",
                        },
                    },
                    "required": ["locator"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "retrieve_spilled":
            locator = ((args or {}).get("locator") or "").strip()
            if not locator:
                return ToolResult(content="locator is required", is_error=True)
            content = self._get_store().retrieve(locator)
            if content is None:
                return ToolResult(content=f"Unknown spill locator: {locator!r}", is_error=True)
            return ToolResult(content=content)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)


def create(**kwargs) -> SpillPlugin:
    return SpillPlugin()
