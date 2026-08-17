# HARNESS-PARITY.md -- deepseek-harness parity program driver

Adopt the capabilities OpenMind lacks vs `deepseek-ai/deepseek-harness` (reviewed
2026-08-15; docs extracted into `openmind.db` collection 'harness improvements').
Design ADRs 0021-0024 + the ADR-0020 amendment are LANDED (grills done). North
star: shrink `main.py`'s privileged core toward plugin-everything -- each slice
chips at it; none is a big-bang rewrite.

## Status: done

<!-- Program CLOSED 2026-08-17. 9 slices built; H8 rejected, H7 deferred. -->
Status: done

## Next slice -- start here

Active: none -- PROGRAM COMPLETE 2026-08-17.

Nine slices built and merged (H5-S1, H4-S1, H6-S1, H1-S1/S2/S3, H3-S1/S2, H2-S1/S2/S3).
The two DESIGN-FIRST slices were grilled 2026-08-17 and both resolved as "don't build":

- H8-S0 / #739 -- Code Mode: **REJECTED**. ADR-0024 is now Status: Rejected with a
  "Why not Code Mode" section. Its token-efficiency payoff was absorbed by spill +
  prune + summarize + subagent, and it is cloud-only, which conflicts with CONTEXT.md
  principle 3 ("Felix works fully offline"). Revisit only on a measured failure of the
  native path.
- H7-S1 / #738 -- task-workflow: **DEFERRED** on its own YAGNI terms. ADR-0023's fork
  needed H2 done (it is) plus a concrete task demanding it (there isn't one). Revisit
  when a real multi-step non-code task needs unattended orchestration AND crash-resume.

Known gap, tracked in #776: H1-S2's context_pruner and harness-improvement C's
StepLedger are ticked complete but have NO production consumer -- both are imported
only by their own tests. Docs and code disagree; wire them or un-tick them.

## Queue (slice-granular; each entry = one tracer PR; read the issue + its ADR)

- [x] H5-S1 -- #736 -- spill store + post-execute hook + retrieve tool -- Type: AFK -- standalone
- [x] H4-S1 -- #735 -- command registry + no-LLM dispatch (main.py) + 1 example -- Type: HITL
- [x] H6-S1 -- #737 -- approval presets over the ADR-0005 gate -- Type: HITL -- confirm preset set first
- [x] H1-S1 -- #732 -- model context_window metadata + prompt token estimator (ADR-0021 S1) -- Type: AFK
- [x] H1-S2 -- #732 -- tool-result pruning via spill (ADR-0021 S2) -- Type: AFK -- needs H5
- [x] H1-S3 -- #732 -- oldest-turn summarization in main.py (ADR-0021 S3) -- Type: HITL
- [x] H3-S1 -- #734 -- derive_model_context() + assembly invariant (ADR-0022 S1) -- Type: HITL
- [x] H3-S2 -- #734 -- fork(session, boundary) on the conversation store (ADR-0022 S2) -- Type: AFK
- [x] H2-S1 -- #768 -- SubagentProvider seam (run_subagent = fork-in-process provider) -- Type: AFK -- merged 151b9bd (PR #771)
- [x] H2-S2 -- #769 -- continuable delegation (follow-up to a finished sub-agent) -- Type: AFK -- merged eb3df93 (PR #772); chose an in-memory SubagentHandle over fork_thread (recording a sub-chain just to fork it fights the isolation boundary)
- [x] H2-S3 -- #770 -- background-job registration (listable/killable delegations) -- Type: AFK -- merged 16b51cb; registry lives in cerebral/llm/job_registry.py (not a new cerebral/jobs/ package)
      (H2 COMPLETE -- all three landed 2026-08-17; parent #733 closed)
- [~] H7-S1 -- #738 -- task-workflow over subagents (ADR-0023) -- DEFERRED 2026-08-17, issue closed not-planned. H2 done, but no concrete task demands it; StepLedger (its crash-resume foundation) is itself unwired (#776).
- [~] H8-S0 -- #739 -- Code Mode sandbox spike, cloud-gated (ADR-0024) -- REJECTED 2026-08-17, ADR-0024 Status: Rejected, issue closed not-planned.

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
