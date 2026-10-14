# SELF-DEV-IDLE-FIX.md -- self_dev campaign driver: campaign-aware idle check

Source: 2026-09-08 Claude Code session, discovered live while running the TICKER-DISCOVERY
campaign -- three separate driver-advance pushes each raced against `tray/main.js`'s auto-update
restart, which considers Felix "idle" (safe to restart on new commits) purely from chat/voice wake
state, blind to whether a self_dev campaign is mid-flight via the direct-IPC
`scripts/trigger_campaign.py` path. Harmless so far only because `plugins/self_dev.py`'s
2026-09-08 concurrency guard meant no two attempts ever corrupted each other -- a real slice's
in-progress clone/edit/test getting killed by this restart is a live risk, not hypothetical.

Not folded into TICKER-DISCOVERY.md's own queue -- unrelated topic (self_dev/tray infra, not the
trading candidate pool), kept as its own driver so the two campaigns' scopes stay clean. Runs as
the next self_dev slice once TICKER-DISCOVERY's DD5 lands.

**tray/ guardrail note (ADR-0015 decision 5, same caveat as TRADING-PANEL-UI.md's UI1):** `tray/`
is in `GUARDRAIL_PATHS` but the 2026-08-21 amendment made that informational-only -- self_dev can
merge it. The self_dev sandbox test gate is pytest-only and cannot run any JS at all, let alone
Electron main-process code with no existing test harness (`tray/main.js` has no test file today).
**Hand-verify this one by actually reading the diff and, ideally, exercising a real restart -- there
is no automated safety net for this specific change.**

## Status: done

## Next slice -- start here

- **Active:** none -- IDLE1 landed 2026-09-08

## Queue

- [x] IDLE1 -- #1168 -- Auto-update restart must not consider a running self_dev_campaign idle

## Landed PRs

- PR #1170 (recovered/hand-opened, see below) -- IDLE1. The triggering run itself hit the exact
  restart-collision bug this issue describes (irony noted) -- the client connection died
  ("1001 going away"), but its edit step had already completed and committed locally in the sandbox
  clone, never pushed. Recovered by pushing the branch and opening the PR by hand.

  Three real bugs found on review, all in the "looks complete, isn't wired" family this whole
  campaign kept hitting: (1) `plugins/self_dev.py` defined `set_campaign_status_fn` and used
  `_campaign_status_fn` correctly inside `_campaign()`, but never declared the module-level
  `_campaign_status_fn = None` next to `_edit_fn`/`_restart_fn`/`_rollback_fn`/`_record_activity_fn`
  -- in production this would `NameError` on the very first campaign run after merge, not just
  silently no-op. (2) `cerebral/main.py`'s wiring-table registration
  (`("self_dev", "set_campaign_status_fn", _self_dev_campaign_status)`) was never added, so even
  past bug (1), nothing would have ever called the setter for real. (3) The PR's own new tests
  called `plugin.set_campaign_status_fn(...)` as an instance method -- it's a module-level function,
  matching `set_edit_fn`/`set_restart_fn`'s real convention -- so both new tests were themselves
  broken (`AttributeError`) before ever reaching the code under test; they never could have caught
  bugs (1) or (2). Fixed all three, made the test spy async (the real seam is awaited), added
  try/finally cleanup so the module-global doesn't leak across tests. Full self_dev suite re-run
  locally clean, 136 passed. `tray/main.js` syntax-checked with `node --check` (no test harness
  exists for this file at all).
