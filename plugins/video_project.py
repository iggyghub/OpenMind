"""
Video project MCP plugin -- ADR-0041, driver VIDEO-EDIT.md.

Tools: video_status (V1).

V1: find_shotcut() / find_melt() discovery + video_status. Shotcut ships melt.exe
next to shotcut.exe, so one install is both the editor the user opens and the
headless engine Felix renders with.

Shotcut discovery is injectable via set_find_shotcut_fn for tests.
Never invoke a real shotcut.exe or melt.exe from within this module at import time or in tests.
"""
import json
import shutil
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "video_project"

# ADR-0005: video_status is pure local introspection (fs_read).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read"})

# winget's default scope is per-user (%LOCALAPPDATA%/Programs); melt runs unsandboxed
# (ADR-0041 decision 3), so a per-user install works as well as a machine-wide one.
_SHOTCUT_DEFAULT_DIRS: tuple[Path, ...] = (
    Path("C:/Program Files/Shotcut"),
    Path("C:/Program Files (x86)/Shotcut"),
    Path.home() / "AppData/Local/Programs/Shotcut",
)

# Injectable seam: tests replace this to avoid real filesystem/process calls.
_find_shotcut_fn = None


def set_find_shotcut_fn(fn) -> None:
    global _find_shotcut_fn
    _find_shotcut_fn = fn


def find_shotcut(_dirs=None) -> "Path | None":
    """Locate shotcut.exe in standard install dirs then PATH."""
    dirs = _dirs if _dirs is not None else _SHOTCUT_DEFAULT_DIRS
    for d in dirs:
        p = d / "shotcut.exe"
        if p.exists():
            return p
    found = shutil.which("shotcut")
    return Path(found) if found else None


def find_melt(shotcut: Path) -> "Path | None":
    """melt.exe ships in the same folder as shotcut.exe."""
    p = shotcut.parent / "melt.exe"
    return p if p.exists() else None


_INSTALL_MSG = (
    "Shotcut not found. Run scripts/setup-shotcut.ps1 to install it "
    "(it also provides melt, the engine Felix renders video projects with)."
)


class VideoProjectPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list:
        return [
            Tool(
                name="video_status",
                description=(
                    "Returns whether Shotcut (the video editor) and its melt render "
                    "engine are installed. available=true when both are found; "
                    "false with an install hint."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "video_status":
            return self._status()
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _status(self) -> ToolResult:
        finder = _find_shotcut_fn if _find_shotcut_fn is not None else find_shotcut
        shotcut = finder()
        melt = find_melt(shotcut) if shotcut else None
        payload = {
            "available": bool(shotcut and melt),
            "shotcut": str(shotcut) if shotcut else None,
            "melt": str(melt) if melt else None,
        }
        if not payload["available"]:
            payload["hint"] = _INSTALL_MSG
        return ToolResult(content=json.dumps(payload))


def create() -> VideoProjectPlugin:
    return VideoProjectPlugin()
