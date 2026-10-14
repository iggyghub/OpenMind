import asyncio

from plugins.openclaw_channels import REQUIRED_CAPABILITIES, OpenClawChannelsPlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_conversations_list_without_token_provider():
    # No token_provider/session_factory wired -- _ensure_session's real,
    # deterministic "not wired" path. Exercises the real code without a
    # live openclaw gateway session.
    plugin = OpenClawChannelsPlugin()
    result = asyncio.run(plugin.call_tool("openclaw_conversations_list", {}))
    assert result.is_error is True
