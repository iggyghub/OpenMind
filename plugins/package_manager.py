"""
Package Manager MCP plugin — Issue #24.

One plugin, three back-ends: npm, pip, winget.

Tools: pkg_install(manager, name), pkg_update(manager, name?), pkg_search(manager, query).
Validates manager against an allowlist; returns is_error=True on unknown.
Shells out via an injectable run_fn (defaults to subprocess.run).

Each manager has small idiomatic differences:
  npm    install / update / search
  pip    install / install -U / index versions  (pip search is disabled)
  winget install / upgrade / search
"""
import json
import subprocess
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "package_manager"

# ADR-0005 / Issue #44 — pkg_install / pkg_update install code from the
# configured registry (code_install). pkg_search hits the registry over the
# public internet (network_egress_cloud). Both install paths also reach the
# registry to fetch the package itself, so the cloud-egress declaration
# applies to every tool.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "code_install",
    "network_egress_cloud",
})

_ALLOWED_MANAGERS = {"npm", "pip", "winget"}


class PackageManagerPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_fn: Callable | None = None) -> None:
        self._run_fn = run_fn or subprocess.run

    def list_tools(self) -> list[Tool]:
        manager_prop = {
            "type": "string",
            "description": "Package manager: 'npm', 'pip', or 'winget'.",
            "enum": ["npm", "pip", "winget"],
        }
        return [
            Tool(
                name="pkg_install",
                description="Install a package via the chosen manager.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "manager": manager_prop,
                        "name": {"type": "string", "description": "Package name"},
                    },
                    "required": ["manager", "name"],
                },
            ),
            Tool(
                name="pkg_update",
                description=(
                    "Update a package (or all packages if 'name' is omitted)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "manager": manager_prop,
                        "name": {
                            "type": "string",
                            "description": "Package to update (optional; omit to update all)",
                        },
                    },
                    "required": ["manager"],
                },
            ),
            Tool(
                name="pkg_search",
                description="Search the registry for packages matching a query.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "manager": manager_prop,
                        "query": {"type": "string", "description": "Search term"},
                    },
                    "required": ["manager", "query"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name not in {"pkg_install", "pkg_update", "pkg_search"}:
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

        manager = args.get("manager")
        if not manager:
            return ToolResult(content="'manager' is required", is_error=True)
        if manager not in _ALLOWED_MANAGERS:
            return ToolResult(
                content=(
                    f"Unknown manager: '{manager}'. "
                    f"Allowed: {sorted(_ALLOWED_MANAGERS)}"
                ),
                is_error=True,
            )

        if tool_name == "pkg_install":
            name = args.get("name")
            if not name:
                return ToolResult(content="'name' is required for pkg_install", is_error=True)
            return self._run(self._build_install_argv(manager, name))

        if tool_name == "pkg_update":
            name = args.get("name")
            return self._run(self._build_update_argv(manager, name))

        if tool_name == "pkg_search":
            query = args.get("query")
            if not query:
                return ToolResult(content="'query' is required for pkg_search", is_error=True)
            return self._run(self._build_search_argv(manager, query))

        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    @staticmethod
    def _build_install_argv(manager: str, name: str) -> list[str]:
        if manager == "npm":
            return ["npm", "install", name]
        if manager == "pip":
            return ["pip", "install", name]
        # winget
        return ["winget", "install", "--silent", name]

    @staticmethod
    def _build_update_argv(manager: str, name: str | None) -> list[str]:
        if manager == "npm":
            argv = ["npm", "update"]
            if name:
                argv.append(name)
            return argv
        if manager == "pip":
            # `pip` has no real update subcommand. `install -U` upgrades a named
            # package; for "all" we fall back to `list --outdated` which is
            # informational rather than destructive.
            if name:
                return ["pip", "install", "-U", name]
            return ["pip", "list", "--outdated"]
        # winget
        argv = ["winget", "upgrade"]
        if name:
            argv.append(name)
        return argv

    @staticmethod
    def _build_search_argv(manager: str, query: str) -> list[str]:
        if manager == "npm":
            return ["npm", "search", query]
        if manager == "pip":
            # `pip search` is disabled on PyPI. `pip index versions <pkg>` is
            # the closest live replacement.
            return ["pip", "index", "versions", query]
        # winget
        return ["winget", "search", query]

    def _run(self, argv: list[str]) -> ToolResult:
        try:
            proc = self._run_fn(
                argv,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="package manager command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"package manager command failed: {exc}", is_error=True)

        payload = json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        })
        return ToolResult(content=payload, is_error=proc.returncode != 0)


def create() -> PackageManagerPlugin:
    return PackageManagerPlugin()
