"""
Network Scanner MCP plugin — Issue #26 (Security MCP — HITL).

Tools:
  - net_list_devices() — parses `arp -a` output for IP/MAC/hostname triples on
    the local LAN. Cross-platform parser (Windows two-column table layout vs
    POSIX `host (ip) at mac on iface` layout).
  - net_ping(host, count?) — shells out to the platform `ping`. Windows uses
    `-n COUNT`; POSIX uses `-c COUNT`. Default count = 4.
  - net_check_port(host, port, timeout?) — opens a TCP connection to the
    given (host, port) tuple via an injectable `socket_factory` (defaults to
    `socket.create_connection`). Returns {open: bool}; ConnectionRefusedError
    and socket.timeout collapse to `open: false` rather than is_error.

OS argv shaping is parameterised by an injectable `platform_name` (defaults
to sys.platform) so tests cover all branches without sys.platform patching.
The socket call is similarly injected so unit tests never open a real
network connection.
"""
import json
import re
import socket
import subprocess
import sys
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "network_scanner"


def _default_socket_factory(addr, timeout):
    return socket.create_connection(addr, timeout=timeout)


class NetworkScannerPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        run_fn: Callable | None = None,
        platform_name: str | None = None,
        socket_factory: Callable | None = None,
    ) -> None:
        self._run_fn = run_fn or subprocess.run
        self._platform_name = platform_name if platform_name is not None else sys.platform
        self._socket_factory = socket_factory or _default_socket_factory

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="net_list_devices",
                description=(
                    "List devices currently in the local ARP table "
                    "(IP, MAC, hostname when known)."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="net_ping",
                description="Ping a host and return stdout/stderr/exit_code.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "Hostname or IP to ping."},
                        "count": {"type": "integer", "description": "Number of echo requests (default 4)."},
                    },
                    "required": ["host"],
                },
            ),
            Tool(
                name="net_check_port",
                description=(
                    "Check whether a TCP port is open on a host. Returns "
                    "{open: bool}; refused/timeout count as closed."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "Hostname or IP."},
                        "port": {"type": "integer", "description": "TCP port number."},
                        "timeout": {
                            "type": "number",
                            "description": "Connect timeout in seconds (default 2.0).",
                        },
                    },
                    "required": ["host", "port"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "net_list_devices":
            return self._list_devices()
        if tool_name == "net_ping":
            return self._ping(args)
        if tool_name == "net_check_port":
            return self._check_port(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # net_list_devices
    # ------------------------------------------------------------------

    def _list_devices(self) -> ToolResult:
        try:
            proc = self._run_fn(
                ["arp", "-a"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="arp command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"arp failed: {exc}", is_error=True)

        devices = self._parse_arp(proc.stdout or "")
        return ToolResult(content=json.dumps({"devices": devices}))

    def _parse_arp(self, raw: str) -> list[dict]:
        devices: list[dict] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            posix = re.match(
                r"^(\S+)\s+\(((?:\d{1,3}\.){3}\d{1,3})\)\s+at\s+([0-9a-fA-F:]{11,17})",
                stripped,
            )
            if posix:
                hostname, ip, mac = posix.group(1), posix.group(2), posix.group(3)
                devices.append({
                    "ip": ip,
                    "mac": mac,
                    "hostname": None if hostname == "?" else hostname,
                })
                continue
            win = re.match(
                r"^((?:\d{1,3}\.){3}\d{1,3})\s+([0-9a-fA-F-]{11,17})\s+\S+",
                stripped,
            )
            if win:
                ip, mac = win.group(1), win.group(2)
                devices.append({"ip": ip, "mac": mac, "hostname": None})
                continue
        return devices

    # ------------------------------------------------------------------
    # net_ping
    # ------------------------------------------------------------------

    def _ping(self, args: dict) -> ToolResult:
        host = args.get("host")
        if not host:
            return ToolResult(content="'host' is required for net_ping", is_error=True)
        count = int(args.get("count", 4))
        flag = "-n" if self._platform_name.startswith("win") else "-c"
        argv = ["ping", flag, str(count), host]

        try:
            proc = self._run_fn(
                argv,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="ping timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"ping failed: {exc}", is_error=True)

        payload = json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        })
        return ToolResult(content=payload, is_error=proc.returncode != 0)

    # ------------------------------------------------------------------
    # net_check_port
    # ------------------------------------------------------------------

    def _check_port(self, args: dict) -> ToolResult:
        host = args.get("host")
        port = args.get("port")
        if not host:
            return ToolResult(
                content="'host' is required for net_check_port", is_error=True
            )
        if port is None:
            return ToolResult(
                content="'port' is required for net_check_port", is_error=True
            )
        timeout = float(args.get("timeout", 2.0))
        try:
            sock = self._socket_factory((host, int(port)), timeout)
        except (ConnectionRefusedError, socket.timeout, OSError):
            return ToolResult(content=json.dumps({
                "host": host,
                "port": int(port),
                "open": False,
            }))
        except Exception as exc:
            return ToolResult(
                content=f"net_check_port failed: {exc}", is_error=True
            )

        try:
            sock.close()
        except Exception:
            pass
        return ToolResult(content=json.dumps({
            "host": host,
            "port": int(port),
            "open": True,
        }))


def create() -> NetworkScannerPlugin:
    return NetworkScannerPlugin()
