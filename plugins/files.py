"""
Files plugin — MCP server for Felix.

Tools: create_file, read_file, move_file, delete_file, search_files.
"""
import fnmatch
import json
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "files"

# ADR-0005 / Issue #44 — read_file / search_files read; create_file writes;
# move_file removes the source and writes the destination; delete_file deletes.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "fs_read",
    "fs_write",
    "fs_delete",
})


class FilesPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="create_file",
                description="Create a file at the given path with optional text content.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative file path"},
                        "content": {"type": "string", "description": "Text content to write (default empty)"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="read_file",
                description="Read and return the text content of a file.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="move_file",
                description="Move or rename a file from src to dst.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "src": {"type": "string"},
                        "dst": {"type": "string"},
                    },
                    "required": ["src", "dst"],
                },
            ),
            Tool(
                name="delete_file",
                description="Delete a file at the given path.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="search_files",
                description="Search for files matching a glob or name pattern under a directory.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.txt'"},
                        "directory": {"type": "string", "description": "Root directory to search (default '.')"},
                    },
                    "required": ["pattern"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "create_file":
            return self._create_file(args)
        if tool_name == "read_file":
            return self._read_file(args)
        if tool_name == "move_file":
            return self._move_file(args)
        if tool_name == "delete_file":
            return self._delete_file(args)
        if tool_name == "search_files":
            return self._search_files(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _create_file(self, args: dict) -> ToolResult:
        path = Path(args["path"])
        content = args.get("content", "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(content=str(path))
        except OSError as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _read_file(self, args: dict) -> ToolResult:
        path = Path(args["path"])
        try:
            return ToolResult(content=path.read_text(encoding="utf-8"))
        except OSError as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _move_file(self, args: dict) -> ToolResult:
        src = Path(args["src"])
        dst = Path(args["dst"])
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return ToolResult(content=str(dst))
        except OSError as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _delete_file(self, args: dict) -> ToolResult:
        path = Path(args["path"])
        try:
            path.unlink()
            return ToolResult(content=str(path))
        except OSError as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _search_files(self, args: dict) -> ToolResult:
        pattern = args["pattern"]
        directory = Path(args.get("directory", "."))
        try:
            matches = [str(p) for p in directory.rglob(pattern)]
            return ToolResult(content=json.dumps(matches))
        except OSError as exc:
            return ToolResult(content=str(exc), is_error=True)


def create() -> FilesPlugin:
    return FilesPlugin()
