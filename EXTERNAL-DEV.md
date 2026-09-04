# EXTERNAL-DEV.md -- self_dev campaign driver for editing code outside Felix's own repo

Source: user request 2026-09-04 ("felix has self dev campaigns, but i need it to also be able to
write code outside of itself, things like being able to point it to a file or destination to work
in"). Explored by hand first (see PR discussion / commit history around this date for the
exploration notes); user explicitly asked for the same pipeline (clone, model edit, sandboxed
tests, PR), not a stripped-down version -- "they shouldn't need to be any different". Single
slice: `self_dev` gains an optional `target_dir` arg that points the whole existing pipeline at
an external local git repo instead of Felix's own, and the Felix-restart step is skipped when
it's set.

**Read before running this campaign:** every guardrail-path slice landed via self_dev in this
project's history has needed hand-review (`plugins/self_dev.py` and `cerebral/main.py` are both
GUARDRAIL_PATHS -- this slice touches both, so it will escalate for human review and never
auto-merge, by design). Hand-verify the real diff against the issue spec before merging, even on
a green sandbox test run.

**Never send `tray/` to self_dev** -- standard practice for this whole project.

## Status: done

## Next slice -- start here

- **Active:** EXT1 -- #1059
- **Model:** sonnet

## Queue

- [x] EXT1 -- #1059 -- self_dev gains target_dir: generalize clone_fn's origin-repoint, thread
      target_dir through _run (skip the Felix-restart step when set), and replace the
      OpenMind-hardcoded candidate-file walk in _self_dev_edit with a generic one

## Landed PRs

(none yet)
- PR #1060 -- EXT1 (auto-merged by self_dev_campaign)
