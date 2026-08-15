# ADR-0024: Code Mode (cloud-gated, last priority)

**Date:** 2026-08-15
**Status:** Accepted-but-deferred (grill session; clear-victor call made inline)
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

## Open (genuine fork — your call)

- **Build vs indefinitely defer.** Recorded as accepted-but-last per "we need all of it." If cloud spend / the local-first ethos makes a cloud-only feature unattractive, this is the one to cut. Flag if you'd rather mark it `wontfix` for now.

## Slices (last in the program)

- **S0** — spike: a sandboxed runner executing a fixed program against 2 gated fake bindings, proving the gate fires per binding call. Then slice the planner/chain integration in a follow-up ADR amendment.
