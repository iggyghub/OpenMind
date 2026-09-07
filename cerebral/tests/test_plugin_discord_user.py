import asyncio

from plugins.discord_user import REQUIRED_CAPABILITIES, DiscordUserPlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_discord_list_guilds_without_token_provider():
    # No token_provider wired -- _resolve_token's real, deterministic
    # "not configured" path. Exercises the real code without needing a
    # live Discord session.
    plugin = DiscordUserPlugin()
    result = asyncio.run(plugin.call_tool("discord_list_guilds", {}))
    assert result.is_error is True
