"""game_capture plugin tests -- fully hermetic (no real keys/registry/files)."""
import json
import sys
import time

import pytest

from plugins.game_capture import GameCapturePlugin, newest_new_file


# ── newest_new_file (pure) ───────────────────────────────────────────────────

def test_newest_new_file_picks_newest_unseen_with_ext():
    before = {"a.png"}
    entries = [("a.png", 100.0), ("b.png", 200.0), ("c.png", 300.0), ("note.txt", 400.0)]
    # a.png was already there; note.txt wrong ext; newest matching new = c.png
    assert newest_new_file(before, entries, {".png"}) == "c.png"


def test_newest_new_file_none_when_nothing_new():
    before = {"a.png"}
    assert newest_new_file(before, [("a.png", 100.0)], {".png"}) is None


# ── capture tools ────────────────────────────────────────────────────────────

def _plugin(tmp_path, drops_ext=None):
    """Plugin whose 'hotkey' drops a file with drops_ext into the captures dir
    (simulating Game Bar), or drops nothing when drops_ext is None."""
    def fake_press(keys):
        if drops_ext is not None:
            (tmp_path / f"Clip_{time.time_ns()}{drops_ext}").write_bytes(b"x")
    return GameCapturePlugin(
        press_hotkey_fn=fake_press, captures_dir=tmp_path,
        registry_set_fn=lambda *a: None, wait_timeout_s=2.0,
    )


async def test_game_screenshot_returns_new_image(tmp_path):
    r = await _plugin(tmp_path, drops_ext=".png").call_tool("game_screenshot", {})
    data = json.loads(r.content)
    assert data["ok"] is True and data["path"].endswith(".png")


async def test_game_clip_returns_new_video(tmp_path):
    r = await _plugin(tmp_path, drops_ext=".mp4").call_tool("game_clip", {})
    data = json.loads(r.content)
    assert data["ok"] is True and data["path"].endswith(".mp4")


async def test_capture_errors_when_no_file_appears(tmp_path):
    # Hotkey fires but nothing gets written (Game Bar off / no game focused).
    r = await _plugin(tmp_path, drops_ext=None).call_tool("game_screenshot", {})
    assert r.is_error
    assert "no new screenshot" in r.content


async def test_screenshot_ignores_wrong_extension(tmp_path):
    # A clip landing (.mp4) must not satisfy a screenshot request.
    r = await _plugin(tmp_path, drops_ext=".mp4").call_tool("game_screenshot", {})
    assert r.is_error  # no image appeared


# ── set_game_recording ───────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="set_game_recording is Windows-only")
async def test_set_game_recording_calls_registry(tmp_path):
    calls = []
    plugin = GameCapturePlugin(
        press_hotkey_fn=lambda k: None, captures_dir=tmp_path,
        registry_set_fn=lambda enabled, seconds: calls.append((enabled, seconds)),
    )
    r = await plugin.call_tool("set_game_recording", {"enabled": True, "seconds": 60})
    assert not r.is_error
    assert calls == [(True, 60)]
    assert json.loads(r.content)["seconds"] == 60
