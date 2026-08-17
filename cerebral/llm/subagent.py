"""Sub-agent delegation -- run a chain inside its own context boundary (ADR-0020).

A sub-agent is NOT a parallel worker (single 8 GB GPU serializes local
inference); it is a context boundary. run_subagent runs a bounded subtask on
a fresh transcript with a scoped tool subset, reusing the caller's gate +
executor + router, and returns ONE compact result. The parent's token budget
stays clean: the sub-chain's intermediate steps never flow up, and (via the
no-op record_fn) never touch the conversation store.

v1 is caller-invoked only -- the planner is NOT handed a delegate tool yet;
that autonomy slice comes behind the eval harness (ADR-0020 S4). Sequential/
blocking by design: parallel local would thrash the one GPU.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

from cerebral.llm.chain_engine import MAX_CHAIN_STEPS, ChainEngine
from cerebral.llm.planner import Planner, shortlist_tools
from cerebral.llm.router import ModelRouter
from cerebral.llm.step_ledger import StepLedger
from cerebral.mcp.orchestrator import ToolResult


@runtime_checkable
class SubagentProvider(Protocol):
    """A provider that runs a task as an isolated sub-agent (ADR-0020
    amendment 2026-08-15, "subagent as a provider seam", decision 1).

    One interface, providers vary: this slice ships only the fork-in-process
    provider (`run_subagent` below, which already satisfies this protocol
    unchanged -- its signature IS `SubagentProvider.__call__`). A
    cloud-delegated provider (model pin) and a future out-of-process provider
    (delegate to another agent) slot in behind the same call shape later,
    mirroring `Backend` in cerebral/llm/router.py and the plugin chain's
    `TokenProvider`.
    """

    async def __call__(
        self,
        task: str,
        *,
        router: ModelRouter,
        gate_fn: Callable[[str, dict], Awaitable[object]],
        execute_fn: Callable[[str, dict], Awaitable[ToolResult]],
        all_tools: list[dict],
        tools: "list[str] | None" = None,
        context: "str | None" = None,
        model: "str | None" = None,
        max_steps: int = MAX_CHAIN_STEPS,
        run_id: "str | None" = None,
        ledger: "StepLedger | None" = None,
    ) -> ToolResult: ...


async def _no_record(kind: str, content: dict) -> None:
    """Isolation: sub-agent steps do not touch the parent conversation store."""
    return None


async def run_subagent(
    task: str,
    *,
    router: ModelRouter,
    gate_fn: Callable[[str, dict], Awaitable[object]],
    execute_fn: Callable[[str, dict], Awaitable[ToolResult]],
    all_tools: list[dict],
    tools: "list[str] | None" = None,
    context: "str | None" = None,
    model: "str | None" = None,
    max_steps: int = MAX_CHAIN_STEPS,
    run_id: "str | None" = None,
    ledger: "StepLedger | None" = None,
) -> ToolResult:
    """Run task as an isolated sub-agent and return its compact result.

    router / gate_fn / execute_fn -- reuse the parent loop machinery so the
        sub-agent routes, gates (ADR-0005), and executes exactly as the parent
        would. A sub-agent therefore can never exceed the caller's permissions.
    all_tools -- full tool registry (e.g. orchestrator.tools_for_llm).
    tools -- allow-list of tool names to scope the sub-planner to; None
        shortlists the full registry against the task (helps the 8B).
    context -- optional context baked into the transcript; absent => fresh
        (task string only), which is the isolation.
    model -- optional model id to pin for this run (e.g. route to cloud);
        None uses the router's active model.
    run_id / ledger -- crash-resume passthrough (harness improvement C). When
        both are given, a sub-agent re-invoked with the same run_id after a
        crash replays already-completed steps from the ledger instead of
        re-executing them; the resume behavior is StepLedger's, this is only
        the pass-through. Both absent => byte-identical to S1.
    """
    transcript = f"{context}\n\n{task}" if context else task

    if tools is None:
        tool_defs = shortlist_tools(task, all_tools)
    else:
        wanted = set(tools)
        tool_defs = [t for t in all_tools if t.get("name") in wanted]

    # No nesting (ADR-0020 decision 9): a sub-agent never gets delegate, so it
    # cannot spawn a sub-sub-agent. Forward-compatible with the S4 plugin.
    tool_defs = [t for t in tool_defs if t.get("name") != "delegate"]

    chain = ChainEngine(
        planner=Planner(router),
        gate_fn=gate_fn,
        execute_fn=execute_fn,
        record_fn=_no_record,
    )

    # Pin the model for the sub-run, restoring the parent's afterwards. Safe
    # because delegation is sequential (ADR-0020 decision 5) -- no concurrent
    # run to disturb.
    # ponytail: switch_model mutates shared router priority; fine while
    # sequential, revisit if parallel cloud fan-out (S5) ever lands.
    prev_model = router.active_model if model else None
    if model:
        try:
            router.switch_model(model)
        except ValueError:
            prev_model = None  # unknown model id; run on active instead

    try:
        # No on_chain_done: sub-agents don't raise recipe offers (isolation).
        text = await chain.run(
            transcript, tool_defs, max_steps=max_steps, run_id=run_id, ledger=ledger
        )
    finally:
        if prev_model is not None:
            try:
                router.switch_model(prev_model)
            except ValueError:
                pass

    return ToolResult(content=text, is_error=False)


# The fork-in-process provider (ADR-0020 amendment, decision 1). run_subagent
# already IS the local provider -- no wrapper class, no signature change; the
# binding just gives callers a name typed as SubagentProvider to code against.
# ponytail: one provider in this slice by design (issue #768). The cloud-pinned
# variant is already expressible via run_subagent's existing `model=` kwarg --
# add a second provider class only when an out-of-process target exists to
# drive (e.g. delegating to another agent), not speculatively.
local_provider: SubagentProvider = run_subagent
