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
    "reply with a clarifying question in plain text -- do not guess. "
    "When the user states something durable about themselves -- a preference, "
    "relationship, important date, or ongoing context -- use the propose_memory "
    "tool to suggest storing it. Never call memory_remember directly; always "
    "propose first so the user can confirm. "
    "Installed skills are reusable procedures -- call skill_list to see what is "
    "available, and skill_use(name) to load one whenever a task matches a "
    "skill's description or the user names it directly. "
    # ADR-0028 R1: the reach ladder, cheapest surface first.
    "Reach for the cheapest surface that does the job: a purpose-built tool "
    "first, then shell_exec, then a browser session, then computer_use. Each "
    "step down that list is slower and less reliable, so never use a heavier "
    "path when a lighter one reaches. If none of them reach, ask the user "
    "rather than attempting an unreliable workaround."
)


def _build_tool_catalog(tools: list[dict]) -> str:
    """Compact name+one-liner catalog of ALL tools, for the system prompt.

    The full schema list is too large for local models (~19k tokens), so only
    a shortlist of ~30 gets full schemas. But the model needs to KNOW what
    exists to pick the right one. This catalog (~15 tokens/tool) always fits.
    """
    if not tools:
        return ""
    lines = []
    for t in tools:
        name = t.get("name", "?")
        desc = (t.get("description") or "")[:80].split("\n")[0]
        lines.append(f"  - {name}: {desc}")
    return (
        "\n\nYou have access to the following tools (full schemas are provided "
        "for the most relevant ones; call any tool by name even if its full "
        "schema isn't shown):\n" + "\n".join(lines)
    )

# ADR-0014 decision 7 -- explicit skill invocation bypasses the LLM entirely:
# "/name" (typed or spoken) and the NL phrasing "use the X skill" both map
# straight to a skill_use ToolCall. Unknown/disabled names are not validated
# here -- skill_use already fails soft with a clear error (plugins/skills.py),
# so forwarding unconditionally is enough: no crash, no guessing.
_SLASH_SKILL_RE = re.compile(r"^/([\w-]+)\s*$")
_NL_SKILL_RE = re.compile(r"\buse (?:the )?(['\"]?)([\w-]+)\1 skill\b", re.IGNORECASE)

# ponytail: keyword heuristic, not a real classifier -- cheap, no extra model
# call, but blind to context ("the function of this meeting" false-positives).
# Named the ceiling on purpose: swap in a one-shot local yes/no classify if it
# misfires once the real coding endpoint is in and can be A/B'd against it.
_CODING_HINTS = re.compile(
    r"```"                                   # a fenced code block
    r"|\bdef\s+\w+\("                         # python def
    r"|\bclass\s+\w+"                         # class decl
    r"|=>|::|\bimport\s+\w"                    # arrow/scope-res/import
    r"|\b(code|coding|refactor|debug(?:ging)?|traceback|stack ?trace|"
    r"regex|compiler?|syntax|recursion|async|"
    r"pytest|unittest|npm|pip install|git (?:commit|push|rebase|merge)|"
    r"python|javascript|typescript|golang|c\+\+|bash|powershell|sql)\b",
    re.IGNORECASE,
)


def is_coding_turn(transcript: str) -> bool:
    """True when a chat turn looks like coding work (routes task_type='coding').

    Deliberately a lightweight heuristic (see _CODING_HINTS ceiling note): the
    cost of a miss is routing one turn to the wrong model, not a crash, and the
    user can always switch manually.
    """
    return bool(_CODING_HINTS.search(transcript or ""))


def resolve_skill_invocation(transcript: str) -> ToolCall | None:
    """Map an explicit skill invocation to a ``skill_use`` ToolCall, or None.

    None means "not an explicit invocation" -- the normal planner flow (LLM
    tool-calling, guided by the system prompt) decides instead.
    """
    text = transcript.strip()
    m = _SLASH_SKILL_RE.match(text)
    if m:
        return ToolCall(name="skill_use", args={"name": m.group(1)})
    m = _NL_SKILL_RE.search(text)
    if m:
        return ToolCall(name="skill_use", args={"name": m.group(2)})
    return None

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


# ADR-0016 S7 (#580): tool families for the browser stealth-vs-fast heuristic.
# When the transcript names a URL, the planner leans toward the family whose
# fingerprint fits: computer_use (OS input, no CDP) for stealth-sensitive
# hosts, Browser (Playwright DOM) for the benign default. Both plugins
# coexist -- see ADR-0016 sec 2.
_COMPUTER_USE_WEB_TOOLS: frozenset[str] = frozenset({"browser_navigate"})
_BROWSER_PLUGIN_WEB_TOOLS: frozenset[str] = frozenset({"navigate", "web_search", "read_pdf"})


def prefer_web_path(transcript: str, tools: list[dict]) -> list[dict]:
    """Reorder ``tools`` so the family that fits the transcript's URL comes
    first: stealth-sensitive -> computer_use, benign -> Browser plugin. No-op
    when no URL is detected -- keeps unaffected callers unchanged. Non-web
    tools keep their original relative order (stable sort by category)."""
    # Local import so plugin discovery order can't wedge the planner into
    # importing a plugin that isn't loaded in the current process.
    try:
        from plugins.computer_use import _URL_RE, select_web_path
    except Exception:
        return list(tools)
    if not _URL_RE.search(transcript or ""):
        return list(tools)
    prefer_cu = select_web_path(transcript) == "computer_use"

    def category(t: dict) -> int:
        name = (t.get("name") or "").lower()
        if name in _COMPUTER_USE_WEB_TOOLS:
            return 0 if prefer_cu else 2
        if name in _BROWSER_PLUGIN_WEB_TOOLS:
            return 0 if not prefer_cu else 2
        return 1  # unrelated tools sit between preferred and deprioritised
    return sorted(tools, key=category)


# A capability meta-question ("what tools do you have", "can you read files")
# has near-zero lexical overlap with the tool descriptions it's asking about --
# the lexical shortlist below would otherwise silently drop real tools from
# the answer and the model wrongly reports it lacks them. Detected up front so
# these questions skip the cap entirely instead of racing the score function.
_CAPABILITY_META_RE = re.compile(
    r"what tools|what can you do|do you have (a|access to)|"
    r"can you\b.{0,30}\bfiles?\b",
    re.I,
)


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

    ADR-0016 S7 (#580): when the transcript names a URL, tools get pre-biased
    by ``prefer_web_path`` so the stealth-vs-fast family wins ties.

    A capability meta-question bypasses the cap and returns the full registry
    unranked -- the question is asking what exists, so scoring it against
    itself is the wrong tool for the job (see _CAPABILITY_META_RE).
    """
    if _CAPABILITY_META_RE.search(transcript or ""):
        return list(tools)

    tools = prefer_web_path(transcript, list(tools))
    words = {w for w in re.findall(r"[a-z0-9]+", transcript.lower()) if len(w) >= 4}
    if not words or len(tools) <= limit:
        return list(tools)

    # ponytail: pin tools whose full name appears verbatim in the transcript
    transcript_lower = transcript.lower()
    pinned = [t for t in tools if (t.get("name") or "") in transcript_lower]
    unpinned = [t for t in tools if t not in pinned]

    def score(t: dict) -> int:
        name_words = set((t.get("name") or "").lower().split("_"))
        desc_words = set(re.findall(r"[a-z0-9]+", (t.get("description") or "").lower()))
        return 3 * len(words & name_words) + len(words & desc_words)

    ranked = sorted(unpinned, key=score, reverse=True)
    return pinned + ranked[:limit - len(pinned)]


class Planner:
    """Routes user intent to a ToolCall or text response via native tool-calling."""

    def __init__(self, backend, task_type: str | None = None) -> None:
        # task_type=None keeps today's routing exactly ("tool" for planning,
        # "chat" for finalize). A coding-chat turn passes task_type="coding" so
        # both steps route to the user's per-task pin (set_task_model). The
        # chat loop decides the classification and builds the Planner per-turn.
        self._backend = backend
        self._task_type = task_type

    async def plan(
        self,
        transcript: str,
        tools: list[dict],
        *,
        all_tools: list[dict] | None = None,
        error: str | None = None,
        prior_steps: list[dict] | None = None,
    ) -> ToolCall | str:
        """Return a ToolCall when a tool is selected, or str for text/clarification.

        Pass all_tools= (the full registry before shortlisting) to inject a
        compact catalog of every tool into the system prompt so the model
        knows what exists even when the full schema isn't sent.
        Pass error= to feed a prior validation failure back to the model for
        a one-shot self-correction attempt (ADR-0008 bounded self-correction).
        Pass prior_steps= (S2 chaining) to include accumulated tool call history
        so the model can pick the next action or return a final summary.
        Each entry: {"name": str, "args": dict, "result": str, "is_error": bool}.
        """
        # ADR-0014 -- explicit "/name" or "use the X skill" invocation skips
        # the LLM entirely. Only on the first step of a chain: once a chain
        # is already underway (prior_steps/error set), the transcript is the
        # original request replayed for a retry/continue decision, not a
        # fresh explicit invocation.
        if not prior_steps and not error:
            skill_call = resolve_skill_invocation(transcript)
            if skill_call is not None:
                return skill_call

        catalog = _build_tool_catalog(all_tools or tools)
        parts = [_SYSTEM_PROMPT + catalog, f"\nUser: {transcript}"]

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
        if self._task_type:
            return await self._backend.complete_with_tools(
                prompt, tools, task_type=self._task_type
            )
        return await self._backend.complete_with_tools(prompt, tools)

    async def finalize(self, transcript: str, prior_steps: list[dict]) -> str:
        """Force a natural-language answer from tool results, WITHOUT tools.

        Tool-native models (Qwen, Hermes, …) tend to keep emitting tool calls
        while tools are offered, re-calling a tool they already ran instead of
        answering — because the chain feeds history as prompt text, not as
        structured tool-result messages. When the chain detects that loop (a
        repeated call, or the step cap), it calls this: a plain text-only
        completion the model cannot answer with another tool call.
        """
        lines = []
        for s in prior_steps:
            res = f"ERROR: {s['result']}" if s.get("is_error") else s["result"]
            lines.append(f"{s['name']} -> {res}")
        prompt = (
            f"{_SYSTEM_PROMPT}\nUser: {transcript}\n\n"
            "You already ran these tools and got these results:\n"
            + "\n".join(lines)
            + "\n\nAnswer the user now in one or two natural sentences using "
            "those results. Do NOT call a tool."
        )
        return await self._backend.complete(prompt, task_type=self._task_type or "chat")
