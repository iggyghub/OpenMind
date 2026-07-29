# Self-dev loop -- build campaign (ADR-0015)

Felix modifies its own core through clone -> test -> PR -> restart. Driver for
`scripts/run-self-dev.ps1`. Each slice runs in a fresh headless Claude Code
session; this file + the issue are the only memory between them.

Status: ready
Model: sonnet

## Next slice -- start here

SD-1 -- #554  self-dev loop -> green PR against its own repo
Model: sonnet
Type: AFK

Build `plugins/self_dev.py` (sibling to `plugins/builder.py` / `plugins/skills.py`):
a `self_dev` tool that clones the repo into `cerebral/data/sandbox/self_dev/<run-id>/`
(full local clone, NOT a shared git worktree), branches, has a model make a
scoped edit, runs the test suite inside the ADR-0010 sandbox, and opens a PR via
`gh` / the `github` plugin. Add `task_type="self_dev"` handling (the router already
accepts arbitrary task-type strings; add `'self_dev'` to `SET_TASK_TYPES` in
`tray/windows/main.html` so it is model-selectable in Settings -> Models). Mirror
builder.py's injected-side-effect pattern (`clone_fn` / `edit_fn` / `test_fn` /
`pr_fn`) so the whole flow tests hermetically. The run stops at "PR opened" --
nothing merged or loaded (that is SD-2..SD-4). Demo: a trivial requested change
yields a green PR.

## Queue

- [ ] SD-1 -- #554  self_dev plugin: clone -> branch -> edit -> test -> PR (AFK, auto-merge on green)
- [ ] SD-2 -- #555  restart-to-load handoff for merged self-dev PRs (AFK, auto-merge on green) [blocked by SD-1]
- [ ] SD-3 -- #556  boot self-check + SHA rollback + state snapshot (HITL -- open PR, stop for human review) [blocked by SD-2]
- [ ] SD-4 -- #557  blast-radius auto-merge gate: safe zones vs guardrails (HITL -- open PR, stop for human review) [blocked by SD-1]

## Landed PRs

(none yet)

## Notes

- Per ADR-0015 + the blast-radius gate, SD-3 and SD-4 touch the guardrails
  (launcher rollback, the merge-authority gate) and the self-dev loop's own code.
  A slice marked `Type: HITL` opens its PR and STOPS for human review -- it must
  not self-merge. AFK slices merge their own PR.
- Slices BUILD the machinery; a slice must NOT run a real self-modification
  against the live repo, must not start a real Cerebral, and must not open real
  PRs from inside tests. Every test injects all side effects (`clone_fn` /
  `edit_fn` / `test_fn` / `pr_fn`) -- no real git / gh / network / Cerebral.
