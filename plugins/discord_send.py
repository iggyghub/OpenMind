"""
Discord send-image macro -- one tool that does the whole flow.

Local models can't reliably freehand the 6-step "screenshot -> copy -> focus
Discord -> Ctrl+K -> type contact -> paste -> send" chain (they loop the easy
steps and never reach the send). So this collapses it into ONE tool call the
model can't get out of order: send_image_to_discord(contact, message?).

No vision / no accessibility needed -- it drives Discord's keyboard
quick-switcher (Ctrl+K), which works even though Discord's UI is opaque to
UIA. Marked irreversible so the ADR-0005 modal confirms before the message
actually goes out.

Seams (screenshot_fn / clipboard_fn / focus_fn / press_fn / type_fn / sleep_fn)
keep it hermetic in tests -- no real screen, clipboard, focus, or keystrokes.
"""
from __future__ import annotations

import io
import json
import sys
import time
from typing import Callable, Optional

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "discord_send"

# screen_capture: grabs the screen. device_control: clipboard + focus + keys.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"screen_capture", "device_control"})

_TARGET_WINDOW = "Discord"


# ── default (real) seams -- Windows only, imported lazily ────────────────────

def _default_screenshot() -> bytes:
    from PIL import ImageGrab
    buf = io.BytesIO()
    ImageGrab.grab().convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _default_clipboard(png_bytes: bytes) -> None:
    import win32clipboard
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "BMP")
    dib = buf.getvalue()[14:]  # strip BITMAPFILEHEADER -> CF_DIB
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def _find_window(title: str) -> Optional[int]:
    import win32gui
    matches: list[int] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and title.lower() in win32gui.GetWindowText(hwnd).lower():
            matches.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return matches[0] if matches else None


def _launch_discord() -> None:
    """Launch Discord without shelling out (os.startfile keeps the plugin
    inspectability-clean). Prefer the Update.exe stub, fall back to the Start
    Menu shortcut."""
    import os
    from pathlib import Path
    update = Path(os.environ.get("LOCALAPPDATA", "")) / "Discord" / "Update.exe"
    if update.exists():
        os.startfile(str(update), arguments="--processStart Discord.exe")  # type: ignore[call-arg]
        return
    lnk = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
           / "Start Menu" / "Programs" / "Discord.lnk")
    if lnk.exists():
        os.startfile(str(lnk))


def _force_foreground(hwnd: int) -> None:
    """Bring hwnd to the front, working around Windows' focus-steal block via
    the classic synthetic-Alt nudge."""
    import ctypes
    import win32con
    import win32gui
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        # A synthetic Alt press unlocks SetForegroundWindow for a background
        # process (Windows only lets the app that owns the current foreground
        # set it otherwise).
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)   # Alt down
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)   # Alt up
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _default_focus(title: str) -> bool:
    """Ensure `title` is the foreground window -- launching the app if it's
    closed and forcing it forward if it's behind. Returns whether it actually
    got there (so the caller can refuse rather than type into the wrong app)."""
    import win32gui
    hwnd = _find_window(title)
    if hwnd is None:
        _launch_discord()
        # Poll for the window to appear after launch (cold start is slow).
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and hwnd is None:
            time.sleep(0.5)
            hwnd = _find_window(title)
        if hwnd is None:
            return False
    # Retry the foreground push a few times -- the first attempt after a launch
    # or from deep background often loses the race.
    for _ in range(5):
        _force_foreground(hwnd)
        time.sleep(0.35)
        fg = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        if title.lower() in fg.lower():
            return True
    return False


def _default_press(keys: list[str]) -> None:
    import pyautogui
    pyautogui.hotkey(*keys)


def _default_type(text: str) -> None:
    import pyautogui
    pyautogui.typewrite(text, interval=0.02)


class DiscordSendPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        *,
        screenshot_fn: Callable[[], bytes] | None = None,
        clipboard_fn: Callable[[bytes], None] | None = None,
        focus_fn: Callable[[str], bool] | None = None,
        press_fn: Callable[[list[str]], None] | None = None,
        type_fn: Callable[[str], None] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._screenshot = screenshot_fn or _default_screenshot
        self._clipboard = clipboard_fn or _default_clipboard
        self._focus = focus_fn or _default_focus
        self._press = press_fn or _default_press
        self._type = type_fn or _default_type
        self._sleep = sleep_fn or time.sleep

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="send_image_to_discord",
                description=(
                    "Take a screenshot and send it to a Discord contact in one "
                    "step: screenshot -> clipboard -> focus Discord -> Ctrl+K -> "
                    "type the contact -> Enter -> paste -> (optional message) -> "
                    "send. Discord must be open. Use this instead of chaining "
                    "screenshot/clipboard/press_keys yourself."
                ),
                plugin=PLUGIN_NAME,
                irreversible=True,  # sends a message -> confirm via ADR-0005 modal
                schema={
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "Discord user/DM name, e.g. 'Budd'."},
                        "message": {"type": "string", "description": "Optional text to send with the image."},
                    },
                    "required": ["contact"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name != "send_image_to_discord":
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)
        if sys.platform != "win32":
            return ToolResult(content="send_image_to_discord is Windows-only", is_error=True)
        return self._send(args)

    def _send(self, args: dict) -> ToolResult:
        contact = (args.get("contact") or "").strip()
        if not contact:
            return ToolResult(content="send_image_to_discord: contact is required", is_error=True)
        message = (args.get("message") or "").strip()

        try:
            png = self._screenshot()
            self._clipboard(png)
        except Exception as exc:
            return ToolResult(content=f"screenshot/clipboard failed: {exc}", is_error=True)

        # Focus Discord and confirm it actually came forward -- otherwise the
        # keystrokes would land in the wrong app.
        try:
            if not self._focus(_TARGET_WINDOW):
                return ToolResult(
                    content=(
                        "Couldn't bring Discord to the front (is it open, not "
                        "minimized?). Nothing was typed or sent."
                    ),
                    is_error=True,
                )
        except Exception as exc:
            return ToolResult(content=f"focus failed: {exc}", is_error=True)

        try:
            self._press(["ctrl", "k"])          # quick switcher
            self._sleep(0.5)
            self._type(contact)                  # find the contact
            self._sleep(0.6)
            self._press(["enter"])               # open that DM
            self._sleep(0.6)
            self._press(["ctrl", "v"])           # paste the screenshot
            self._sleep(0.6)
            if message:
                self._type(message)
                self._sleep(0.3)
            self._press(["enter"])               # send
        except Exception as exc:
            return ToolResult(content=f"send sequence failed: {exc}", is_error=True)

        return ToolResult(content=json.dumps({
            "ok": True, "contact": contact, "sent_message": bool(message),
        }))


def create() -> DiscordSendPlugin:
    return DiscordSendPlugin()
