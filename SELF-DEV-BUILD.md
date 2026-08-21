# Self-dev loop -- build campaign (ADR-0015)

Felix modifies its own core through clone -> test -> PR -> restart. Driver for
`scripts/run-self-dev.ps1`. Each slice runs in a fresh headless Claude Code
session; this file + the issue are the only memory between them.

Status: done
Model: sonnet

## Next slice -- start here

- (all slices landed)

## Queue

- [x] SD-1 -- #554  self_dev plugin: clone -> branch -> edit -> test -> PR (AFK, auto-merge on green)
- [x] SD-2 -- #555  restart-to-load handoff for merged self-dev PRs (AFK, auto-merge on green)
- [x] SD-3 -- #556  boot self-check + SHA rollback + state snapshot (HITL -- PR #560 open, awaiting human review)
- [x] SD-4 -- #557  blast-radius auto-merge gate: safe zones vs guardrails (HITL -- landed as PR #561)
- [x] SD-5 -- #807  self_dev_campaign: driver-file slice-queue mode (HITL -- landed as PR #808, human-approved merge)

## Landed PRs

- PR #558: SD-1 -- self_dev plugin (clone -> branch -> edit -> test -> PR)
- PR #559: SD-2 -- restart-to-load handoff for merged self-dev PRs
- PR #560: SD-3 -- boot self-check + SHA rollback + state snapshot
- PR #561: SD-4 -- blast-radius auto-merge gate (safe zones vs guardrails)
- PR #808: SD-5 -- self_dev_campaign: driver-file slice-queue mode

## Notes

- Per ADR-0015 + the blast-radius gate, SD-3 and SD-4 touch the guardrails
  (launcher rollback, the merge-authority gate) and the self-dev loop's own code.
  A slice marked `Type: HITL` opens its PR and STOPS for human review -- it must
  not self-merge. AFK slices merge their own PR.
- Slices BUILD the machinery; a slice must NOT run a real self-modification
  against the live repo, must not start a real Cerebral, and must not open real
  PRs from inside tests. Every test injects all side effects (`clone_fn` /
  `edit_fn` / `test_fn` / `pr_fn`) -- no real git / gh / network / Cerebral.
- SD-5 (2026-08-21 ADR-0015 amendment): `self_dev_campaign` reuses `_run()`
  unchanged -- it is a loop around the existing engine, not a second one. Same
  seam discipline as above, plus a new injectable `issue_fn` seam for `gh issue
  view` (never real `gh` in tests).
