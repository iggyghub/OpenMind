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

## Status: ready

## Next slice -- start here

- **Active:** UI1 -- #1162
- **Model:** sonnet

## Queue

- [ ] UI1 -- #1162 -- Trade Log: real Paper/Live tabs, column sort, %-gain/loss column

## Landed PRs

(none yet)
