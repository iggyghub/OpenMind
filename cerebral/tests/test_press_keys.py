"""press_keys (keyboard-nav path) + copy_image_to_clipboard tests."""
import io
import json
import sys

import pytest

from plugins.computer_use import ComputerUsePlugin, _parse_key_chord


class _KeyBackend:
    """Minimal fake actuation backend for the press_keys path."""
    def __init__(self, idle_ms: int = 5000):
        self._idle_ms = idle_ms
        self.hotkeys: list[list[str]] = []
        self.keys: list[str] = []
        self.surfaced: list[str] = []

    def last_input_ms(self) -> int:
        return self._idle_ms

    def surface_window(self, window_title: str) -> None:
        self.surfaced.append(window_title)

    def press_key(self, key: str) -> None:
        self.keys.append(key)

    def press_hotkey(self, keys: list[str]) -> None:
        self.hotkeys.append(list(keys))


def _plugin(backend):
    return ComputerUsePlugin(
        backend=backend,
        user_idle_ms_fn=lambda: 3000,
        full_autonomy_fn=lambda: False,
    )


# ── _parse_key_chord ─────────────────────────────────────────────────────────

def test_parse_chord_variants():
    assert _parse_key_chord("ctrl+k") == ["ctrl", "k"]
    assert _parse_key_chord("Ctrl+Shift+P") == ["ctrl", "shift", "p"]
    assert _parse_key_chord("enter") == ["enter"]
    assert _parse_key_chord(["ctrl", "v"]) == ["ctrl", "v"]
    assert _parse_key_chord("cmd+return") == ["win", "enter"]  # aliases
    assert _parse_key_chord("") == []


# ── press_keys idle gate ─────────────────────────────────────────────────────

async def test_press_keys_fires_when_idle():
    backend = _KeyBackend(idle_ms=5000)  # well past the 3000ms threshold
    r = await _plugin(backend).call_tool(
        "press_keys", {"keys": "ctrl+k", "window_title": "Discord"},
    )
    assert not r.is_error
    assert backend.hotkeys == [["ctrl", "k"]]
    assert backend.surfaced == ["Discord"]


async def test_press_keys_blocked_when_user_present():
    backend = _KeyBackend(idle_ms=400)  # user just touched the keyboard
    r = await _plugin(backend).call_tool("press_keys", {"keys": "ctrl+v"})
    assert r.is_error
    assert "Waiting" in r.content
    assert backend.hotkeys == []       # never fired


async def test_press_keys_single_key_uses_press_key():
    backend = _KeyBackend(idle_ms=5000)
    await _plugin(backend).call_tool("press_keys", {"keys": "enter"})
    assert backend.keys == ["enter"]
    assert backend.hotkeys == []


async def test_press_keys_rejects_empty():
    backend = _KeyBackend(idle_ms=5000)
    r = await _plugin(backend).call_tool("press_keys", {"keys": ""})
    assert r.is_error


# ── copy_image_to_clipboard ──────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard only")
def test_copy_image_to_clipboard_puts_dib(tmp_path):
    from PIL import Image
    from plugins.system import SystemPlugin
    p = tmp_path / "shot.png"
    Image.new("RGB", (32, 16), (10, 20, 30)).save(p)

    res = SystemPlugin()._copy_image_to_clipboard({"path": str(p)})
    assert json.loads(res.content)["ok"] is True

    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        assert win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB)
    finally:
        win32clipboard.CloseClipboard()
