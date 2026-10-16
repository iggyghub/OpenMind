import json
import os
from pathlib import Path
from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "autolaunch"
REQUIRED_CAPABILITIES = frozenset({"fs_read", "fs_write", "fs_delete"})


class AutolaunchPlugin:
    name = PLUGIN_NAME

    def __init__(self, startup_dir=None, root=None):
        # A literal "%APPDATA%" is never expanded by pathlib: resolve the real folder.
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        default_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        self.startup_dir = Path(startup_dir) if startup_dir else default_dir
        self.root = root or Path(__file__).resolve().parent.parent
        self.launcher_file = self.startup_dir / "Felix.cmd"

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="autolaunch_status",
                description="Get the current autolaunch status and launcher file path.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}, "required": []}
            ),
            Tool(
                name="autolaunch_set",
                description="Enable or disable autolaunch by creating or removing the launcher file.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]}
            )
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "autolaunch_status":
            enabled = self.launcher_file.exists()
            return ToolResult(content=json.dumps({"enabled": enabled, "path": str(self.launcher_file)}))

        elif tool_name == "autolaunch_set":
            enabled = args.get("enabled")
            if not isinstance(enabled, bool):
                return ToolResult(content="enabled must be a boolean", is_error=True)

            if enabled:
                self.startup_dir.mkdir(parents=True, exist_ok=True)
                content = f"@echo off\nstart \"\" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{self.root}\\scripts\\launch-felix.ps1\""
                # .cmd files are read by cmd.exe: write Windows line endings.
                self.launcher_file.write_text(content, encoding="ascii", newline="\r\n")
            else:
                if self.launcher_file.exists():
                    self.launcher_file.unlink()

            return ToolResult(content=json.dumps({"enabled": enabled, "path": str(self.launcher_file)}))

        else:
            return ToolResult(content="Unknown tool", is_error=True)


def create(**kwargs):
    return AutolaunchPlugin(**kwargs)
