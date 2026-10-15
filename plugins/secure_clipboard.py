import json
import threading
from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "secure_clipboard"
REQUIRED_CAPABILITIES = frozenset({"clipboard"})


def _default_write(text: str) -> None:
    try:
        import pyperclip
        pyperclip.copy(text)
    except ImportError:
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.destroy()
        except Exception:
            pass


def _default_timer(seconds: float, fn: callable) -> threading.Timer:
    t = threading.Timer(seconds, fn)
    t.daemon = True
    t.start()
    return t


class SecureClipboardPlugin:
    name = PLUGIN_NAME

    def __init__(self, write_fn=None, timer_fn=None):
        self.write_fn = write_fn or _default_write
        self.timer_fn = timer_fn or _default_timer
        self._armed = False

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="clipboard_copy_temporarily",
                description="Copies text to clipboard and schedules a clear after a delay.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "seconds": {"type": "integer", "default": 30}
                    },
                    "required": ["text"]
                }
            ),
            Tool(
                name="clipboard_clear",
                description="Clears the clipboard immediately.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}, "required": []}
            ),
            Tool(
                name="clipboard_clear_if_needed",
                description="Clears the clipboard if it was set temporarily.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}, "required": []}
            )
        ]

    def _schedule_clear(self):
        self.write_fn("")
        self._armed = False

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "clipboard_copy_temporarily":
            text = args.get("text", "")
            seconds = args.get("seconds", 30)

            if not isinstance(text, str):
                return ToolResult(content=json.dumps({"error": "text must be a string"}), is_error=True)
            if len(text) > 1000000:
                return ToolResult(content=json.dumps({"error": "text exceeds 1,000,000 chars"}), is_error=True)
            if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1 or seconds > 600:
                return ToolResult(content=json.dumps({"error": "seconds must be an integer between 1 and 600"}), is_error=True)

            self.write_fn(text)
            self._armed = True
            self.timer_fn(seconds, self._schedule_clear)
            return ToolResult(content=json.dumps({"copied": True, "clears_in_seconds": seconds}))

        elif tool_name == "clipboard_clear":
            self.write_fn("")
            self._armed = False
            return ToolResult(content=json.dumps({"cleared": True}))

        elif tool_name == "clipboard_clear_if_needed":
            if self._armed:
                self.write_fn("")
                self._armed = False
                return ToolResult(content=json.dumps({"cleared": True}))
            return ToolResult(content=json.dumps({"cleared": False}))

        return ToolResult(content=json.dumps({"error": "Unknown tool"}), is_error=True)


def create(**kwargs) -> SecureClipboardPlugin:
    return SecureClipboardPlugin(**kwargs)
