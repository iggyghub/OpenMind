import json
import os
import subprocess
import sys
from pathlib import Path

from cerebral.tools import ToolResult


class FelixDialogsPlugin:
    PLUGIN_NAME = "felix_dialogs"
    REQUIRED_CAPABILITIES = frozenset({"fs_read"})

    def __init__(self, run_fn=None, repo_root=None):
        self.run_fn = run_fn or subprocess.run
        self.repo_root = repo_root or Path(__file__).resolve().parent.parent

    def create(self):
        return [
            self.felix_dialog_open_path,
            self.felix_dialog_save_path,
        ]

    list_tools = create

    def felix_dialog_open_path(self):
        return self._run_dialog("open")

    def felix_dialog_save_path(self):
        return self._run_dialog("save")

    def _run_dialog(self, mode):
        script = '''
import sys, os, json, tkinter as tk
root = tk.Tk()
root.withdraw()
from tkinter import filedialog
repo = os.environ["FELIX_REPO_ROOT"]
if sys.argv[1] == "open":
    p = filedialog.askopenfilename(title="Open local file", initialdir=repo, filetypes=[("HTML files", "*.html *.htm"), ("All files", "*.*")])
else:
    p = filedialog.asksaveasfilename(title="New page", initialdir=repo, filetypes=[("HTML files", "*.html"), ("All files", "*.*")])
root.destroy()
if not p:
    print(json.dumps({"canceled": True}))
    sys.exit(0)
try:
    rel = os.path.relpath(p, repo)
except ValueError:
    print(json.dumps({"canceled": False, "error": "Must be inside the OpenMind folder."}))
    sys.exit(0)
if rel.startswith(".."):
    print(json.dumps({"canceled": False, "error": "Must be inside the OpenMind folder."}))
else:
    print(json.dumps({"canceled": False, "path": rel.replace(os.sep, "/")}))
'''
        env = os.environ.copy()
        env["FELIX_REPO_ROOT"] = str(self.repo_root)
        res = self.run_fn(
            [sys.executable, "-c", script, mode],
            capture_output=True,
            text=True,
            env=env,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return ToolResult(content='{"canceled": true}')
        return ToolResult(content=res.stdout.strip())
