import json
import platform
import subprocess
import sys
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "debuglog"
REQUIRED_CAPABILITIES = frozenset({"fs_read", "fs_write"})
LOG_FILES = ("cerebral.log", "cerebral.err.log", "launcher.log")


def create(**kwargs):
    return DebuglogPlugin(**kwargs)


def fetch_log_entries(root, max_lines=300) -> list[dict]:
    entries = []
    for name in LOG_FILES:
        path = Path(root) / name
        if path.exists():
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            entries.append({"file": name, "lines": lines[-max_lines:]})
    return entries


def build_report(entries, versions) -> str:
    lines = ["Felix debug log"]
    for k, v in versions.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    for entry in entries:
        lines.append(f"== {entry['file']} ==")
        lines.extend(entry["lines"])
    return "\n".join(lines)


class DebuglogPlugin:
    name = PLUGIN_NAME

    def __init__(self, root=None, run_fn=None):
        if root is None:
            root = Path(__file__).resolve().parent.parent
        self.root = root
        self.run_fn = run_fn or subprocess.run

    def _get_versions(self) -> dict:
        versions = {
            "Python": platform.python_version(),
            "Platform": platform.platform(),
            "Architecture": platform.machine(),
        }
        head_path = Path(self.root) / ".git" / "HEAD"
        commit = "unknown"
        if head_path.exists():
            try:
                content = head_path.read_text()
                if content.startswith("ref: "):
                    ref = content.split(" ")[1].strip()
                    ref_path = Path(self.root) / ".git" / ref
                    if ref_path.exists():
                        commit = ref_path.read_text().strip()
                else:
                    commit = content.strip()
            except Exception:
                pass
        versions["GitCommit"] = commit
        return versions

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="debuglog_fetch",
                description="Fetch recent debug log entries and build a report.",
                plugin=PLUGIN_NAME,
                schema={},
            ),
            Tool(
                name="debuglog_save",
                description="Save debug report to a file via native dialog.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "debuglog_fetch":
            entries = fetch_log_entries(self.root)
            versions = self._get_versions()
            report = build_report(entries, versions)
            files = [e["file"] for e in entries]
            line_count = sum(len(e["lines"]) for e in entries)
            return ToolResult(content=json.dumps({"report": report, "files": files, "line_count": line_count}))
        elif tool_name == "debuglog_save":
            content = args.get("content")
            if not isinstance(content, str):
                return ToolResult(content="Error: 'content' must be a string.", is_error=True)
            if len(content) > 5_000_000:
                return ToolResult(content="Error: 'content' exceeds 5,000,000 characters.", is_error=True)
            
            pick_code = """import sys; import tkinter; root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True); path = tkinter.filedialog.asksaveasfilename(initialfile='debuglog.txt', defaultextension='.txt'); print(path if path else ''); root.destroy()"""
            proc = self.run_fn([sys.executable, "-c", pick_code], capture_output=True, text=True)
            chosen = proc.stdout.strip()
            if not chosen:
                return ToolResult(content=json.dumps({"canceled": True}))
            
            try:
                Path(chosen).write_text(content, encoding="utf-8")
                return ToolResult(content=json.dumps({"canceled": False, "path": chosen}))
            except Exception as e:
                return ToolResult(content=f"Error writing file: {e}", is_error=True)
        else:
            return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    def panel_spec(self, profile_id=None) -> dict:
        entries = fetch_log_entries(self.root)
        versions = self._get_versions()
        report = build_report(entries, versions)
        files = [e["file"] for e in entries]
        line_count = sum(len(e["lines"]) for e in entries)
        
        return {
            "title": "Debug log",
            "widgets": [
                {
                    "type": "detail",
                    "fields": {
                        "files_read": ", ".join(files) if files else "none",
                        "line_count": line_count,
                    },
                },
                {
                    "type": "action",
                    "id": "debuglog-refresh",
                    "label": "Refresh",
                    "tool": "debuglog_fetch",
                    "tool_args": {},
                },
                {
                    "type": "text",
                    "id": "debuglog-report",
                    "label": "Report (edit before saving if you like)",
                    "tool": "debuglog_save",
                    "tool_args": {},
                    "value": report,
                },
            ],
        }
