"""
VPN MCP plugin — Issue #26 (Security MCP — HITL).

Tools:
  - vpn_connect(profile_name) — bring up a system VPN profile that has been
    pre-configured in the OS network settings. Felix never creates VPN
    profiles — it only triggers profiles you have already set up.
  - vpn_disconnect() — drop the active VPN connection.
  - vpn_status() — return {connected, profile?, ip?}; the public IP is
    fetched via the same ip-api.com endpoint used by
    cerebral/environment/context.py.

Platform-aware:
  Windows  → rasdial
  Darwin   → scutil --nc
  Linux    → nmcli connection

OS dispatch is parameterised by an injectable `platform_name` (defaults to
sys.platform) so the test suite covers all three branches without OS
detection or sys.platform monkey-patching.
"""
import json
import subprocess
import sys
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "vpn"

# ADR-0005 / Issue #44 — vpn_connect / vpn_disconnect change system network
# configuration (network_config). vpn_status additionally queries
# ip-api.com to report the public IP (network_egress_cloud).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "network_config",
    "network_egress_cloud",
})

_GEOLOCATION_URL = "http://ip-api.com/json/?fields=status,query"


def _default_http_get(url: str) -> dict:
    import urllib.request
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


class VpnPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        run_fn: Callable | None = None,
        platform_name: str | None = None,
        http_get: Callable[[str], dict] | None = None,
    ) -> None:
        self._run_fn = run_fn or subprocess.run
        self._platform_name = platform_name if platform_name is not None else sys.platform
        self._http_get = http_get or _default_http_get
        # Track the most recently connected profile so disconnect can target
        # it on platforms that need a profile name (macOS scutil, Linux nmcli).
        self._active_profile: str | None = None

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="vpn_connect",
                description=(
                    "Connect to a pre-configured system VPN profile. The profile "
                    "must already exist in the OS network settings — Felix never "
                    "creates VPN profiles."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "profile_name": {
                            "type": "string",
                            "description": "Name of an existing OS VPN profile.",
                        },
                    },
                    "required": ["profile_name"],
                },
            ),
            Tool(
                name="vpn_disconnect",
                description="Disconnect the active VPN connection.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="vpn_status",
                description=(
                    "Return current VPN status: {connected, profile?, ip?}. "
                    "The public IP is fetched from ip-api.com."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "vpn_connect":
            return self._connect(args)
        if tool_name == "vpn_disconnect":
            return self._disconnect()
        if tool_name == "vpn_status":
            return self._status()
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # connect / disconnect
    # ------------------------------------------------------------------

    def _connect(self, args: dict) -> ToolResult:
        profile = args.get("profile_name")
        if not profile:
            return ToolResult(
                content="'profile_name' is required for vpn_connect — "
                "Felix never auto-connects to a default profile",
                is_error=True,
            )
        argv = self._connect_argv(profile)
        if argv is None:
            return ToolResult(
                content=f"unsupported platform '{self._platform_name}' for vpn_connect",
                is_error=True,
            )
        result = self._run(argv)
        if not result.is_error:
            self._active_profile = profile
        return result

    def _disconnect(self) -> ToolResult:
        argv = self._disconnect_argv(self._active_profile)
        if argv is None:
            return ToolResult(
                content=f"unsupported platform '{self._platform_name}' for vpn_disconnect",
                is_error=True,
            )
        result = self._run(argv)
        if not result.is_error:
            self._active_profile = None
        return result

    def _status(self) -> ToolResult:
        ip = self._fetch_public_ip()
        connected = self._active_profile is not None
        payload = {
            "connected": connected,
            "profile": self._active_profile,
            "ip": ip,
        }
        return ToolResult(content=json.dumps(payload))

    # ------------------------------------------------------------------
    # platform argv builders
    # ------------------------------------------------------------------

    def _connect_argv(self, profile: str) -> list[str] | None:
        if self._platform_name.startswith("win"):
            return ["rasdial", profile]
        if self._platform_name == "darwin":
            return ["scutil", "--nc", "start", profile]
        if self._platform_name.startswith("linux"):
            return ["nmcli", "connection", "up", profile]
        return None

    def _disconnect_argv(self, profile: str | None) -> list[str] | None:
        if self._platform_name.startswith("win"):
            # rasdial disconnects all when no profile is given
            argv = ["rasdial"]
            if profile:
                argv.append(profile)
            argv.append("/disconnect")
            return argv
        if self._platform_name == "darwin":
            if not profile:
                return None
            return ["scutil", "--nc", "stop", profile]
        if self._platform_name.startswith("linux"):
            if not profile:
                return None
            return ["nmcli", "connection", "down", profile]
        return None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _run(self, argv: list[str]) -> ToolResult:
        try:
            proc = self._run_fn(
                argv,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content="vpn command timed out", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"vpn command failed: {exc}", is_error=True)

        payload = json.dumps({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        })
        return ToolResult(content=payload, is_error=proc.returncode != 0)

    def _fetch_public_ip(self) -> str | None:
        try:
            data = self._http_get(_GEOLOCATION_URL)
        except Exception:
            return None
        return data.get("query") if isinstance(data, dict) else None


def create() -> VpnPlugin:
    return VpnPlugin()
