"""
Documents MCP plugin -- Documents campaign ADR-0011, issues #452-#457 / #448.

Tools: doc_status (S2), [more in S3+]

S2 (#453): find_soffice() discovery helper + doc_status tool.

All soffice discovery is injectable via set_find_soffice_fn for tests.
Never invoke a real soffice.exe from within this module at import time or in tests.
"""
import json
import logging
import shutil
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "documents"

# ADR-0005: doc_status is pure local introspection (fs_read).
# Later slices add fs_write (convert/edit) and device_control (Writer launch).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read"})

_SOFFICE_DEFAULT_DIRS: tuple[Path, ...] = (
    Path("C:/Program Files/LibreOffice/program"),
    Path("C:/Program Files (x86)/LibreOffice/program"),
)

# Injectable seam: tests replace this to avoid real filesystem/process calls.
_find_soffice_fn = None


def set_find_soffice_fn(fn) -> None:
    global _find_soffice_fn
    _find_soffice_fn = fn


def find_soffice(_dirs=None) -> "Path | None":
    """Locate soffice.exe in standard install dirs then PATH."""
    dirs = _dirs if _dirs is not None else _SOFFICE_DEFAULT_DIRS
    for d in dirs:
        p = d / "soffice.exe"
        if p.exists():
            return p
    found = shutil.which("soffice")
    return Path(found) if found else None


_INSTALL_MSG = (
    "LibreOffice not found. Run scripts/setup-libreoffice.ps1 to install it."
)


class DocumentsPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list:
        return [
            Tool(
                name="doc_status",
                description=(
                    "Returns LibreOffice availability. available=true when soffice.exe "
                    "is found; false with an install hint."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "doc_status":
            return self._doc_status()
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _doc_status(self) -> ToolResult:
        finder = _find_soffice_fn if _find_soffice_fn is not None else find_soffice
        path = finder()
        if path:
            return ToolResult(content=json.dumps({
                "available": True,
                "soffice_path": str(path),
            }))
        return ToolResult(content=json.dumps({
            "available": False,
            "message": _INSTALL_MSG,
        }))


def create() -> DocumentsPlugin:
    return DocumentsPlugin()
