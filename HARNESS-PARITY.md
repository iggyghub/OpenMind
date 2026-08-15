# HARNESS-PARITY.md -- deepseek-harness parity program driver

Adopt the capabilities OpenMind lacks vs `deepseek-ai/deepseek-harness` (reviewed
2026-08-15; docs extracted into `openmind.db` collection 'harness improvements').
Design ADRs 0021-0024 + the ADR-0020 amendment are LANDED (grills done). North
star: shrink `main.py`'s privileged core toward plugin-everything -- each slice
chips at it; none is a big-bang rewrite.

## Status: blocked

<!-- STAGED. Reason: awaiting operator go-ahead. Flip to 'ready' to launch. -->
Status: blocked

## Next slice -- start here

Active: H4-S1 -- #735
Model: sonnet

## Queue (slice-granular; each entry = one tracer PR; read the issue + its ADR)

- [x] H5-S1 -- #736 -- spill store + post-execute hook + retrieve tool -- Type: AFK -- standalone
- [ ] H4-S1 -- #735 -- command registry + no-LLM dispatch (main.py) + 1 example -- Type: HITL
- [ ] H6-S1 -- #737 -- approval presets over the ADR-0005 gate -- Type: HITL -- confirm preset set first
- [ ] H1-S1 -- #732 -- model context_window metadata + prompt token estimator (ADR-0021 S1) -- Type: AFK
- [ ] H1-S2 -- #732 -- tool-result pruning via spill (ADR-0021 S2) -- Type: AFK -- needs H5
- [ ] H1-S3 -- #732 -- oldest-turn summarization in main.py (ADR-0021 S3) -- Type: HITL
- [ ] H3-S1 -- #734 -- derive_model_context() + assembly invariant (ADR-0022 S1) -- Type: HITL
- [ ] H3-S2 -- #734 -- fork(session, boundary) on the conversation store (ADR-0022 S2) -- Type: AFK
- [ ] H2-S1 -- #733 -- subagent provider seam + continuation + jobs (ADR-0020 amend) -- Type: HITL -- AFTER delegation #727-730
- [ ] H7-S1 -- #738 -- task-workflow over subagents (ADR-0023) -- Type: AFK -- gated on H2; DEFER unless a task needs it
- [ ] H8-S0 -- #739 -- Code Mode sandbox spike, cloud-gated (ADR-0024) -- Type: HITL -- LAST

## Landed PRs

- H5-S1 #736 -- PR #742 (spill store) -- merged to master. Built by Claude Code (Budd was unavailable across 3 self_dev attempts); AFK safe-zone, full suite green (4679 passed).

## SAFETY

- One tracer PR per slice, branched off latest origin/master, Closes its issue.
- Type: AFK (safe-zone / new-file-adjacent) -> the session merges its own PR after green.
- Type: HITL (guardrail: cerebral/main.py, cerebral/security/; or a new autonomous
  capability) -> open the PR, set Status: blocked naming the PR, commit that to
  master, STOP for human review. Never self-merge a HITL slice.
- The dev-loop itself stays external/operator-driven (ADR-0023): no Felix-run loop
  self-approves guardrail changes.
- No test may call a real model / real git / real Cerebral -- inject fakes (mirror
  tests/test_eval_harness.py, tests/test_step_ledger.py). ASCII .ps1 bodies.
- HARNESS-PARITY.md is the ONLY file committed straight to master; slice code via PR.
