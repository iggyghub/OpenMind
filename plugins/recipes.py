"""
Recipes plugin -- ADR-0035 slice J prep (recipe_run / recipe_delete tools).

Exposes ``recipe_run(recipe_id)`` and ``recipe_delete(recipe_id)`` as
declared MCP tools instead of the tray's internal ``run_recipe`` /
``delete_recipe`` WebSocket events (ADR-0031: "a button is a declared tool
call"). This lets the Recipes tab (slice J) drive both actions through
``ps-action`` like every other registry-widget row.

ADR-0005 gating
----------------
The plugin declares ``REQUIRED_CAPABILITIES = {"fs_delete"}`` -- deleting a
saved Recipe destroys user data with no finer-grained re-check available,
so FS_DELETE (ASK-class) is the honest claim. ``recipe_run`` overrides this
per-Tool down to an empty capability set: every step it replays is already
individually re-gated against its own plugin's capabilities inside
``_replay_recipe`` (ADR-0005 amendment), so an outer gate on the dispatcher
itself would only duplicate that check -- exactly how the WS event behaved.

Wiring seam
-----------
``set_recipe_run_callback(fn)`` / ``set_recipe_delete_callback(fn)`` are the
module-level seams ``cerebral.main`` injects via ``_wire_plugin_seams``
(Issue #153: must target the orchestrator-loaded module instance, not a
second one from ``import plugins.X``). Each injected callback is an async
function ``(recipe_id: int) -> str`` that performs the actual work and
returns a summary message, raising ``ValueError`` on failure -- the same
``_apply_fn`` contract ``settings_control`` uses.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "recipes"

# FS_DELETE: ASK-class in DEFAULT_POLICY -- recipe_delete destroys a saved
# Recipe. recipe_run overrides this per-Tool (see list_tools) since every
# step it replays is already individually re-gated.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_delete"})


# Module-level seams injected by cerebral.main._wire_plugin_seams.
# Shape: async fn(recipe_id: int) -> str; raises ValueError on failure.
RecipeActionFn = Callable[[int], Awaitable[str]]
_run_fn: Optional[RecipeActionFn] = None
_delete_fn: Optional[RecipeActionFn] = None


def set_recipe_run_callback(fn: Optional[RecipeActionFn]) -> None:
    """Inject the (async) recipe-run callback from cerebral.main."""
    global _run_fn
    _run_fn = fn


def set_recipe_delete_callback(fn: Optional[RecipeActionFn]) -> None:
    """Inject the (async) recipe-delete callback from cerebral.main."""
    global _delete_fn
    _delete_fn = fn


_RECIPE_ID_SCHEMA = {
    "type": "object",
    "properties": {
        "recipe_id": {"type": "integer", "description": "The Recipe's id."},
    },
    "required": ["recipe_id"],
}


class RecipesPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="recipe_run",
                description="Run a saved Recipe by id, replaying its steps.",
                plugin=PLUGIN_NAME,
                schema=_RECIPE_ID_SCHEMA,
                # Each step is individually re-gated inside the run callback
                # (ADR-0005 amendment) -- no outer gate needed here.
                required_capabilities=frozenset(),
            ),
            Tool(
                name="recipe_delete",
                description="Permanently delete a saved Recipe by id.",
                plugin=PLUGIN_NAME,
                schema=_RECIPE_ID_SCHEMA,
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "recipe_run":
            fn = _run_fn
        elif tool_name == "recipe_delete":
            fn = _delete_fn
        else:
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

        recipe_id = args.get("recipe_id")
        if not isinstance(recipe_id, int):
            return ToolResult(
                content=f"Missing or invalid 'recipe_id': {recipe_id!r}", is_error=True,
            )
        if fn is None:
            logger.warning(
                "[recipes] %s callback not wired; cannot act on recipe %r",
                tool_name, recipe_id,
            )
            return ToolResult(
                content="Recipes are not wired in this process.", is_error=True,
            )
        try:
            message = await fn(recipe_id)
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:  # pragma: no cover -- defensive
            logger.exception("[recipes] %s failed for recipe %r", tool_name, recipe_id)
            return ToolResult(
                content=f"Could not run {tool_name} on recipe {recipe_id}: {exc}",
                is_error=True,
            )
        return ToolResult(content=message)


def create() -> RecipesPlugin:
    return RecipesPlugin()
