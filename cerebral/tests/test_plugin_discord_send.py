import asyncio
import sys

from plugins.discord_send import REQUIRED_CAPABILITIES, DiscordSendPlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_send_image_to_discord_happy_path():
    plugin = DiscordSendPlugin(
        screenshot_fn=lambda: b"stub-png-bytes",
        clipboard_fn=lambda png: None,
        focus_fn=lambda window: True,
        press_fn=lambda keys: None,
        type_fn=lambda text: None,
        sleep_fn=lambda seconds: None,
    )
    result = asyncio.run(plugin.call_tool("send_image_to_discord", {"contact": "Stub"}))

    if sys.platform != "win32":
        # The tool itself is Windows-only by design -- assert the real
        # documented behavior on other platforms instead of skipping.
        assert result.is_error is True
        return

    assert result.is_error is False
    assert "Stub" in result.content
