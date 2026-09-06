# SUPERVISOR.md -- self-dev campaign driver for ADR-0033

Autonomous slice loop for **ADR-0033: the supervisor is the tray**. Read
`docs/adr/0033-the-supervisor.md` and CONTEXT.md's **Supervisor** / **Code load**
terms for the design. Driven by Felix's own `self_dev_campaign` tool (ADR-0015
amendment SD-5), not an external `claude -p` loop -- this campaign fixes the
restart/rollback path itself, so it goes through the same gate any other core
change would.

## Status: ready

<!-- ready = slices remain; done = SUP-4 landed; blocked = a session needs a human -->

## Next slice -- start here

- **Active:** SUP-2b -- #1112
- **Model:** sonnet

## Queue

- [x] SUP-0 -- #1098 -- self_dev test gate must run tray's jest suite for JS diffs
- [x] SUP-1 -- #1099 -- arm the rollback on a code load, never on a plain restart (INCOMPLETE -- see SUP-1b)
- [x] SUP-1b -- #1105 -- tray/main.js never got the reason-based restart routing (PR #1104 only touched cerebral/main.py)
- [x] SUP-2 -- #1100 -- a rollback must never destroy uncommitted work (INCOMPLETE -- see SUP-2b)
- [ ] SUP-2b -- #1112 -- stash-before-reset never wired into tray/main.js or manualRollback's own call site (PR #1111 was inert)
- [ ] SUP-3 -- #1101 -- respawn a dead Cerebral once, bounded
- [ ] SUP-4 -- #1102 -- the master-update poll: fix the crash, then stop treating local commits as updates

## Landed PRs

- SUP-0 -> PR #1103 (hand-built + reviewed, not run through self_dev_campaign -- see SAFETY)
- SUP-1 -> PR #1104 (auto-merged by self_dev_campaign -- INCOMPLETE, cerebral/main.py only; tray/main.js half filed as SUP-1b)
- SUP-1b -> PR #1108 (self_dev_campaign produced the correct diff twice -- #1106 and #1108 -- both blocked by an unrelated pytest exit-code flake, #1107; #1108 hand-merged after verifying its content by hand, since retrying a third time was not worth another ~11min run)
- SUP-2 -> PR #1111 closed, not merged -- boot-check.js's own logic was correct but the wiring that would call it (tray/main.js's two call sites, plus manualRollback's own internal _doRollback call) was never added, so the fix would have been inert. Filed as SUP-2b with the exact three-part gap.

## SAFETY

- **SUP-0 first, and by hand.** ADR-0023 decision 1: a loop that edits its own
  core and merges its own PRs must never be driven by Felix -- the gate is
  meaningless if Felix builds the gate. SUP-0 fixes the gate itself
  (`cerebral/self_dev_io.py` `run_tests` running pytest only, never jest, on
  `tray/` diffs). Land SUP-0 as a human-reviewed PR before this driver hands
  SUP-1..SUP-4 to `self_dev_campaign`.
- SUP-1 through SUP-4 all touch `tray/main.js` and/or `tray/lib/boot-check.js`,
  both in `GUARDRAIL_PATHS`. Since the 2026-08-21 full-auto-merge amendment,
  `is_guardrail_diff` is informational only and does not block merge -- SUP-0's
  jest gate is the only thing standing between an untested edit and the
  restart/rollback path Felix needs in order to load its own fixes.
- Sequential, one slice at a time, same reasoning as SANDBOX-BUILD.md: SUP-1..
  SUP-4 edit overlapping files (`tray/main.js`, `tray/lib/boot-check.js`) and a
  parallel run would collide.
- If a slice genuinely needs a human decision, set `Status: blocked` with a
  one-line reason and stop without merging.
- **Firing this campaign restarts Cerebral, mid-campaign, every time a slice
  auto-merges.** SUP-1..SUP-4 all touch Felix's own core, so each successful
  merge triggers self_dev's own SD-2 restart-to-load -- which kills the
  connection driving the campaign call and orphans the async task, since the
  restart broadcast doesn't await its own shutdown. Observed 2026-09-05/06: an
  orphaned run kept processing the NEXT slice on the old, about-to-die process
  while a fresh Cerebral instance came up separately -- a live instance of the
  exact ADR-0028 R5 hazard ("nothing preempts"). Fire ONE slice at a time (not
  `max_slices > 1`), confirm the new Cerebral instance is healthy and no
  `pytest`/`git` process with a parent PID from the old instance is still
  running, before firing the next slice.
- The self-dev edit step's file-planning call has twice produced a
  `cerebral/main.py`-only diff for SUP-1, a task that genuinely needs both
  `cerebral/main.py` and `tray/main.js` changed together (see SUP-1b). If a
  slice's spec names two files that must change together, watch the merged
  PR's file list before ticking it done -- a green test run proves nothing
  about a file the edit step silently didn't touch.
- Live-verify per ADR-0028 R6 after SUP-1..SUP-3 land: a killed Cerebral process
  is seen to come back once; a chat "restart yourself" is seen not to arm the
  rollback; a dirty working tree is seen to survive a manual rollback via
  `git stash pop`, not a wipe.
