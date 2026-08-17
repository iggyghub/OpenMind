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

Active: H2-S1 -- #768 -- SLICED 2026-08-17, ready to build (all three AFK).
#733's design was already done (ADR-0020's provider-seam amendment); it is now
sliced into #768 / #769 / #770. #733 stays open as the parent/tracking issue.
The decomposition was produced by Felix (self_dev planner) reading the ADR --
its structure and AFK classification were sound; its file paths were wrong in
three places (invented cerebral/llm/context.py, cerebral/llm/test_subagent.py,
cerebral/session_worker.py) and were corrected against the real tree before
filing. Build order: #768 first (the others depend on the seam), then #769
and #770 in either order.
Then: H7-S1/#738 deferred per its own note; H8-S0/#739 still design-first
(no ADR yet -- needs a grill-with-docs session before any code).
Model: sonnet (all three are safe-zone builds)

## Queue (slice-granular; each entry = one tracer PR; read the issue + its ADR)

- [x] H5-S1 -- #736 -- spill store + post-execute hook + retrieve tool -- Type: AFK -- standalone
- [x] H4-S1 -- #735 -- command registry + no-LLM dispatch (main.py) + 1 example -- Type: HITL
- [x] H6-S1 -- #737 -- approval presets over the ADR-0005 gate -- Type: HITL -- confirm preset set first
- [x] H1-S1 -- #732 -- model context_window metadata + prompt token estimator (ADR-0021 S1) -- Type: AFK
- [x] H1-S2 -- #732 -- tool-result pruning via spill (ADR-0021 S2) -- Type: AFK -- needs H5
- [x] H1-S3 -- #732 -- oldest-turn summarization in main.py (ADR-0021 S3) -- Type: HITL
- [x] H3-S1 -- #734 -- derive_model_context() + assembly invariant (ADR-0022 S1) -- Type: HITL
- [x] H3-S2 -- #734 -- fork(session, boundary) on the conversation store (ADR-0022 S2) -- Type: AFK
- [ ] H2-S1 -- #768 -- SubagentProvider seam (run_subagent = fork-in-process provider) -- Type: AFK -- FIRST, the other two depend on it
- [ ] H2-S2 -- #769 -- continuable delegation (follow-up to a finished sub-agent) -- Type: AFK -- needs #768 + H3-S2's fork_thread
- [ ] H2-S3 -- #770 -- background-job registration (listable/killable delegations) -- Type: AFK -- needs #768; observability only, still sequential
      (parent #733 stays open as the H2 tracking issue until all three land)
- [ ] H7-S1 -- #738 -- task-workflow over subagents (ADR-0023) -- Type: AFK -- gated on H2; DEFER unless a task needs it
- [ ] H8-S0 -- #739 -- Code Mode sandbox spike, cloud-gated (ADR-0024) -- Type: HITL -- LAST

## Landed PRs

- H5-S1 #736 -- PR #742 (spill store) -- merged to master. Built by Claude Code (Budd was unavailable across 3 self_dev attempts); AFK safe-zone, full suite green (4679 passed).
- H1-S1 #732 -- PR #745 (context_window metadata + token estimator) -- merged to master. Module+tests from Felix's self_dev run (hermes); Deliverable 1 + _real_models fix by hand. AFK safe-zone, full suite green (4690 passed).

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
