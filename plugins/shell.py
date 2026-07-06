"""
Shell plugin -- MCP server for Felix.

Tools: run_command -> {stdout, stderr, exit_code}.
Routes through WindowsSandbox (ADR-0010 SBX-3): never calls subprocess directly.
"""
import json
import sys
from pathlib import Path
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "shell"

# ADR-0005 / Issue #44 -- run_command runs arbitrary user-supplied shell. This
# is the canonical shell_exec tool; the default policy denies it unless the
# user opts in via a one-time settings flag.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"shell_exec", "fs_write"})

# Wired by main.py via set_workdir_fn; returns the per-profile sandbox workdir.
_workdir_fn: Callable[[], str] | None = None

from cerebral.paths import data_dir

_DEFAULT_WORKDIR = str(data_dir() / "sandbox" / "default")


def set_workdir_fn(fn: Callable[[], str]) -> None:
    global _workdir_fn
    _workdir_fn = fn


def _resolve_workdir() -> str:
    if _workdir_fn is not None:
        return _workdir_fn()
    wd = Path(_DEFAULT_WORKDIR)
    wd.mkdir(parents=True, exist_ok=True)
    return str(wd)


_UNSET = object()  # sentinel: "caller did not pass sandbox"


class ShellPlugin:
    name = PLUGIN_NAME

    def __init__(self, sandbox=_UNSET) -> None:
        if sandbox is _UNSET:
            if sys.platform == "win32":
                from cerebral.sandbox import WindowsSandbox
                self._sandbox = WindowsSandbox()
            else:
                self._sandbox = None  # non-Windows: gate keeps shell_exec denied
        else:
            self._sandbox = sandbox  # explicit injection (None forces no-sandbox path)

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
        if self._sandbox is None:
            return ToolResult(content="shell_exec is not available on this platform", is_error=True)
        command = args["command"]
        timeout = float(args.get("timeout", 30))
        workdir = _resolve_workdir()
        try:
            from cerebral.sandbox._interface import SandboxResult
            result: SandboxResult = self._sandbox.spawn(
                ["cmd", "/c", command],
                workdir,
                timeout_s=timeout,
            )
            payload = json.dumps({
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
            })
            return ToolResult(content=payload, is_error=bool(result.killed_reason))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)


def create() -> ShellPlugin:
    return ShellPlugin()
