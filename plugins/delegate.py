"""
Delegate plugin -- planner-autonomy wrapper around run_subagent (ADR-0020 S4,
Issue #730).

HITL slice: this is the first time the planner itself is handed a `delegate`
tool. Every earlier slice (S1-S3) was caller-invoked only (ADR-0020 decision
3) -- a hand-picked internal flow called run_subagent directly. This plugin
crosses that line: any turn the planner is choosing tools for can now decide
on its own to spin off a sub-agent. That is exactly the "new autonomous
capability" category that requires human review before merge (see
DELEGATION-BUILD.md's HITL marking on this slice).

REQUIRED_CAPABILITIES -- declared as the FULL 16-class vocabulary, not a
narrow guess. `delegate` is a meta-tool: the sub-run it spins up can invoke
ANY tool the caller's own gate would allow (ADR-0020 decision 7 -- "same
orchestrator + gate + ACL for every nested step"), scoped only by the
`tools` argument the caller supplies (or the full shortlist when omitted).
Each nested tool call is re-gated independently at execution time, so
under-declaring here would not weaken enforcement -- but it WOULD make the
Plugins UI's capability badge lie about what this tool can transitively
reach. Declaring the closed vocabulary in full is the honest answer for a
tool whose entire purpose is "run more tools."

No-nesting: `run_subagent` already strips `delegate` out of a sub-agent's
own tool set (ADR-0020 decision 9, cerebral/llm/subagent.py) -- this plugin
does not re-implement that guard, only relies on it. Never add "delegate" to
a `tools=[...]` scoping list passed into a sub-run.

Wiring seam: this module must never `import cerebral.main` -- plugins are
loaded by the orchestrator via spec_from_file_location as
`openmind_plugin_delegate`, a SEPARATE module instance from anything
`import plugins.delegate` would get (Issue #153). Instead it exposes
`set_subagent_context(context_fn)`, a single module-level setter matching
the `set_memory_factory` / `set_settings_store` seam convention already used
by plugins/skills.py and plugins/builder.py. `context_fn` is a zero-arg
callable, called FRESH on every `delegate()` invocation (never cached) so
the tool set reflects the orchestrator's current registry -- e.g. a plugin
`builder_create` registered after Cerebral started. Until main.py wires it,
`delegate` fails closed with a clear error (mirrors plugins/builder.py's
`_ParkedBuilderPlugin`).
"""
from __future__ import annotations

from typing import Callable

from cerebral.llm.subagent import run_subagent
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.security import CAPABILITY_VOCABULARY

PLUGIN_NAME = "delegate"

REQUIRED_CAPABILITIES: frozenset[str] = CAPABILITY_VOCABULARY

# main.py wires (ADR-0020 S4): set_subagent_context(lambda: {
#     "router": _router, "gate_fn": _gate_tool,
#     "execute_fn": _orc.call_tool, "all_tools": _orc.tools_for_llm,
# })
_context_fn: "Callable[[], dict | None] | None" = None


def set_subagent_context(context_fn) -> None:
    """Inject the zero-arg context factory main.py wires (ADR-0020 S4).

    Called fresh on every delegate() call -- see module docstring.
    """
    global _context_fn
    _context_fn = context_fn


class DelegatePlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="delegate",
                description=(
                    "Run a bounded subtask in its own fresh context (a "
                    "sub-agent) and get back ONE compact result -- the "
                    "sub-run's intermediate tool calls never enter this "
                    "conversation. Use for a heavy, self-contained piece of "
                    "work (e.g. 'research X and summarise it') that would "
                    "otherwise bloat this turn with many tool calls. Do NOT "
                    "use this for a single simple tool call -- call that "
                    "tool directly instead."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The subtask for the sub-agent to complete.",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional allow-list of tool names to scope the "
                                "sub-agent to. Prefer a short explicit list when "
                                "you know which tools the subtask needs -- omit "
                                "to let it shortlist from the full registry."
                            ),
                        },
                        "model": {
                            "type": "string",
                            "description": (
                                "Optional model id to pin for this sub-run (e.g. "
                                "route a heavy subtask to cloud). Omit to use "
                                "the active model."
                            ),
                        },
                    },
                    "required": ["task"],
                },
                required_capabilities=REQUIRED_CAPABILITIES,
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name != "delegate":
            return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)
        return await self._delegate(args or {})

    async def _delegate(self, args: dict) -> ToolResult:
        task = str(args.get("task", "")).strip()
        if not task:
            return ToolResult(content="task is required", is_error=True)

        if _context_fn is None:
            return ToolResult(
                content=(
                    "delegate is not wired -- main.py must call "
                    "set_subagent_context() during startup (ADR-0020 S4)."
                ),
                is_error=True,
            )
        ctx = _context_fn()
        if not ctx:
            return ToolResult(
                content=(
                    "delegate is not wired -- set_subagent_context()'s factory "
                    "returned no context (ADR-0020 S4)."
                ),
                is_error=True,
            )

        tools = args.get("tools")
        if tools is not None:
            tools = [str(t) for t in tools]
        model = args.get("model")
        model = str(model) if model else None

        return await run_subagent(
            task,
            router=ctx["router"],
            gate_fn=ctx["gate_fn"],
            execute_fn=ctx["execute_fn"],
            all_tools=ctx["all_tools"],
            tools=tools,
            model=model,
        )


def create() -> DelegatePlugin:
    return DelegatePlugin()
