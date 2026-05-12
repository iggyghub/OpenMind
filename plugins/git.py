"""
Git MCP plugin — Issue #24.

Tools: git_status, git_commit, git_push, git_pull, git_diff, git_log, git_branch.

Shells out to the local `git` binary via an injectable run_fn (defaults to
subprocess.run, same pattern as plugins/shell.py and plugins/system.py).
Every tool accepts an optional `repo_path` (defaults to os.getcwd()) and
returns {stdout, stderr, exit_code} on success.

Requires `git` on PATH. If the binary is missing, run_fn raises and the
plugin returns is_error=True — same fail-loud behaviour as ShellPlugin.
"""
import json
import os
import subprocess
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "git"

# ADR-0005 / Issue #44 — git_status / git_diff / git_log read the local repo
# (fs_read); git_commit / git_branch modify the working tree and ref store
# (fs_write); git_push / git_pull talk to the configured upstream
# (network_egress_cloud). The git CLI argv is restricted to a closed set of
# subcommands.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "fs_read",
    "fs_write",
    "network_egress_cloud",
})


class GitPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None) -> None:
        self._run_fn = run_fn or subprocess.run

    def list_tools(self) -> list[Tool]:
        repo_path_prop = {
            "type": "string",
            "description": "Path to the git repo (defaults to current working directory)",
        }
        return [
            Tool(
                name="git_status",
                description="Show working tree status of a git repo.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"repo_path": repo_path_prop},
                },
            ),
            Tool(
                name="git_commit",
                description="Create a commit with the given message. Stages all tracked changes via -a.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Commit message"},
                        "repo_path": repo_path_prop,
                    },
                    "required": ["message"],
                },
            ),
            Tool(
                name="git_push",
                description="Push commits to the configured upstream.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"repo_path": repo_path_prop},
                },
            ),
            Tool(
                name="git_pull",
                description="Pull from upstream and fast-forward / merge.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"repo_path": repo_path_prop},
                },
            ),
            Tool(
                name="git_diff",
                description="Show unstaged changes in the working tree.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"repo_path": repo_path_prop},
                },
            ),
            Tool(
                name="git_log",
                description="Show recent commits.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "max_count": {
                            "type": "integer",
                            "description": "Limit number of commits returned (default 10)",
                        },
                        "repo_path": repo_path_prop,
                    },
                },
            ),
            Tool(
                name="git_branch",
                description="List branches, or create a branch when 'name' is provided.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "If provided, create this branch instead of listing.",
                        },
                        "repo_path": repo_path_prop,
                    },
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        argv_builders = {
            "git_status": lambda a: ["git", "status"],
            "git_push": lambda a: ["git", "push"],
            "git_pull": lambda a: ["git", "pull"],
            "git_diff": lambda a: ["git", "diff"],
            "git_log": lambda a: ["git", "log", "-n", str(a.get("max_count", 10))],
        }
        if tool_name in argv_builders:
            return self._run(argv_builders[tool_name](args), args)
        if tool_name == "git_commit":
            message = args.get("message")
            if not message:
                return ToolResult(content="'message' is required for git_commit", is_error=True)
            return self._run(["git", "commit", "-a", "-m", message], args)
        if tool_name == "git_branch":
            argv = ["git", "branch"]
            if args.get("name"):
                argv.append(args["name"])
            return self._run(argv, args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _run(self, argv: list[str], args: dict) -> ToolResult:
        repo_path = args.get("repo_path") or os.getcwd()
        try:
            proc = self._run_fn(
                argv,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="git command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"git command failed: {exc}", is_error=True)

        if proc.returncode != 0:
            return ToolResult(
                content=json.dumps({
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "exit_code": proc.returncode,
                }),
                is_error=True,
            )
        return ToolResult(content=json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }))


def create() -> GitPlugin:
    return GitPlugin()
