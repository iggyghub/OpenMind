"""
Recipes plugin tests -- ADR-0035 slice J prep (#1132).

Covers:
  - plugin declares fs_delete capability, exposes recipe_run + recipe_delete
  - recipe_run overrides required_capabilities to an empty set (per-step
    re-gating already covers it -- see _replay_recipe)
  - missing/invalid recipe_id is rejected before the callback runs
  - callback not wired surfaces a clear error
  - a wired callback is invoked and its message returned
  - a ValueError from the callback surfaces as an error ToolResult
  - unknown tool name errors

Pattern modelled on cerebral/tests/test_settings_control.py.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@pytest.fixture
def recipes_plugin():
    """Fresh import of the plugin module per-test.

    The seam setters write module-level state; reloading isolates tests
    from each other regardless of ordering.
    """
    import plugins.recipes as mod
    importlib.reload(mod)
    yield mod
    mod.set_recipe_run_callback(None)
    mod.set_recipe_delete_callback(None)


def test_plugin_declares_fs_delete_capability(recipes_plugin):
    assert recipes_plugin.REQUIRED_CAPABILITIES == frozenset({"fs_delete"})


def test_plugin_exposes_both_tools(recipes_plugin):
    plugin = recipes_plugin.create()
    tools = {t.name: t for t in plugin.list_tools()}
    assert set(tools) == {"recipe_run", "recipe_delete"}
    for tool in tools.values():
        assert tool.plugin == recipes_plugin.PLUGIN_NAME
        assert tool.schema["required"] == ["recipe_id"]


def test_recipe_run_overrides_to_empty_capabilities(recipes_plugin):
    """Per-step re-gating in _replay_recipe already covers this; the outer
    dispatcher must not double-gate."""
    plugin = recipes_plugin.create()
    tools = {t.name: t for t in plugin.list_tools()}
    assert tools["recipe_run"].required_capabilities == frozenset()
    # recipe_delete falls back to the plugin-level default (no override).
    assert tools["recipe_delete"].required_capabilities is None


async def test_call_tool_missing_recipe_id_errors(recipes_plugin):
    plugin = recipes_plugin.create()
    result = await plugin.call_tool("recipe_run", {})
    assert result.is_error
    assert "recipe_id" in result.content


async def test_call_tool_invalid_recipe_id_type_errors(recipes_plugin):
    plugin = recipes_plugin.create()
    result = await plugin.call_tool("recipe_delete", {"recipe_id": "abc"})
    assert result.is_error
    assert "recipe_id" in result.content


async def test_unknown_tool_name_errors(recipes_plugin):
    plugin = recipes_plugin.create()
    result = await plugin.call_tool("clearly_not_a_tool", {"recipe_id": 1})
    assert result.is_error


async def test_recipe_run_without_wired_callback_errors(recipes_plugin):
    plugin = recipes_plugin.create()
    result = await plugin.call_tool("recipe_run", {"recipe_id": 1})
    assert result.is_error
    assert "not wired" in result.content


async def test_recipe_delete_without_wired_callback_errors(recipes_plugin):
    plugin = recipes_plugin.create()
    result = await plugin.call_tool("recipe_delete", {"recipe_id": 1})
    assert result.is_error
    assert "not wired" in result.content


async def test_recipe_run_invokes_wired_callback(recipes_plugin):
    plugin = recipes_plugin.create()
    calls: list[int] = []

    async def run(recipe_id):
        calls.append(recipe_id)
        return "Recipe completed."

    recipes_plugin.set_recipe_run_callback(run)
    result = await plugin.call_tool("recipe_run", {"recipe_id": 42})
    assert not result.is_error
    assert calls == [42]
    assert result.content == "Recipe completed."


async def test_recipe_delete_invokes_wired_callback(recipes_plugin):
    plugin = recipes_plugin.create()
    calls: list[int] = []

    async def delete(recipe_id):
        calls.append(recipe_id)
        return f"Recipe {recipe_id} deleted."

    recipes_plugin.set_recipe_delete_callback(delete)
    result = await plugin.call_tool("recipe_delete", {"recipe_id": 7})
    assert not result.is_error
    assert calls == [7]
    assert "7" in result.content


async def test_recipe_run_callback_value_error_surfaces(recipes_plugin):
    plugin = recipes_plugin.create()

    async def run(recipe_id):
        raise ValueError(f"Recipe {recipe_id} not found.")

    recipes_plugin.set_recipe_run_callback(run)
    result = await plugin.call_tool("recipe_run", {"recipe_id": 999})
    assert result.is_error
    assert "999" in result.content


async def test_recipe_delete_callback_value_error_surfaces(recipes_plugin):
    plugin = recipes_plugin.create()

    async def delete(recipe_id):
        raise ValueError(f"Recipe {recipe_id} not found.")

    recipes_plugin.set_recipe_delete_callback(delete)
    result = await plugin.call_tool("recipe_delete", {"recipe_id": 999})
    assert result.is_error
    assert "999" in result.content
