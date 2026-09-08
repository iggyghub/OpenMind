# TRADING-PANEL-UI.md -- self_dev campaign driver: Trade Log tabs/sort/%

Source: 2026-09-08 Claude Code session, same conversation as TICKER-DISCOVERY.md but unrelated
in scope -- this touches `tray/lib/trading-panel.js` only, no dependency on TICKER-DISCOVERY's
slices. Kept as its own driver file rather than folded into that queue so the two campaigns don't
share ordering/dependency state they don't actually have.

**tray/ guardrail note (ADR-0015 decision 5):** `tray/` is listed in `GUARDRAIL_PATHS`
(`plugins/self_dev.py`), but the 2026-08-21 "full auto-merge" amendment made that
informational-only -- it no longer blocks merge. The real risk: self_dev's sandbox test gate runs
`pytest` only and cannot execute `tray/tests/trading-panel.test.js`'s JS suite, so a green sandbox
verdict here proves nothing about whether the JS tests actually pass. **Run the real JS test
command by hand (check `tray/package.json`'s `scripts.test`) before merging, not just the sandbox
verdict.**

## Status: done

## Next slice -- start here

- **Active:** none -- queue fully landed 2026-09-08

## Queue

- [x] UI1 -- #1162 -- Trade Log: real Paper/Live tabs, column sort, %-gain/loss column

## Landed PRs

- PR #1164 -- UI1 (self_dev generated a correct-looking implementation, but the JS test gate this
  driver's own note warned about was the real finding: self_dev's sandbox is pytest-only and never
  ran `tray/tests/trading-panel.test.js` at all. Running it by hand found a REAL regression --
  the new sub-tab wiring called `mount.querySelector('.trd-log-subtabs').addEventListener(...)`
  with no null guard, crashing 2 pre-existing tests against this suite's own fake mount (whose
  `querySelector` always returns `null` -- same convention `_wireTradeLogSection` already guards
  against). Fixed with the same `if (!el) return` pattern. Also added the missing coverage the PR
  shipped without: initial Paper/Live visibility state and the new PnL % column's `data-sort-key`
  wiring -- full jest suite re-run locally clean, 102 passed. The sort logic and % math itself live
  inside `_renderTradeLogRows`, which this test file's existing weak fake mount can't reach at all
  (a pre-existing gap, not introduced by this PR) -- verified by code-reading instead, matching this
  file's own established precedent for click-driven behavior ("verify by hand in the app").
