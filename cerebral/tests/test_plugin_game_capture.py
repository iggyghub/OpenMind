import asyncio
import sys

from plugins.game_capture import REQUIRED_CAPABILITIES, GameCapturePlugin


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_set_game_recording_happy_path(tmp_path):
    seen = {}

    plugin = GameCapturePlugin(
        press_hotkey_fn=lambda keys: None,
        captures_dir=tmp_path,
        registry_set_fn=lambda enabled, seconds: seen.update(enabled=enabled, seconds=seconds),
    )
    result = asyncio.run(
        plugin.call_tool("set_game_recording", {"enabled": True, "seconds": 30})
    )

    if sys.platform != "win32":
        assert result.is_error is True
        return

    assert result.is_error is False
    assert seen == {"enabled": True, "seconds": 30}
