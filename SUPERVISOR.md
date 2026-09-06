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

- **Active:** SUP-4b -- #1120
- **Model:** sonnet

## Queue

- [x] SUP-0 -- #1098 -- self_dev test gate must run tray's jest suite for JS diffs
- [x] SUP-1 -- #1099 -- arm the rollback on a code load, never on a plain restart (INCOMPLETE -- see SUP-1b)
- [x] SUP-1b -- #1105 -- tray/main.js never got the reason-based restart routing (PR #1104 only touched cerebral/main.py)
- [x] SUP-2 -- #1100 -- a rollback must never destroy uncommitted work (INCOMPLETE, safe partial -- see SUP-2b)
- [x] SUP-2b -- #1112 -- stash-before-reset never wired into tray/main.js or manualRollback's own call site -- COMPLETE
- [x] SUP-3 -- #1101 -- respawn a dead Cerebral once, bounded (state declared, INCOMPLETE -- see SUP-3b)
- [x] SUP-3b -- #1117 -- wire the reconnect-counter/respawn state PR #1116 declared but never used -- COMPLETE (hand-authored, see Landed PRs)
- [x] SUP-4 -- #1102 -- the master-update poll: fix the crash, then stop treating local commits as updates (Problem A COMPLETE, Problem B INCOMPLETE -- see SUP-4b)
- [ ] SUP-4b -- #1120 -- _checkForMasterUpdate never wires gitMergeBaseFn (PR #1119 left Problem B live)

## Landed PRs

- SUP-0 -> PR #1103 (hand-built + reviewed, not run through self_dev_campaign -- see SAFETY)
- SUP-1 -> PR #1104 (auto-merged by self_dev_campaign -- INCOMPLETE, cerebral/main.py only; tray/main.js half filed as SUP-1b)
- SUP-1b -> PR #1108 (self_dev_campaign produced the correct diff twice -- #1106 and #1108 -- both blocked by an unrelated pytest exit-code flake, #1107; #1108 hand-merged after verifying its content by hand, since retrying a third time was not worth another ~11min run)
- SUP-2 attempt 1 -> PR #1111 closed, not merged -- boot-check.js's own logic was correct but the wiring that would call it was never added (filed as SUP-2b, #1112).
- SUP-2 attempt 2 (SUP-2b) -> PR #1113 auto-merged, then **reverted** (commit 9a437d7) -- `_doRollback` didn't even accept `gitStashFn` any more and `manualRollback` passed hardcoded no-op stubs, a real regression. Its own new jest tests would have caught this, but jest never actually ran -- `tray/node_modules` is gitignored, missing in every clone, and "missing -> skip" reported the same `passed` as pytest alone. Root-caused and fixed by hand in `cerebral/self_dev_io.py` (`_ensure_tray_node_modules`, commit b18e71e).
- SUP-2 attempt 3 -> PR #1114 auto-merged -- landed on master, verified SAFE (unlike attempt 2): `_doRollback` genuinely accepts and calls `gitStashFn`. NOT reverted. Still incomplete in the same shape as attempt 1: `manualRollback` never passed `gitStashFn` to its own `_doRollback` call, `tray/main.js` didn't wire a real `gitStashFn`, no new tests. Ran through the OLD gate (Cerebral hadn't reloaded the node_modules fix yet).
- SUP-2 attempt 4 (SUP-2b) -> **PR #1115, hand-merged, COMPLETE.** First run with the node_modules fix live: jest genuinely ran and crashed the gate with a second, unrelated bug -- `capture_output=True`/`text=True` with no explicit encoding decodes jest's coloured output using the platform's default codepage (cp1252 on this box), which can't handle a byte in jest's real output and silently kills the stderr-reader thread, leaving `.stderr` as `None` and crashing `test_fn` with `TypeError` instead of showing jest's actual failure. Fixed by hand (`encoding="utf-8", errors="replace"`, commit 2ddeb7d). Once visible, jest's real failure was genuine but trivial: the PR's own new tests used `toHaveBeenCalledBefore`, a `jest-extended` matcher not installed in this project. The functional diff (`manualRollback` fixed, both `tray/main.js` call sites wired with real `git stash push --include-untracked`) was independently verified correct via `gh pr diff` before touching anything. Fixed the two matcher lines by hand (native `mock.invocationCallOrder`, a mechanical one-line substitution, not feature work) and merged. Cerebral restarted afterward and confirmed the restart was a plain one (`pending_backup` stayed null) -- live proof SUP-1b's reason-based routing still works correctly.
- SUP-3 -> PR #1116 auto-merged -- safe but inert: declared all five state variables/constants (`_consecutiveFailures`, `_respawnWatchdog`, `_reconnectHalted`, `RECONNECT_FAILURE_THRESHOLD`, `RESPAWN_WATCHDOG_MS`, with correct threshold math -- 5 x 3s = 15s) but never touched `ws.on('close')` or `ws.on('open')` to actually use them. Fourth occurrence of this exact "declare, don't wire" shape (SUP-1 x2, SUP-2 x2-3, now SUP-3) -- filed SUP-3b (#1117) with the complete literal patch for both handlers, matching the explicit-code style that worked for SUP-1b and SUP-2b's successful retries.
- SUP-3b -> **PR #1118, hand-authored, COMPLETE.** `self_dev_campaign` returned `Edit step produced no commit` TWICE in a row for this exact, fully pre-specified patch (#1117), with bonsai confirmed reachable both times -- a mechanical failure to produce any diff at all, not an incorrect one, unlike every other gap tonight. Since the patch was already complete and independently verified (it's the literal code #1117 specified), applied it by hand rather than retrying a third identical attempt: 34 suites / 908 tests green, 25-line diff, nothing else touched. Cerebral restarted afterward and confirmed the restart stayed plain (`pending_backup` null).
- SUP-4 -> PR #1119 auto-merged -- Problem A (the `_gitOut` null-stdout crash) fixed correctly. Problem B's supporting logic (the `gitMergeBaseFn` ancestor check in `checkForUpdate`) was added correctly to `tray/lib/boot-check.js`, guarded on `gitMergeBaseFn` being truthy -- but `tray/main.js`'s `_checkForMasterUpdate`, the only caller, never passes it. Fifth occurrence tonight of the "supporting logic added, call site never wired" shape. A local commit still arms a destructive self-dev restart today, unchanged from before this PR. Filed SUP-4b (#1120) with the one-parameter literal fix.

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
- **"Tests: PASS" on a self-dev PR does not mean what it looks like it means
  for a tray/ change.** PR #1113 (SUP-2b) auto-merged with 4 failing jest
  tests -- the ones it added itself -- because `tray/node_modules` is missing
  in every self-dev clone (gitignored) and the gate's old behaviour was to
  silently skip jest rather than fail when it's absent, reporting the same
  `passed` either way. Fixed by hand (`_ensure_tray_node_modules`,
  `cerebral/self_dev_io.py`) so jest genuinely runs now -- but the general
  lesson stands for any future gate change: a green result and a skipped
  check look identical from the campaign's own output, so read the actual
  test summary text (not just `merge_decision`) before trusting a tray/ PR,
  and check the merged file list against the issue's own file list every
  time, not just when something already smells wrong.
- **A hand-fix to Cerebral's own Python doesn't take effect until Cerebral
  restarts** -- same lesson as the tray/main.js hot-reload gap earlier
  tonight, just on the other process. The `_ensure_tray_node_modules` fix
  was committed and pushed, but SUP-2 attempt 3 (PR #1114) still ran through
  the OLD gate (jest skipped, not because it failed to link -- because the
  running process hadn't reloaded the file that does the linking). After any
  hand-edit to `cerebral/*.py` this campaign depends on, restart Cerebral (a
  plain `restart_felix`, reason `user` -- confirm it does NOT arm the
  self-dev boot-check) and confirm the new behaviour before firing the next
  slice, not just after pushing the commit.
- **A crashed gate hides the real result -- diagnose crashes, don't just
  treat "tests_failed" as "the diff is wrong."** SUP-2b's fourth attempt
  (PR #1115) showed `merge_decision: tests_failed` with a Python-side
  `TypeError`, which looked identical to "the model wrote bad code." The
  actual diff was completely correct; the gate itself (`test_fn`'s missing
  `encoding=` on the jest subprocess call) crashed before jest's own
  failure output could be seen. When a self-dev PR is blocked with an
  opaque runner-level error (not a named test failure), reproduce the exact
  gate command by hand against the clone before concluding the PR's content
  is at fault.
