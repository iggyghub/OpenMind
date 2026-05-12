"""
Apps plugin — MCP server for Felix.

Tools: list_running, launch_app, close_app.

Uses psutil for process enumeration and termination.
"""
import json
import subprocess
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "apps"

# ADR-0005 / Issue #44 — minimum capability classes this plugin's tools use.
# list_running / launch_app / close_app all manage local application processes
# (read process list, spawn, terminate). The subprocess.Popen call site in
# launch_app is restricted to user-named apps; the intent is device_control.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"device_control"})


def _default_process_iter():
    try:
        import psutil
        return list(psutil.process_iter(["name", "pid"]))
    except Exception:
        return []


class AppsPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        process_iter: Callable | None = None,
        popen_fn: Callable | None = None,
    ) -> None:
        self._process_iter = process_iter or _default_process_iter
        self._popen_fn = popen_fn or subprocess.Popen

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="list_running",
                description="List all currently running applications (name + PID).",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="launch_app",
                description="Launch an application by name or full path.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "app": {"type": "string", "description": "App name or full path to executable"},
                    },
                    "required": ["app"],
                },
            ),
            Tool(
                name="close_app",
                description="Close a running application by name or PID.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "app": {"type": "string", "description": "Process name (e.g. 'notepad.exe') or numeric PID"},
                    },
                    "required": ["app"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "list_running":
            return self._list_running()
        if tool_name == "launch_app":
            return self._launch_app(args)
        if tool_name == "close_app":
            return self._close_app(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _list_running(self) -> ToolResult:
        try:
            procs = self._process_iter()
            apps = [{"name": p.info["name"], "pid": p.info["pid"]} for p in procs]
            return ToolResult(content=json.dumps({"apps": apps}))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _launch_app(self, args: dict) -> ToolResult:
        app = args["app"]
        try:
            proc = self._popen_fn(app, shell=True)
            return ToolResult(content=json.dumps({"pid": proc.pid, "ok": True}))
        except FileNotFoundError:
            return ToolResult(content=f"App not found: '{app}'", is_error=True)
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _close_app(self, args: dict) -> ToolResult:
        target = args["app"]
        # Try numeric PID first
        if target.isdigit():
            return self._terminate_by_pid(int(target))
        return self._terminate_by_name(target)

    def _terminate_by_pid(self, pid: int) -> ToolResult:
        try:
            procs = self._process_iter()
            for p in procs:
                if p.info["pid"] == pid:
                    p.terminate()
                    return ToolResult(content=json.dumps({"ok": True, "pid": pid}))
            return ToolResult(content=f"No process with PID {pid}", is_error=True)
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _terminate_by_name(self, name: str) -> ToolResult:
        try:
            procs = self._process_iter()
            found = [p for p in procs if (p.info.get("name") or "").lower() == name.lower()]
            if not found:
                return ToolResult(content=f"No running process named '{name}'", is_error=True)
            for p in found:
                p.terminate()
            return ToolResult(content=json.dumps({"ok": True, "terminated": len(found)}))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)


def create() -> AppsPlugin:
    return AppsPlugin()
