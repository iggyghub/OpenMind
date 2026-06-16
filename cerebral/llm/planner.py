"""
Planner -- single-step intent-to-tool engine (Issue #274, S1).

Takes the user's natural-language transcript and the list of available tools
from the orchestrator, calls the active backend with native tool-calling, and
returns either a ToolCall (LLM chose a tool) or str (LLM answered in text or
asked a clarifying question).

The planner is the sole arbiter of conversation-vs-action: one call decides
both. A system-prompt instruction tells the model to ask a clarifying question
(return text) when it cannot confidently select a tool.
"""

from cerebral.llm.router import ToolCall

_SYSTEM_PROMPT = (
    "You are Felix, a personal AI assistant with access to tools. "
    "When a user request can be fulfilled by one of your tools, use it. "
    "When you are unsure which tool applies or need more information, "
    "reply with a clarifying question in plain text -- do not guess."
)

_TYPE_MAP: dict[str, type | tuple] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_tool_args(
    tool_name: str, args: dict, tools: list[dict]
) -> str | None:
    """Lightweight arg validation against the tool's input_schema.

    Returns an error message string on failure, or None when valid.
    Checks required fields and top-level types only -- no jsonschema dependency.
    """
    schema = next((t["input_schema"] for t in tools if t["name"] == tool_name), None)
    if not schema:
        return None

    required: list[str] = schema.get("required") or []
    properties: dict = schema.get("properties") or {}

    for field in required:
        if field not in args:
            return f"Missing required argument '{field}' for tool '{tool_name}'"

    for field, value in args.items():
        if field in properties:
            expected = properties[field].get("type")
            py_type = _TYPE_MAP.get(expected)
            if py_type and not isinstance(value, py_type):
                return (
                    f"Argument '{field}' for tool '{tool_name}' must be {expected}, "
                    f"got {type(value).__name__}"
                )

    return None


class Planner:
    """Routes user intent to a ToolCall or text response via native tool-calling."""

    def __init__(self, backend) -> None:
        self._backend = backend

    async def plan(
        self,
        transcript: str,
        tools: list[dict],
        *,
        error: str | None = None,
    ) -> ToolCall | str:
        """Return a ToolCall when a tool is selected, or str for text/clarification.

        Pass error= to feed a prior validation failure back to the model for
        a one-shot self-correction attempt.
        """
        if error:
            prompt = (
                f"{_SYSTEM_PROMPT}\n\n"
                f"User: {transcript}\n\n"
                f"Note: the previous tool call failed validation: {error}. "
                f"Please retry with correct arguments."
            )
        else:
            prompt = f"{_SYSTEM_PROMPT}\n\nUser: {transcript}"
        return await self._backend.complete_with_tools(prompt, tools)
