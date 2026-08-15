# ADR-0023: Task-workflow as a capability; the dev-loop stays external

**Date:** 2026-08-15
**Status:** Accepted (grill session; clear-victor call made inline)
**Program:** harness-parity (H7, #738). Adapted from `deepseek-ai/deepseek-harness` (`ctx.workflowEngine` + `tool-ralph`).
**Depends on:** ADR-0020 subagents (H2, #733). **Relates:** ADR-0015 self-dev loop, C StepLedger.

## Context

`dsh` makes the autonomous slice-loop a first-class tool (`tool-ralph`): a workflow run fans its `agent()` calls out through `ctx.subagents`, one fresh structured-output route per iteration. OpenMind already has this pattern, but **external** — the `scripts/run-*.ps1` campaign runners and the self_dev loop drive fresh `claude -p` sessions from PowerShell (it built delegation S1 today). The question: should Felix run its own loop?

## Decisions (clear victor — the safety tension resolves cleanly)

1. **Split by whether the loop self-modifies.** The self-modifying **dev-loop stays EXTERNAL / operator-driven.** A loop that edits Felix's own core and merges PRs must never be driven by Felix — the ADR-0015 blast-radius gate (which escalates guardrail changes to a human) is meaningless if Felix also runs the loop that would approve them. This is a hard line.
2. **A bounded, non-self-modifying task-workflow IS in scope.** Felix may run a multi-step *task* plan — research, multi-doc processing, data pipelines — where each step is an ADR-0020 subagent, resumable via StepLedger (C). This is `ctx.workflowEngine` minus self-modification.
3. **The workflow tool cannot touch git/PRs/self_dev.** Enforced by tool scoping: a workflow's subagents get a tool allow-list that excludes `self_dev`, git, and guardrail-adjacent tools. A workflow that needs code changes escalates to the external operator loop, it does not run one.
4. **Reuse, don't reinvent, the driver/state.** A workflow's plan + per-step state reuse StepLedger (C) for crash-resume; no new state store.

## Open (genuine fork — your call)

- **Do we even need the in-product task-workflow yet?** It only pays off once there are real multi-step non-code tasks that benefit from Felix orchestrating subagents unattended. Recommend building H2 (subagents) fully first and **deferring this** until a concrete task demands it (YAGNI). Flag if you want it built alongside H2.

## Slices (gated on H2)

- **S1** — a `workflow` capability: takes a step plan, runs each step as a scoped subagent (no self_dev/git tools), persists progress to StepLedger, resumes on crash. Test: a 3-step fake plan runs, one step's tools are correctly scoped-out, a mid-plan crash resumes. AFK (new module over subagents).
