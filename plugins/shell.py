"""
Shell plugin — MCP server for Felix.

Tools: run_command → {stdout, stderr, exit_code}.
"""
import json
import subprocess
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "shell"


class ShellPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None) -> None:
        self._run_fn = run_fn or subprocess.run

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="run_command",
                description="Run a shell command and return stdout, stderr, and exit code.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to execute"},
                        "timeout": {
                            "type": "number",
                            "description": "Max seconds to wait (default 30)",
                        },
                    },
                    "required": ["command"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "run_command":
            return self._run_command(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _run_command(self, args: dict) -> ToolResult:
        command = args["command"]
        timeout = args.get("timeout", 30)
        try:
            proc = self._run_fn(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return ToolResult(content=json.dumps({
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
            }))
        except subprocess.TimeoutExpired:
            return ToolResult(content="Command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)


def create() -> ShellPlugin:
    return ShellPlugin()
