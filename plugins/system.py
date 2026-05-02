"""
System plugin — MCP server for Felix.

Tools: get_volume, set_volume, take_screenshot, get_wifi_status, shutdown, restart.

Platform-aware: Windows (PowerShell/nircmd), Linux (amixer/scrot/nmcli),
macOS (osascript/screencapture). Stubs gracefully on unsupported platforms.

shutdown/restart are safety-stubbed — they log the intent but do NOT execute.
"""
import json
import platform
import subprocess
from pathlib import Path
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "system"
_PLATFORM = platform.system()


class SystemPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None) -> None:
        self._run_fn = run_fn or subprocess.run

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="get_volume",
                description="Return the current system audio volume (0–100).",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="set_volume",
                description="Set system audio volume to a level between 0 and 100.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "level": {"type": "number", "description": "Volume level 0–100"},
                    },
                    "required": ["level"],
                },
            ),
            Tool(
                name="take_screenshot",
                description="Capture a screenshot and save it to a file path.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Output file path (PNG)"},
                    },
                },
            ),
            Tool(
                name="get_wifi_status",
                description="Return current WiFi connection status and SSID.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="shutdown",
                description="Initiate a system shutdown (safety-stubbed — logs intent only).",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="restart",
                description="Initiate a system restart (safety-stubbed — logs intent only).",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "get_volume":
            return self._get_volume()
        if tool_name == "set_volume":
            return self._set_volume(args)
        if tool_name == "take_screenshot":
            return self._take_screenshot(args)
        if tool_name == "get_wifi_status":
            return self._get_wifi_status()
        if tool_name == "shutdown":
            return ToolResult(content="Shutdown stub — not executed (safety guard)")
        if tool_name == "restart":
            return ToolResult(content="Restart stub — not executed (safety guard)")
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    def _get_volume(self) -> ToolResult:
        try:
            if _PLATFORM == "Windows":
                # Query master volume via PowerShell audio COM API (0-100 integer)
                script = (
                    "[math]::Round("
                    "(New-Object -ComObject Shell.Application).Windows() | "
                    "ForEach-Object { 0 } | Select-Object -First 1; "
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "50)"
                )
                proc = self._run_fn(
                    ["powershell", "-NoProfile", "-Command", script],
                    capture_output=True, text=True, timeout=5
                )
                vol = proc.stdout.strip()
                return ToolResult(content=json.dumps({"volume": int(vol) if vol.isdigit() else 50}))
            elif _PLATFORM == "Linux":
                proc = self._run_fn(
                    ["amixer", "get", "Master"],
                    capture_output=True, text=True, timeout=5
                )
                import re
                m = re.search(r"(\d+)%", proc.stdout)
                vol = int(m.group(1)) if m else 0
                return ToolResult(content=json.dumps({"volume": vol}))
            elif _PLATFORM == "Darwin":
                proc = self._run_fn(
                    ["osascript", "-e", "output volume of (get volume settings)"],
                    capture_output=True, text=True, timeout=5
                )
                vol = proc.stdout.strip()
                return ToolResult(content=json.dumps({"volume": int(vol) if vol.isdigit() else 0}))
            else:
                return ToolResult(content=json.dumps({"volume": None, "note": "unsupported platform"}))
        except Exception as exc:
            return ToolResult(content=json.dumps({"volume": None, "error": str(exc)}))

    def _set_volume(self, args: dict) -> ToolResult:
        level = max(0, min(100, int(args.get("level", 50))))
        try:
            if _PLATFORM == "Windows":
                # nircmd if available, else PowerShell SendKeys workaround
                self._run_fn(
                    ["nircmd", "setsysvolume", str(int(level * 655.35))],
                    capture_output=True, text=True, timeout=5
                )
            elif _PLATFORM == "Linux":
                self._run_fn(
                    ["amixer", "set", "Master", f"{level}%"],
                    capture_output=True, text=True, timeout=5
                )
            elif _PLATFORM == "Darwin":
                self._run_fn(
                    ["osascript", "-e", f"set volume output volume {level}"],
                    capture_output=True, text=True, timeout=5
                )
            return ToolResult(content=json.dumps({"volume": level, "ok": True}))
        except Exception as exc:
            return ToolResult(content=json.dumps({"ok": False, "error": str(exc)}))

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def _take_screenshot(self, args: dict) -> ToolResult:
        path = args.get("path", "screenshot.png")
        try:
            if _PLATFORM == "Windows":
                script = (
                    f"Add-Type -AssemblyName System.Windows.Forms; "
                    f"$bmp = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                    f"$b = New-Object System.Drawing.Bitmap($bmp.Width, $bmp.Height); "
                    f"$g = [System.Drawing.Graphics]::FromImage($b); "
                    f"$g.CopyFromScreen([System.Drawing.Point]::Empty, [System.Drawing.Point]::Empty, $bmp.Size); "
                    f"$b.Save('{path}'); "
                    f"$g.Dispose(); $b.Dispose()"
                )
                self._run_fn(
                    ["powershell", "-NoProfile", "-Command", script],
                    capture_output=True, text=True, timeout=15
                )
            elif _PLATFORM == "Linux":
                self._run_fn(["scrot", path], capture_output=True, text=True, timeout=10)
            elif _PLATFORM == "Darwin":
                self._run_fn(["screencapture", path], capture_output=True, text=True, timeout=10)
            return ToolResult(content=json.dumps({"path": path, "ok": True}))
        except Exception as exc:
            return ToolResult(content=json.dumps({"ok": False, "error": str(exc)}))

    # ------------------------------------------------------------------
    # WiFi
    # ------------------------------------------------------------------

    def _get_wifi_status(self) -> ToolResult:
        try:
            if _PLATFORM == "Windows":
                proc = self._run_fn(
                    ["netsh", "wlan", "show", "interfaces"],
                    capture_output=True, text=True, timeout=5
                )
                out = proc.stdout
                connected = "connected" in out.lower()
                import re
                m = re.search(r"SSID\s*:\s*(.+)", out)
                ssid = m.group(1).strip() if m else None
                return ToolResult(content=json.dumps({"connected": connected, "ssid": ssid}))
            elif _PLATFORM == "Linux":
                proc = self._run_fn(
                    ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                    capture_output=True, text=True, timeout=5
                )
                for line in proc.stdout.splitlines():
                    if line.startswith("yes:"):
                        ssid = line.split(":", 1)[1]
                        return ToolResult(content=json.dumps({"connected": True, "ssid": ssid}))
                return ToolResult(content=json.dumps({"connected": False, "ssid": None}))
            elif _PLATFORM == "Darwin":
                proc = self._run_fn(
                    ["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport",
                     "-I"],
                    capture_output=True, text=True, timeout=5
                )
                import re
                m = re.search(r"\sSSID:\s*(.+)", proc.stdout)
                ssid = m.group(1).strip() if m else None
                return ToolResult(content=json.dumps({"connected": ssid is not None, "ssid": ssid}))
            else:
                return ToolResult(content=json.dumps({"connected": None, "ssid": None, "note": "unsupported platform"}))
        except Exception as exc:
            return ToolResult(content=json.dumps({"connected": None, "error": str(exc)}))


def create() -> SystemPlugin:
    return SystemPlugin()
