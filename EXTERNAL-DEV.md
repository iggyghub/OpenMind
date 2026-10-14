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

## Status: done

## Next slice -- start here

- **Active:** none -- queue fully landed 2026-09-04

## Queue

- [x] EXT1 -- #1059 -- self_dev gains target_dir: generalize clone_fn's origin-repoint, thread
      target_dir through _run (skip the Felix-restart step when set), and replace the
      OpenMind-hardcoded candidate-file walk in _self_dev_edit with a generic one
- [x] EXT2 -- #1061 -- clone_fn (cerebral/self_dev_io.py) still repoints an external target_dir
      clone's origin at Felix's OWN repo -- the one piece EXT1 skipped, and the piece that makes
      target_dir unsafe to use until fixed

## Landed PRs

- PR #1062 -- EXT2 (self_dev generated the correct `clone_fn` fix, matching the issue spec
  byte-for-byte; the sandbox gate correctly caught a real bug in the new test it wrote
  (`git commit` before `git config user.name`/`user.email`, "Author identity unknown", exit
  128) and left the PR unmerged as `tests_failed` instead of auto-merging red. Hand-fixed the
  test's operation order only (`clone_fn` itself needed no changes), full suite re-run clean
  locally (5533 passed/7 skipped), merged by hand.)
- PR #1060 -- EXT1 (auto-merged by self_dev_campaign; hand-review found it skipped
  `cerebral/self_dev_io.py`'s `clone_fn` fix entirely and added none of the specified tests --
  main.py/self_dev.py parts were correct and match the issue spec closely. Filed the gap as
  #1061/EXT2 rather than hand-fixing, since the fix is small, precisely specced, and
  `self_dev_io.py` isn't a guardrail path.)

**Campaign done 2026-09-04.** `self_dev` now accepts an optional `target_dir` (absolute path to
an external local git repo with a GitHub remote): same clone -> model edit -> sandboxed test ->
PR pipeline as Felix's own repo, origin/identity correctly resolved from whichever repo was
actually cloned, and the Felix-restart step skipped when `target_dir` is set. Not yet
live-verified against a REAL external repo end-to-end (both slices were verified by diff review
+ local test runs, not a live `self_dev` call with `target_dir` set) -- worth a one-time live
smoke test before relying on it for real.
