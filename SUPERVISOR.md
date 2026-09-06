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

- **Active:** SUP-0 -- #1098
- **Model:** sonnet

## Queue

- [ ] SUP-0 -- #1098 -- self_dev test gate must run tray's jest suite for JS diffs
- [ ] SUP-1 -- #1099 -- arm the rollback on a code load, never on a plain restart
- [ ] SUP-2 -- #1100 -- a rollback must never destroy uncommitted work
- [ ] SUP-3 -- #1101 -- respawn a dead Cerebral once, bounded
- [ ] SUP-4 -- #1102 -- the master-update poll: fix the crash, then stop treating local commits as updates

## Landed PRs

(none yet)

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
- Live-verify per ADR-0028 R6 after SUP-1..SUP-3 land: a killed Cerebral process
  is seen to come back once; a chat "restart yourself" is seen not to arm the
  rollback; a dirty working tree is seen to survive a manual rollback via
  `git stash pop`, not a wipe.
