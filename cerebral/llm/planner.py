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

import re

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


def shortlist_tools(
    transcript: str, tools: list[dict], limit: int = 30
) -> list[dict]:
    """Rank tools by lexical overlap with the transcript; keep the top ``limit``.

    The full registry (~200 tool schemas) serialises to ~19k tokens — past the
    local model's context window, so sending everything makes Ollama silently
    truncate the payload and the model "loses" its tools (it then replies that
    it has no such access). A small, relevant subset keeps native tool-calling
    inside the window.

    Words shorter than 4 chars are ignored (drops "my/as/the" noise); a word
    matching the tool *name* counts triple. Ties keep registration order
    (sorted() is stable).
    ponytail: lexical overlap; upgrade to embedding recall if misses show up.
    """
    words = {w for w in re.findall(r"[a-z0-9]+", transcript.lower()) if len(w) >= 4}
    if not words or len(tools) <= limit:
        return list(tools)

    def score(t: dict) -> int:
        name_words = set((t.get("name") or "").lower().split("_"))
        desc_words = set(re.findall(r"[a-z0-9]+", (t.get("description") or "").lower()))
        return 3 * len(words & name_words) + len(words & desc_words)

    return sorted(tools, key=score, reverse=True)[:limit]


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
        prior_steps: list[dict] | None = None,
    ) -> ToolCall | str:
        """Return a ToolCall when a tool is selected, or str for text/clarification.

        Pass error= to feed a prior validation failure back to the model for
        a one-shot self-correction attempt (ADR-0008 bounded self-correction).
        Pass prior_steps= (S2 chaining) to include accumulated tool call history
        so the model can pick the next action or return a final summary.
        Each entry: {"name": str, "args": dict, "result": str, "is_error": bool}.
        """
        parts = [_SYSTEM_PROMPT, f"\nUser: {transcript}"]

        if prior_steps:
            lines = []
            for i, s in enumerate(prior_steps, 1):
                res = f"ERROR: {s['result']}" if s.get("is_error") else s["result"]
                lines.append(f"Step {i}: {s['name']} -> {res}")
            parts.append(
                "\nPrevious steps:\n"
                + "\n".join(lines)
                + "\nWhat should I do next? Use a tool to continue, or reply with a summary if done."
            )

        if error:
            parts.append(
                f"\nNote: the previous tool call failed validation: {error}. "
                "Please retry with correct arguments."
            )

        prompt = "\n".join(parts)
        return await self._backend.complete_with_tools(prompt, tools)
