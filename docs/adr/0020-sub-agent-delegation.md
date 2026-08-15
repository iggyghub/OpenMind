# ADR-0020: Sub-agent delegation is a context boundary, not a parallel swarm

**Date:** 2026-08-15
**Status:** Accepted (grill session)
**Extends:** ADR-0008 (core loop / tool selection), ADR-0005 (security gate).
**Relates:** `cerebral/llm/step_ledger.py` (crash-resume, harness improvement C).

## Context

"Sub-agent delegation" was the most-corroborated idea in the 10-repo harness
extraction, and was parked pending this grill. The corroboration is a **sampling
artifact** — agent-orchestration frameworks are over-represented in the sampled
repos, and they all sell the same thing: a supervisor that fans work out to
**parallel** sub-agents.

That frame does not pay off on this hardware. Cerebral runs local inference on a
single 8 GB GTX 1080. Two local qwen sub-agents do not run twice as fast — they
serialize on the one GPU. The headline payoff of the swarm frame is throughput,
and throughput is exactly what a single GPU cannot give.

Two payoffs *do* survive the single-GPU constraint, and they are the real reason
to build delegation:

1. **Context isolation** — run a bounded subtask with its own fresh context so
   the parent turn's token budget doesn't bloat, and the parent sees one compact
   result instead of N intermediate steps. This is about *tokens*, not throughput.
2. **Optional cloud offload** — a subtask can be pinned to Budd/Claude (not
   GPU-bound), so a caller can spend a cloud hop on a heavy sub-run without
   stalling the local box.

The core loop today (ADR-0008) is `Planner → ChainEngine → orchestrator.call_tool`
through the ADR-0005 gate. There is **no** existing sub-agent concept:
`cerebral/session_worker.py` is an ADR-0016 UI *actuator* over WS, Recipes are
saved chains, Skills are installable procedures. Delegation is genuinely new.

## Decisions (from grill)

1. **A sub-agent is a context boundary, not a parallel worker.** The whole design
   is reframed around token isolation + optional cloud routing. The swarm/throughput
   frame is **rejected** — it is the one payoff a single GPU cannot cash.

2. **Delegation-as-a-function that reuses the existing loop, not a new orchestrator
   layer.** A sub-agent is "a chain run with its own context and tool subset." It
   rides the existing `Planner` + `ChainEngine` + `ModelRouter` + orchestrator gate.
   No supervisor above `ChainEngine`, no second routing or gating path.

3. **Caller-invoked infra-first; planner autonomy is a later slice.** v1 does NOT
   expose a `delegate` tool to the planner. Only explicit internal callers invoke
   the primitive. Autonomous decomposition (the planner reaching for `delegate`
   itself) is a distinct behavior that must be measured behind the eval harness
   (B, `cerebral/eval/`) before it ships — the 8B already over-emits and mis-args,
   and an autonomous delegate it mis-drives would be discovered live otherwise.

4. **Reuse the shared `ModelRouter` with an optional `model=` pin.** Default: the
   sub-agent uses the router's active model (local-first, same as the parent). A
   caller may pin `model=` to route a subtask to cloud. One routing path; the
   `model=` knob is the per-call calibration the single-GPU box needs. Local-first
   stays intact — cloud is opt-in, never baked in (Budd throws 504s).

5. **Sequential only in v1.** `delegate` is a blocking `await` that runs one
   sub-chain to completion and returns. Local runs serialize on the GPU *by
   construction* (nothing to contend over); cloud runs just work. Parallel fan-out,
   if it ever earns its place, is a later cloud-only `asyncio.gather` slice, guarded
   and eval'd — never parallel local.

6. **Fresh context by default, optional explicit `context`.** The sub-agent starts
   with only its `task` string — no parent conversation history, no memory preamble.
   The caller bakes any needed context into `task`/`context` explicitly, which
   forces the boundary to be deliberate. A blanket memory-inherit is a later knob,
   YAGNI until a real caller needs it. Fresh-by-default *is* the isolation.

7. **Same orchestrator + ADR-0005 gate + profile ACL for every nested step.** A
   sub-agent cannot exceed the caller's permissions — security is preserved for free
   by reusing the gate. Tool scoping (below) is about focus/token budget, not security.

8. **Scoped tools: `tools: list[str] | None`.** None → shortlist the full registry
   against the task (as the parent turn does). A list → restrict the sub-planner to
   exactly those tool names. A small, focused tool set is where the 8B picks tools
   most reliably — a concrete reason to delegate at all. Gate always applies on top.

9. **Return boundary, no nesting, reuse the step cap.**
   - Returns `ToolResult(content=<sub-agent final text>, is_error)` — nothing else;
     the sub-chain's steps do not flow back up. Isolation enforced at the return.
   - **No nesting in v1**: a sub-agent's tool set excludes `delegate`, so it cannot
     spawn a sub-sub-agent. Removes the only real runaway-cost risk for free.
   - Step cap reuses `ChainEngine`'s existing `MAX_CHAIN_STEPS` (8), caller-overridable
     via the existing `max_steps` kwarg. No new cap system.

10. **v1 is a plain function, not a plugin.** The *frame* is "delegation-as-a-tool,"
    but a plugin's job is to expose tools to the planner, and v1 (decision 3) does
    not expose it. So v1 ships as `cerebral/llm/subagent.py::run_subagent(...)`; the
    MCP-plugin wrapper is deferred to the autonomy slice, the moment the planner
    needs to *see* a `delegate` tool.

## Design (minimal)

```python
# cerebral/llm/subagent.py
async def run_subagent(
    task: str,
    *,
    router: ModelRouter,
    orchestrator,                 # provides call_tool + the ADR-0005 gate + ACL
    tools: list[str] | None = None,
    context: str | None = None,
    model: str | None = None,
    max_steps: int = MAX_CHAIN_STEPS,
) -> ToolResult:
    ...
```

Builds a fresh transcript from `context` + `task`, resolves the tool set (scoped
list or shortlist), optionally pins `model` on the shared router for the run,
constructs a `Planner` + `ChainEngine` whose `gate_fn`/`execute_fn` delegate to the
same orchestrator, runs to completion, and returns the final text as a `ToolResult`.
`delegate` is excluded from the sub-agent's tool set (no nesting).

**Crash-resume tie-in (optional, later):** a delegated sub-chain is exactly the
"long run" `StepLedger` (C) was built for — `run_subagent` can pass a `run_id` +
ledger into `ChainEngine.run` so a delegation resumes after a crash. Deferred out
of the tracer to keep the first slice minimal.

## Why not the swarm (rejected alternative, non-obvious)

A future reader — or Felix mid-self_dev — will see "sub-agent delegation" and
expect a parallel supervisor, because that is what every framework in the
extraction ships. It is deliberately **not** that. On a single 8 GB GPU, parallel
local inference serializes; the throughput frame buys nothing and adds a scheduler,
a second routing path, and concurrency bugs. The value here is a context boundary,
delivered by a humble sequential blocking function. This ADR exists mainly to stop
someone "fixing" that back into a swarm.

## Slices (each one small, tested, mergeable)

**S1 (tracer) — `run_subagent` + integration test.** The function above.
One test with fake planner/orchestrator proving: fresh context (parent history
absent), tool scoping (sub-planner sees only the passed subset), gate reuse (a
denied tool stops the sub-chain), compact return (parent gets final text only).
No planner exposure, no caller wired yet — the primitive lands alone, safely.

**S2 — first real caller.** Wire one heavy internal flow to `run_subagent` (a
scoped research/summarize sub-run), proving the boundary in a live path.

**S3 — resumable delegations.** Thread `run_id` + `StepLedger` (C) through
`run_subagent` so a delegated sub-chain replays finished steps after a crash.

**S4 (gated on B) — planner autonomy.** Wrap `run_subagent` as a `delegate` plugin
tool exposed to the planner, behind an eval-harness case set that measures whether
the 8B scopes delegations sensibly (doesn't delegate everything / nothing).

**S5 (deferred, maybe never) — cloud-only parallel fan-out.** `asyncio.gather` over
cloud-pinned delegations, guarded so local never parallelizes. Only if a real need
appears.
