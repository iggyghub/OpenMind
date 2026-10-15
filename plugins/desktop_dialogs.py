import subprocess
import sys
import os
import json
import ctypes
from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "desktop_dialogs"
REQUIRED_CAPABILITIES = frozenset({"fs_read", "fs_write", "device_control"})

def create(**kwargs):
    return DesktopDialogsPlugin(**kwargs)

class DesktopDialogsPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn=None, spawn_fn=None, user32=None):
        self.run_fn = run_fn or subprocess.run
        self.spawn_fn = spawn_fn or subprocess.Popen
        self._user32 = user32
        self._user32_loaded = False

    def _get_user32(self):
        if not self._user32:
            import ctypes.windll.user32
            self._user32 = ctypes.windll.user32
        return self._user32

    def _run_tkinter_dialog(self, code: str) -> str:
        res = self.run_fn([sys.executable, "-c", code], capture_output=True, text=True)
        return res.stdout.strip()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="pick_folder",
                description="Open a native folder picker dialog.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"title": {"type": "string", "default": "Choose a folder"}},
                    "required": []
                }
            ),
            Tool(
                name="pick_save_path",
                description="Open a native save file dialog.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"default_name": {"type": "string", "default": "untitled.txt"}},
                    "required": []
                }
            ),
            Tool(
                name="show_in_folder",
                description="Show a file/folder in the native file explorer.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]
                }
            ),
            Tool(
                name="message_box",
                description="Show a native message box (fire-and-forget).",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"kind": {"type": "string", "enum": ["info", "warning", "error"]}, "message": {"type": "string"}},
                    "required": ["kind", "message"]
                }
            ),
            Tool(
                name="flash_attention",
                description="Start or stop flashing the application window in the taskbar.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"start": {"type": "boolean", "default": True}},
                    "required": []
                }
            )
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "pick_folder":
            title = args.get("title", "Choose a folder")
            code = f"""
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
path = filedialog.askdirectory(title='{title.replace(chr(39), chr(92)+chr(39))}')
print(path)
root.destroy()
"""
            path = self._run_tkinter_dialog(code)
            if not path:
                return ToolResult(content=json.dumps({"canceled": True}))
            return ToolResult(content=json.dumps({"canceled": False, "path": path}))

        elif tool_name == "pick_save_path":
            default_name = args.get("default_name", "untitled.txt")
            if len(default_name) > 255 or "/" in default_name or "\\" in default_name:
                return ToolResult(content=json.dumps({"error": "default_name must be <= 255 chars and contain no path separators"}), is_error=True)
            code = f"""
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
path = filedialog.asksaveasfilename(initialfile='{default_name.replace(chr(39), chr(92)+chr(39))}')
print(path)
root.destroy()
"""
            path = self._run_tkinter_dialog(code)
            if not path:
                return ToolResult(content=json.dumps({"canceled": True}))
            return ToolResult(content=json.dumps({"canceled": False, "path": path}))

        elif tool_name == "show_in_folder":
            path = args.get("path")
            if not path or not os.path.exists(path):
                return ToolResult(content=json.dumps({"error": "path does not exist"}), is_error=True)
            self.run_fn(["explorer", "/select," + path], capture_output=True)
            return ToolResult(content=json.dumps({"shown": True}))

        elif tool_name == "message_box":
            kind = args.get("kind")
            message = args.get("message", "")
            if len(message) > 2000:
                return ToolResult(content=json.dumps({"error": "message exceeds 2000 chars"}), is_error=True)
            if kind not in ("info", "warning", "error"):
                return ToolResult(content=json.dumps({"error": "kind must be info, warning, or error"}), is_error=True)
            code = f"""
import tkinter as tk
from tkinter import messagebox
root = tk.Tk()
root.withdraw()
messagebox.show{kind.capitalize()}("Signal", '{message.replace(chr(39), chr(92)+chr(39))}')
root.destroy()
"""
            self.spawn_fn([sys.executable, "-c", code])
            return ToolResult(content=json.dumps({"shown": True}))

        elif tool_name == "flash_attention":
            start = args.get("start", True)
            user32 = self._get_user32()
            hwnd = user32.FindWindowW(None, "Felix")
            if hwnd == 0:
                return ToolResult(content=json.dumps({"window_found": False, "flashing": False}))

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("hwnd", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint),
                    ("dwTimeout", ctypes.c_uint)
                ]

            fwi = FLASHWINFO()
            fwi.cbSize = ctypes.sizeof(fwi)
            fwi.hwnd = hwnd
            if start:
                fwi.dwFlags = 0x3 | 0xC  # FLASHW_ALL | FLASHW_TIMERNOFG
            else:
                fwi.dwFlags = 0  # FLASHW_STOP
            user32.FlashWindowEx(ctypes.byref(fwi))
            return ToolResult(content=json.dumps({"window_found": True, "flashing": start}))

        else:
            return ToolResult(content=json.dumps({"error": f"Unknown tool: {tool_name}"}), is_error=True)
