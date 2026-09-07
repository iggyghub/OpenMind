import asyncio

from plugins.settings_control import REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_set_system_setting_rejects_unknown_key():
    # Real, deterministic guard-clause path -- exercises the actual
    # ALLOWED_KEYS check without needing to know a real writable key/value.
    plugin = create()
    result = asyncio.run(
        plugin.call_tool("set_system_setting", {"key": "not-a-real-key", "value": 1})
    )
    assert result.is_error is True
