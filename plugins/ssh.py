"""
SSH MCP plugin — Issue #24.

Tool: ssh_run_command(host, command, port?, key_path?).

Builds an `ssh user@host -i key -p port -o BatchMode=yes "command"` line via
an injectable run_fn. We never write keys, never prompt — `BatchMode=yes`
ensures the call fails immediately if the remote needs password auth, rather
than blocking on an unsolvable interactive prompt.
"""
import json
import subprocess
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "ssh"


class SshPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None) -> None:
        self._run_fn = run_fn or subprocess.run

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="ssh_run_command",
                description=(
                    "Run a command on a remote host via SSH and return its "
                    "stdout/stderr/exit_code. BatchMode is enabled so any "
                    "auth challenge fails fast rather than blocking on a prompt."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "host": {
                            "type": "string",
                            "description": "Target in `user@hostname` form.",
                        },
                        "command": {
                            "type": "string",
                            "description": "Command to run on the remote host.",
                        },
                        "port": {
                            "type": "integer",
                            "description": "SSH port (default 22).",
                        },
                        "key_path": {
                            "type": "string",
                            "description": "Path to a private key file.",
                        },
                    },
                    "required": ["host", "command"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name != "ssh_run_command":
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

        host = args.get("host")
        command = args.get("command")
        if not host:
            return ToolResult(content="'host' is required for ssh_run_command", is_error=True)
        if not command:
            return ToolResult(content="'command' is required for ssh_run_command", is_error=True)

        argv: list[str] = ["ssh", "-o", "BatchMode=yes"]
        if args.get("port"):
            argv.extend(["-p", str(args["port"])])
        if args.get("key_path"):
            argv.extend(["-i", args["key_path"]])
        argv.append(host)
        argv.append(command)

        try:
            proc = self._run_fn(
                argv,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="ssh command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"ssh command failed: {exc}", is_error=True)

        payload = json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        })
        return ToolResult(content=payload, is_error=proc.returncode != 0)


def create() -> SshPlugin:
    return SshPlugin()
