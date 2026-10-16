import json
import platform
import re
import socket
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "app_info"
REQUIRED_CAPABILITIES = frozenset({"fs_read", "network_recon"})


def create(**kwargs):
    return AppInfoPlugin(**kwargs)


class AppInfoPlugin:
    name = PLUGIN_NAME

    def __init__(self, root=None, theme_read=None, resolver=None):
        self.root = root or Path(__file__).resolve().parent.parent
        self._theme_read_fn = theme_read
        self.resolver = resolver or socket.getaddrinfo
        self._theme_cache = None

    def _get_theme(self):
        # Read every time: the user can switch light/dark while Felix runs.
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(val)
        except Exception:
            return None

    def _resolve_theme(self):
        if self._theme_read_fn is not None:
            return self._theme_read_fn()
        return self._get_theme()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="app_config",
                description="Returns application configuration info",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}, "required": []}
            ),
            Tool(
                name="user_data_path",
                description="Returns the user data path",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}, "required": []}
            ),
            Tool(
                name="os_theme",
                description="Returns the current OS theme preference",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}, "required": []}
            ),
            Tool(
                name="resolve_host",
                description="Resolves a hostname to IP addresses",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "host": {"type": "string"},
                        "family": {"type": "string", "enum": ["ipv4", "ipv6", "any"]}
                    },
                    "required": ["host"]
                }
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        try:
            if tool_name == "app_config":
                return ToolResult(content=json.dumps(self.app_config()))
            elif tool_name == "user_data_path":
                return ToolResult(content=json.dumps(self.user_data_path()))
            elif tool_name == "os_theme":
                return ToolResult(content=json.dumps(self.os_theme()))
            elif tool_name == "resolve_host":
                result = self.resolve_host(args.get("host", ""), args.get("family", "any"))
                # Flag failures on the ToolResult too, or the planner reads them as success.
                return ToolResult(content=json.dumps(result), is_error=bool(result.get("is_error")))
            else:
                return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)
        except Exception as e:
            return ToolResult(content=str(e), is_error=True)

    def app_config(self):
        commit = "unknown"
        head_path = self.root / ".git" / "HEAD"
        try:
            head = head_path.read_text().strip()
            if head.startswith("ref: "):
                ref = head.replace("ref: ", "")
                ref_path = self.root / ".git" / ref.replace("refs/", "")
                commit = ref_path.read_text().strip()
        except Exception:
            pass

        return {
            "name": "Felix",
            "commit": commit,
            "platform": platform.platform(),
            "os_class": platform.system().lower(),
            "python": platform.python_version(),
            "user_data_path": str(self.root / "cerebral" / "data")
        }

    def user_data_path(self):
        return {"path": str(self.root / "cerebral" / "data")}

    def os_theme(self):
        val = self._resolve_theme()
        if val == 0:
            return {"dark": True}
        elif val == 1:
            return {"dark": False}
        else:
            return {"dark": None}

    def resolve_host(self, host: str, family: str = "any") -> dict:
        if not re.match(r"^[A-Za-z0-9.-]{1,253}$", host) or host.startswith("-"):
            return {"is_error": True, "error": "Invalid host format"}

        family_map = {
            "ipv4": socket.AF_INET,
            "ipv6": socket.AF_INET6,
            "any": socket.AF_UNSPEC
        }
        sock_type = family_map.get(family, socket.AF_UNSPEC)

        try:
            results = self.resolver(host, 0, sock_type)
            addresses = list({addr[4][0] for addr in results})
            addresses.sort()
            return {"host": host, "addresses": addresses}
        except Exception as e:
            return {"is_error": True, "error": str(e)}
