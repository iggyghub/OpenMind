# EXTERNAL-DEV.md -- self_dev campaign driver for editing code outside Felix's own repo

Source: user request 2026-09-04 ("felix has self dev campaigns, but i need it to also be able to
write code outside of itself, things like being able to point it to a file or destination to work
in"). Explored by hand first (see PR discussion / commit history around this date for the
exploration notes); user explicitly asked for the same pipeline (clone, model edit, sandboxed
tests, PR), not a stripped-down version -- "they shouldn't need to be any different". Single
slice: `self_dev` gains an optional `target_dir` arg that points the whole existing pipeline at
an external local git repo instead of Felix's own, and the Felix-restart step is skipped when
it's set.

**Read before running this campaign:** guardrail-path hits (`plugins/self_dev.py` and
`cerebral/main.py` are both GUARDRAIL_PATHS) are INFORMATIONAL ONLY per the 2026-08-21 full-
auto-merge amendment -- they do NOT block merge (only a failing test suite does). EXT1 proved
this: it touched both guardrail files and still auto-merged. Hand-verify every landed PR's real
diff against its issue spec regardless -- self_dev has repeatedly shipped incomplete diffs (EXT1
itself skipped one of its three specified files, see EXT1 note below) even with green tests.

**Never send `tray/` to self_dev** -- standard practice for this whole project.

## Status: ready

## Next slice -- start here

- **Active:** EXT2 -- #1061
- **Model:** sonnet

## Queue

- [x] EXT1 -- #1059 -- self_dev gains target_dir: generalize clone_fn's origin-repoint, thread
      target_dir through _run (skip the Felix-restart step when set), and replace the
      OpenMind-hardcoded candidate-file walk in _self_dev_edit with a generic one
- [ ] EXT2 -- #1061 -- clone_fn (cerebral/self_dev_io.py) still repoints an external target_dir
      clone's origin at Felix's OWN repo -- the one piece EXT1 skipped, and the piece that makes
      target_dir unsafe to use until fixed

## Landed PRs

- PR #1060 -- EXT1 (auto-merged by self_dev_campaign; hand-review found it skipped
  `cerebral/self_dev_io.py`'s `clone_fn` fix entirely and added none of the specified tests --
  main.py/self_dev.py parts were correct and match the issue spec closely. Filed the gap as
  #1061/EXT2 rather than hand-fixing, since the fix is small, precisely specced, and
  `self_dev_io.py` isn't a guardrail path.)
