# DELEGATION-BUILD.md -- Sub-agent delegation (harness improvement D) campaign driver

Design: `docs/adr/0020-sub-agent-delegation.md`. A sub-agent is a **context
boundary**, not a parallel swarm. Slices land one at a time via a fresh
`claude -p` session each (scripts/run-delegation.ps1).

## Status: ready

## Next slice -- start here

- **Active:** S2 -- #728
- **Model:** opus

## Queue

- [x] S1 -- #727 -- run_subagent tracer (new files, safe-zone) -- Type: AFK
- [ ] S2 -- #728 -- first real caller in main.py (guardrail) -- Type: HITL
- [ ] S3 -- #729 -- resumable delegations via StepLedger -- Type: AFK
- [ ] S4 -- #730 -- planner-autonomy delegate plugin (gated on eval harness) -- Type: HITL

## Landed PRs

- S1 -- PR #731 (run_subagent tracer)

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
