import json
import re
import subprocess
from typing import Any, Dict, List, Optional

_NAME_RE = re.compile(r"^[@a-z0-9._/-]+$")
_ALLOWED_MANAGERS = {"npm", "pip", "winget"}


class PackageManagerPlugin:
    def __init__(self, run_fn=None):
        self.run_fn = run_fn or subprocess.run

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {"name": "pkg_install", "description": "Install a package"},
            {"name": "pkg_update", "description": "Update a package"},
            {"name": "pkg_search", "description": "Search for a package"},
        ]

    async def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        manager = params.get("manager")
        if not manager:
            return self._error("Missing 'manager' parameter")
        if manager not in _ALLOWED_MANAGERS:
            return self._error(f"Unsupported manager: {manager}. Allowed: {', '.join(sorted(_ALLOWED_MANAGERS))}")

        if tool_name == "pkg_install":
            name = params.get("name")
            if not name:
                return self._error("Missing 'name' parameter for pkg_install")
            if not _NAME_RE.match(name):
                return self._error(f"Invalid package name: {name}")
            global_flag = params.get("global", False)
            if global_flag and manager != "npm":
                return self._error(f"'global' is only supported for npm, not {manager}")
            return self._install(manager, name, global_flag)

        elif tool_name == "pkg_update":
            global_flag = params.get("global", False)
            if global_flag and manager != "npm":
                return self._error(f"'global' is only supported for npm, not {manager}")
            return self._update(manager, params.get("name"), global_flag)

        elif tool_name == "pkg_search":
            query = params.get("query")
            if not query:
                return self._error("Missing 'query' parameter for pkg_search")
            return self._search(manager, query)

        return self._error(f"Unknown tool: {tool_name}")

    def _install(self, manager: str, name: str, global_flag: bool) -> Dict[str, Any]:
        if manager == "npm":
            argv = ["npm", "install"]
            if global_flag:
                argv.append("--global")
            argv.append(name)
        elif manager == "pip":
            argv = ["pip", "install", name]
        elif manager == "winget":
            argv = ["winget", "install", name]
        return self._run(argv)

    def _update(self, manager: str, name: Optional[str], global_flag: bool) -> Dict[str, Any]:
        if manager == "npm":
            argv = ["npm", "update"]
            if global_flag:
                argv.append("--global")
            if name:
                argv.append(name)
        elif manager == "pip":
            argv = ["pip", "install", "-U", name] if name else ["pip", "install", "-U"]
        elif manager == "winget":
            argv = ["winget", "update", name] if name else ["winget", "update"]
        return self._run(argv)

    def _search(self, manager: str, query: str) -> Dict[str, Any]:
        if manager == "npm":
            argv = ["npm", "search", query]
        elif manager == "pip":
            argv = ["pip", "index", "versions", query]
        elif manager == "winget":
            argv = ["winget", "search", query]
        return self._run(argv)

    def _run(self, argv: List[str]) -> Dict[str, Any]:
        try:
            proc = self.run_fn(argv, capture_output=True, text=True)
            return {
                "is_error": proc.returncode != 0,
                "content": json.dumps({
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "exit_code": proc.returncode
                })
            }
        except Exception as e:
            return self._error(str(e))

    @staticmethod
    def _error(msg: str) -> Dict[str, Any]:
        return {"is_error": True, "content": json.dumps({"error": msg})}


def create() -> PackageManagerPlugin:
    return PackageManagerPlugin()
