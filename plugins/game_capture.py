"""
Game capture plugin -- screenshots and instant-replay clips of games.

Games (especially exclusive-fullscreen) are opaque to the GDI screenshot and
to UIA. So instead of building a capture engine, this drives Windows' built-in
Xbox Game Bar via its global hotkeys and returns the file it drops in the
Captures folder:

  game_screenshot     -> Win+Alt+PrtScn  -> newest image in Captures
  game_clip           -> Win+Alt+G       -> newest video in Captures (the
                                            background "record the last N sec"
                                            buffer -- must be enabled first)
  set_game_recording  -> toggle the background-recording buffer + its length

Game Bar is OS-level (no vendor app to keep running, survives a GPU swap) and
the hotkey goes to the FOCUSED game -- no focus-stealing, works in fullscreen.

Seams (press_hotkey_fn / captures_dir / registry_set_fn) keep it hermetic in
tests: no real keystrokes, no real registry, no real filesystem needed.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Callable, Optional

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "game_capture"

# Hotkeys send OS input + the buffer toggle writes a system setting
# (device_control); reading the produced capture file is fs_read.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"device_control", "fs_read"})

_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv"}

# Game Bar hotkeys (Windows default).
_SCREENSHOT_CHORD = ["win", "alt", "printscreen"]
_CLIP_CHORD = ["win", "alt", "g"]


def _default_captures_dir() -> Path:
    return Path.home() / "Videos" / "Captures"


def _default_press_hotkey(keys: list[str]) -> None:
    import pyautogui  # local import: absent on non-Windows / headless CI
    pyautogui.hotkey(*keys)


def _default_registry_set(enabled: bool, seconds: int) -> None:
    """Enable/disable Game Bar background recording + set the buffer length.
    Windows-only; winreg import fails elsewhere so callers guard by platform."""
    import winreg
    key = winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
    )
    try:
        winreg.SetValueEx(key, "HistoricalCaptureEnabled", 0, winreg.REG_DWORD, 1 if enabled else 0)
        winreg.SetValueEx(key, "HistoricalBufferLengthUnit", 0, winreg.REG_DWORD, 0)  # 0 = seconds
        winreg.SetValueEx(key, "HistoricalBufferLength", 0, winreg.REG_DWORD, int(seconds))
    finally:
        winreg.CloseKey(key)


def newest_new_file(
    before: "set[str]", entries: "list[tuple[str, float]]", exts: "set[str]",
) -> Optional[str]:
    """Pure picker: of the (path, mtime) entries whose suffix is in exts and
    whose path was NOT in ``before``, return the most recently modified path,
    or None. Factored out so the poll loop's decision is unit-testable."""
    best: Optional[str] = None
    best_mtime = -1.0
    for path, mtime in entries:
        if path in before:
            continue
        if Path(path).suffix.lower() not in exts:
            continue
        if mtime > best_mtime:
            best, best_mtime = path, mtime
    return best


class GameCapturePlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        *,
        press_hotkey_fn: Callable[[list[str]], None] | None = None,
        captures_dir: Path | None = None,
        registry_set_fn: Callable[[bool, int], None] | None = None,
        wait_timeout_s: float = 6.0,
    ) -> None:
        self._press = press_hotkey_fn or _default_press_hotkey
        self._captures = captures_dir or _default_captures_dir()
        self._registry_set = registry_set_fn or _default_registry_set
        self._timeout = wait_timeout_s

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="game_screenshot",
                description=(
                    "Screenshot the focused game via Xbox Game Bar (works in "
                    "fullscreen, unlike take_screenshot). Returns the saved image "
                    "path so it can be sent (e.g. pasted into Discord)."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="game_clip",
                description=(
                    "Save the last N seconds of gameplay (Game Bar instant "
                    "replay). Requires background recording to be ON "
                    "(set_game_recording). Returns the saved video path."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="set_game_recording",
                description=(
                    "Turn Game Bar background recording (instant-replay buffer) "
                    "on or off and set how many seconds it keeps. Needed before "
                    "game_clip works. May take effect only after the next game "
                    "launch or sign-in."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "seconds": {"type": "integer", "description": "Buffer length, e.g. 30 or 60."},
                    },
                    "required": ["enabled"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "game_screenshot":
            return await self._capture(_SCREENSHOT_CHORD, _IMAGE_EXTS, "screenshot")
        if tool_name == "game_clip":
            return await self._capture(_CLIP_CHORD, _VIDEO_EXTS, "clip")
        if tool_name == "set_game_recording":
            return self._set_recording(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _snapshot(self) -> "set[str]":
        if not self._captures.exists():
            return set()
        return {str(p) for p in self._captures.iterdir() if p.is_file()}

    def _entries(self) -> "list[tuple[str, float]]":
        if not self._captures.exists():
            return []
        out = []
        for p in self._captures.iterdir():
            try:
                out.append((str(p), p.stat().st_mtime))
            except OSError:
                continue
        return out

    async def _capture(self, chord: list[str], exts: "set[str]", label: str) -> ToolResult:
        before = self._snapshot()
        try:
            self._press(chord)
        except Exception as exc:
            return ToolResult(content=f"game {label} hotkey failed: {exc}", is_error=True)
        # Poll for a new file to land (Game Bar writes it a beat later).
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.4)
            found = newest_new_file(before, self._entries(), exts)
            if found:
                return ToolResult(content=json.dumps({"path": found, "ok": True}))
        return ToolResult(
            content=json.dumps({
                "ok": False,
                "error": (
                    f"no new {label} appeared in {self._captures}. "
                    "Is Game Bar enabled and a game focused"
                    + (" (and background recording on)?" if label == "clip" else "?")
                ),
            }),
            is_error=True,
        )

    def _set_recording(self, args: dict) -> ToolResult:
        import sys
        if sys.platform != "win32":
            return ToolResult(content="set_game_recording is Windows-only", is_error=True)
        enabled = bool(args.get("enabled"))
        seconds = int(args.get("seconds") or 60)
        try:
            self._registry_set(enabled, seconds)
        except Exception as exc:
            return ToolResult(content=f"set_game_recording failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps({
            "ok": True, "enabled": enabled, "seconds": seconds,
            "note": "May take effect only after the next game launch or sign-in.",
        }))


def create() -> GameCapturePlugin:
    return GameCapturePlugin()
