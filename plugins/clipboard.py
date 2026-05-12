"""
Clipboard plugin — MCP server for Felix.

Tools: read_clipboard, write_clipboard, list_clipboard_history.

History is kept in-process RAM only (never written to disk).
Uses pyperclip for system clipboard access; falls back to tkinter if unavailable.
"""
import json
from collections import deque
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "clipboard"
_HISTORY_LIMIT = 50

# ADR-0005 / Issue #44 — read_clipboard / write_clipboard / list_history all
# touch the system clipboard. The history buffer lives only in RAM.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"clipboard"})


def _make_default_backend():
    """Return (read_fn, write_fn) using pyperclip, or tkinter fallback."""
    try:
        import pyperclip
        return pyperclip.paste, pyperclip.copy
    except ImportError:
        pass
    try:
        import tkinter as tk
        def _tk_read():
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text
        def _tk_write(text: str):
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
        return _tk_read, _tk_write
    except Exception:
        pass
    # Final no-op fallback
    _mem = {"text": ""}
    return lambda: _mem["text"], lambda t: _mem.update({"text": t})


class ClipboardPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        read_fn: Callable | None = None,
        write_fn: Callable | None = None,
    ) -> None:
        if read_fn is None or write_fn is None:
            _r, _w = _make_default_backend()
            self._read_fn = read_fn or _r
            self._write_fn = write_fn or _w
        else:
            self._read_fn = read_fn
            self._write_fn = write_fn
        self._history: deque[str] = deque(maxlen=_HISTORY_LIMIT)

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="read_clipboard",
                description="Return the current clipboard text content.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="write_clipboard",
                description="Write text to the clipboard.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to place on the clipboard"},
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="list_clipboard_history",
                description="Return the in-process clipboard write history (newest first, RAM only).",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "read_clipboard":
            return self._read_clipboard()
        if tool_name == "write_clipboard":
            return self._write_clipboard(args)
        if tool_name == "list_clipboard_history":
            return ToolResult(content=json.dumps({"history": list(self._history)}))
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _read_clipboard(self) -> ToolResult:
        try:
            text = self._read_fn()
            return ToolResult(content=text or "")
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)

    def _write_clipboard(self, args: dict) -> ToolResult:
        text = args.get("text", "")
        try:
            self._write_fn(text)
            self._history.appendleft(text)
            return ToolResult(content=json.dumps({"ok": True}))
        except Exception as exc:
            return ToolResult(content=str(exc), is_error=True)


def create() -> ClipboardPlugin:
    return ClipboardPlugin()
