import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "felix_dialogs"
# fs_write is declared because the dialog subprocess is launched through run_fn and a
# save dialog can name a new file; the AST completeness check requires one of the
# process/file-write classes for any subprocess call (same as plugins/rea.py).
REQUIRED_CAPABILITIES = frozenset({"fs_read", "fs_write"})


class FelixDialogsPlugin:
    name = PLUGIN_NAME

    def __init__(self, root: Optional[Path] = None, run_fn: Optional[Any] = None):
        self.root = root or Path(__file__).resolve().parent.parent
        self.run_fn = run_fn or subprocess.run

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="felix_dialog_open_path",
                description="Open a native file dialog to select an existing file.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="felix_dialog_save_path",
                description="Open a native file dialog to save a new page.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "felix_dialog_open_path":
            result_str = self._run_dialog("open")
        elif tool_name == "felix_dialog_save_path":
            result_str = self._run_dialog("save")
        else:
            return ToolResult(content=json.dumps({"error": "Unknown tool"}))
        return ToolResult(content=result_str)

    def _run_dialog(self, dialog_type: str) -> str:
        title = "Open local file" if dialog_type == "open" else "New page"
        filters = [("HTML", [".html", ".htm"])] if dialog_type == "open" else [("HTML", [".html"])]

        script = f"""
import tkinter as tk
from tkinter import filedialog
import json
import sys
import os

root = tk.Tk()
root.withdraw()
filters = {filters}
dialog_type = {repr(dialog_type)}
repo_root = sys.argv[1]
if dialog_type == 'open':
    path = filedialog.askopenfilename(title='{title}', filetypes=filters)
else:
    path = filedialog.asksaveasfilename(title='{title}', filetypes=filters)

result = {{}}
if not path:
    result = {{"canceled": True}}
else:
    try:
        rel = os.path.relpath(path, repo_root)
        if rel.startswith('..'):
            result = {{"canceled": False, "error": "Must be inside the OpenMind folder."}}
        else:
            result = {{"canceled": False, "path": rel.replace(os.sep, '/')}}
    except ValueError:
        result = {{"canceled": False, "error": "Must be inside the OpenMind folder."}}

print(json.dumps(result))
"""
        proc = self.run_fn([sys.executable, "-c", script, str(self.root)], capture_output=True, text=True)
        try:
            return proc.stdout
        except Exception:
            return json.dumps({"canceled": True})


def create(**kwargs) -> FelixDialogsPlugin:
    return FelixDialogsPlugin(**kwargs)
