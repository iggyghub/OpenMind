import subprocess
import ctypes
import json
import sys
from typing import Any
from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "desktop_dialogs"
REQUIRED_CAPABILITIES = frozenset({"fs_read", "fs_write", "device_control"})

_PICK_CODE = """
import tkinter as tk
import tkinter.filedialog as fd
import sys
root = tk.Tk()
root.withdraw()
title = sys.argv[1]
result = fd.askdirectory(title=title)
root.destroy()
print(result)
"""

_SAVE_CODE = """
import tkinter as tk
import tkinter.filedialog as fd
import sys
root = tk.Tk()
root.withdraw()
default = sys.argv[1]
result = fd.asksaveasfilename(initialfile=default)
root.destroy()
print(result)
"""

_MSG_INFO_CODE = """
import tkinter as tk
import tkinter.messagebox as mb
import sys
root = tk.Tk()
root.withdraw()
msg = sys.argv[1]
mb.showinfo(title="Felix", message=msg)
root.destroy()
"""

_MSG_WARNING_CODE = """
import tkinter as tk
import tkinter.messagebox as mb
import sys
root = tk.Tk()
root.withdraw()
msg = sys.argv[1]
mb.showwarning(title="Felix", message=msg)
root.destroy()
"""

_MSG_ERROR_CODE = """
import tkinter as tk
import tkinter.messagebox as mb
import sys
root = tk.Tk()
root.withdraw()
msg = sys.argv[1]
mb.showerror(title="Felix", message=msg)
root.destroy()
"""

class DesktopDialogs:
    name = PLUGIN_NAME

    def __init__(self, run_fn=None, spawn_fn=None, user32=None):
        self.run_fn = run_fn or subprocess.run
        self.spawn_fn = spawn_fn or subprocess.Popen
        self.user32 = user32

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="pick_folder",
                description="Pick a folder using native dialog.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "default": "Choose a folder"}
                    },
                    "required": ["title"]
                }
            ),
            Tool(
                name="pick_save_path",
                description="Pick a save path using native dialog.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "default_name": {"type": "string", "default": "untitled.txt"}
                    },
                    "required": ["default_name"]
                }
            ),
            Tool(
                name="show_in_folder",
                description="Show item in folder.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}
                    },
                    "required": ["path"]
                }
            ),
            Tool(
                name="message_box",
                description="Show message box.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["info", "warning", "error"]},
                        "message": {"type": "string"}
                    },
                    "required": ["kind", "message"]
                }
            ),
            Tool(
                name="flash_attention",
                description="Flash window attention.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "start": {"type": "boolean", "default": True}
                    },
                    "required": ["start"]
                }
            )
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        try:
            if tool_name == "pick_folder":
                title = args.get("title", "Choose a folder")
                code = _PICK_CODE
                result = self.run_fn([sys.executable, "-c", code, title], capture_output=True, text=True)
                path = result.stdout.strip()
                if not path:
                    return ToolResult(content=json.dumps({"canceled": True}))
                return ToolResult(content=json.dumps({"canceled": False, "path": path}))

            elif tool_name == "pick_save_path":
                default_name = args.get("default_name", "untitled.txt")
                if len(default_name) > 255 or "/" in default_name or "\\" in default_name:
                    return ToolResult(content=json.dumps({"is_error": True, "message": "Invalid default_name"}), is_error=True)
                code = _SAVE_CODE
                result = self.run_fn([sys.executable, "-c", code, default_name], capture_output=True, text=True)
                path = result.stdout.strip()
                if not path:
                    return ToolResult(content=json.dumps({"canceled": True}))
                return ToolResult(content=json.dumps({"canceled": False, "path": path}))

            elif tool_name == "show_in_folder":
                path = args.get("path")
                import os
                if not os.path.exists(path):
                    return ToolResult(content=json.dumps({"is_error": True, "message": "Path does not exist"}), is_error=True)
                self.run_fn(["explorer", "/select," + path], capture_output=True)
                return ToolResult(content=json.dumps({"shown": True}))

            elif tool_name == "message_box":
                kind = args.get("kind")
                message = args.get("message", "")
                if kind == "info":
                    code = _MSG_INFO_CODE
                elif kind == "warning":
                    code = _MSG_WARNING_CODE
                elif kind == "error":
                    code = _MSG_ERROR_CODE
                else:
                    return ToolResult(content=json.dumps({"is_error": True, "message": "Invalid kind"}), is_error=True)
                
                if len(message) > 2000:
                    return ToolResult(content=json.dumps({"is_error": True, "message": "Message too long"}), is_error=True)

                self.spawn_fn([sys.executable, "-c", code, message])
                return ToolResult(content=json.dumps({"shown": True}))

            elif tool_name == "flash_attention":
                start = args.get("start", True)
                if self.user32 is None:
                    self.user32 = ctypes.windll.user32
                hwnd = self.user32.FindWindowW(None, "Felix")
                if hwnd == 0:
                    return ToolResult(content=json.dumps({"window_found": False, "flashing": False}))
                
                class FLASHWINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.c_uint),
                        ("hwnd", ctypes.c_uint),
                        ("dwFlags", ctypes.c_uint),
                        ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint),
                    ]
                
                fwi = FLASHWINFO()
                fwi.cbSize = ctypes.sizeof(FLASHWINFO)
                fwi.hwnd = hwnd
                fwi.dwFlags = 15 if start else 0
                fwi.uCount = ctypes.c_uint(0)
                fwi.dwTimeout = ctypes.c_uint(0)
                
                self.user32.FlashWindowEx(ctypes.byref(fwi))
                return ToolResult(content=json.dumps({"window_found": True, "flashing": start}))

            else:
                return ToolResult(content=json.dumps({"is_error": True, "message": "Unknown tool"}), is_error=True)
        except Exception as e:
            return ToolResult(content=json.dumps({"is_error": True, "message": str(e)}), is_error=True)

def create(**kwargs):
    return DesktopDialogs(**kwargs)
