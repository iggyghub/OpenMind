# DELEGATION-BUILD.md -- Sub-agent delegation (harness improvement D) campaign driver

Design: `docs/adr/0020-sub-agent-delegation.md`. A sub-agent is a **context
boundary**, not a parallel swarm. Slices land one at a time via a fresh
`claude -p` session each (scripts/run-delegation.ps1).

## Status: blocked

<!-- HITL: S4 (#730) is built and green as PR #755, awaiting human review + merge.
     It is the last slice -- merging it completes the campaign. -->

## Next slice -- start here

- **Active:** S4 -- #730 -- BUILT, awaiting human merge of PR #755
- **Model:** sonnet

## Queue

- [x] S1 -- #727 -- run_subagent tracer (new files, safe-zone) -- Type: AFK
- [x] S2 -- #728 -- first real caller in main.py (guardrail) -- Type: HITL
- [x] S3 -- #729 -- resumable delegations via StepLedger -- Type: AFK
- [ ] S4 -- #730 -- planner-autonomy delegate plugin (gated on eval harness) -- Type: HITL

## Landed PRs

- S1 -- PR #731 (run_subagent tracer)
- S2 -- PR #753 (_video_verify grounding via run_subagent) -- merged to master as
  bf9dba4. HITL slice merged directly on explicit operator authorization, after a
  full diff review + independent targeted run (67 passed). Also hoists the turn
  handler's `_gate` closure to module-level `_gate_tool` so sub-agents reuse the
  same ADR-0005 gate instead of duplicating it.
- S3 -- PR #754 (run_id + ledger crash-resume passthrough) -- merged to master as
  448664e. AFK safe-zone, pure wiring (12 lines in subagent.py); independently
  re-run after merge: tests/test_subagent.py 7 passed.
- S4 -- PR #755 (delegate plugin + eval cases + seam wiring) -- OPEN, HITL, NOT
  merged. Exposes `delegate` to the planner (first planner-autonomy slice), so a
  human merges it. Review found and fixed two blockers before it was mergeable:
  (1) it declared the full 16-class vocabulary, which resolves DENY-by-default --
  `check_capabilities` takes the worst decision across the set and `shell_exec`
  defaults to DENY (gate.py:57), so the planner would have seen a permanently
  denied tool. Now `frozenset()`, per plugins/memory.py's precedent: the wrapper
  does nothing gate-worthy itself and every nested step is re-gated (ADR-0020
  decision 7), the same posture `_gate_tool` already uses for `recipe_*`.
  (2) the `set_subagent_context` seam was never wired, so every call failed
  closed -- added a 3-line entry to main.py's `_wire_plugin_seams` table (this is
  why the PR touches a guardrail file). Independently verified: 239 passed.

## SAFETY

- Each slice is ONE PR that Closes its issue, branched off latest origin/master.
- **Type: AFK** slices (S1, S3) are safe-zone (new files / new-file-adjacent) --
  the session merges its own PR after its pytest passes.
- **Type: HITL** slices (S2, S4) touch a guardrail (`cerebral/main.py`) or add an
  autonomous capability (`delegate` plugin exposed to the planner). The session
  opens the PR, sets `Status: blocked` naming the PR, commits that to master, and
  STOPS -- a human reviews and merges. Never self-merge a HITL slice.
- No test may call a real model, real git/gh/network, or start a real Cerebral --
  inject fakes (mirror `tests/test_eval_harness.py` / `tests/test_step_ledger.py`).
- DELEGATION-BUILD.md is the ONLY file committed straight to master; all slice
  code goes through the PR. Keep .ps1 bodies ASCII (Windows PS 5.1 / CLAUDE.md).
