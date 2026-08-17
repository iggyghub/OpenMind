# ADR-0024: Code Mode (cloud-gated) — REJECTED

**Date:** 2026-08-15
**Status:** **Rejected 2026-08-17** (grill session; the open fork below was resolved
against building it). Superseded in practice by the compaction/isolation machinery
that landed between the two dates — see "Resolution" and "Why not Code Mode".
**Program:** harness-parity (H8, #739). Adapted from `deepseek-ai/deepseek-harness` (`ctx.codeRuntime`, "Code Mode transport").
**Relates:** ADR-0008 native tool-calling, ADR-0010 shell sandbox, B eval harness, ADR-0005 gate.

## Context

`dsh` Code Mode runs one model-written program against host-provided async tool bindings — the model orchestrates several tools in a single program instead of many native tool-call round trips (token-efficient, expressive). OpenMind uses native tool-calling only (ADR-0008: one `ToolCall` per step, looped by `ChainEngine`).

## Decisions (clear victors, decided here)

1. **Cloud-gated.** Code Mode only runs when routed to a cloud/quality model. Program synthesis is exactly what the local 8B is weakest at; running it locally would produce broken programs. A new `task_type="code_mode"` pins cloud (Budd/Claude); if no cloud model is available, Code Mode is unavailable and the planner falls back to native tool-calling.
2. **Every binding call still goes through the ADR-0005 gate.** The bindings exposed to the program are the existing tool registry wrapped as async functions; each call re-enters the same gate + ACL. Code Mode is NOT a gate bypass — a program calling a committing tool still escalates exactly as a native call would.
3. **Sandbox the runtime.** The program runs in the ADR-0010 shell/subprocess sandbox (restricted Python runtime), no network/fs except through gated bindings.
4. **Alternate step type, behind an eval.** The planner may emit a code-mode program instead of a `ToolCall`; `ChainEngine` gets a code-mode branch. It ships behind B eval cases proving a program's tool calls gate correctly and its result flows back — never on by default until measured.
5. **Last priority.** Deepest, most uncertain payoff on this hardware. Build only after H1-H6 and the delegation campaign land. It is accepted so the direction is recorded, not scheduled ahead of cheaper wins.

## Resolution (2026-08-17 grill) — do not build

The open fork below was **resolved against building it.** The five decisions above
still stand as the design *were it ever built*; they are kept verbatim so a future
revisit starts from a worked design rather than a blank page.

> **Open (the fork, now closed):** Build vs indefinitely defer. Recorded as
> accepted-but-last per "we need all of it." If cloud spend / the local-first ethos
> makes a cloud-only feature unattractive, this is the one to cut.

**Why the answer changed between 2026-08-15 and 2026-08-17.** This ADR was written
when Code Mode's pitch was token efficiency: many native tool-call round trips bloat
the transcript. In the two days after, four separate mechanisms landed that attack
exactly that problem:

| landed | effect on the bloat Code Mode was meant to avoid |
|---|---|
| `cerebral/llm/spill_store.py` (H5-S1, #736) | oversized tool results never enter the transcript at all |
| `cerebral/llm/context_budget.py` + `context_pruner.py` (H1-S1/S2, #732) | prunes the biggest prior results back out |
| `cerebral/llm/context_summarizer.py` (H1-S3, #732) | folds the oldest turns into a summary |
| `cerebral/llm/subagent.py` (ADR-0020) | an entire sub-chain returns **one compact result** |

The last one matters most: a sub-agent with a scoped tool set is already "run several
tools and hand back one small answer" — Code Mode's headline benefit, delivered by a
sequential blocking function that needs no sandboxed program runtime, no new step type
in `ChainEngine`, and no cloud dependency.

**What we are knowingly giving up.** Genuine control flow *inside* a single model
output — a loop that runs until a condition holds, or a branch chosen on an
intermediate tool result, expressed as a program rather than as successive planner
turns. A sub-agent approximates this (the sub-planner can loop across steps) but the
control flow lives in the planner's head, not in code. That is the real, non-imaginary
capability being declined.

**Revisit trigger.** Reopen only on a *measured* failure of the current path, not on
taste: a real task that demonstrably cannot be expressed as sequential `ToolCall`s
(needs loop-until-condition or branch-on-result across tools), or an eval case showing
round-trip cost dominating a real workload. Absent one of those, this stays closed.

## Why not Code Mode (rejected alternative, non-obvious)

A future reader — or Felix mid-self_dev — will find `ctx.codeRuntime` in the
deepseek-harness extraction and reasonably ask why OpenMind never built the
equivalent. It is deliberately absent, for two reasons:

1. **It is cloud-only, and CONTEXT.md principle 3 is "Local first, cloud fallback —
   Felix works fully offline."** Every other capability degrades to a local path.
   Code Mode (decision 1) does not degrade; offline it simply does not exist, because
   program synthesis is precisely what an 8B on a GTX 1080 is worst at. Building a
   capability the product's own principle says must work offline, that cannot, invites
   exactly the dependence the principle exists to prevent.
2. **Its payoff was largely absorbed** by the compaction + isolation work above,
   which is local-first, already shipped, and far cheaper to reason about.

Note also that decision 4's safeguard ("behind B eval cases") was thinner than it
sounds: at rejection time `cerebral/eval/harness.py` was 55 lines with a single case
file. "Gated behind an eval" would not have meant much.

## Slices (not scheduled — retained for a future revisit)

- **S0** — spike: a sandboxed runner executing a fixed program against 2 gated fake bindings, proving the gate fires per binding call. Then slice the planner/chain integration in a follow-up ADR amendment.
