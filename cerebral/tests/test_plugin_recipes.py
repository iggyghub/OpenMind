import asyncio

from plugins.recipes import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_recipe_run_rejects_invalid_recipe_id():
    # Real, deterministic guard-clause path -- exercises the actual
    # recipe_id type check without needing a wired run/delete callback.
    plugin = create()
    result = asyncio.run(plugin.call_tool("recipe_run", {"recipe_id": "not-an-int"}))
    assert result.is_error is True
