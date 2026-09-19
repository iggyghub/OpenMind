# FELIX-CAUSALITY.md -- look-ahead (causality) gate for the cross-stock sweep

Found 2026-09-19 reviewing the finished 5-year sweep: the top of the "most consistent across stocks"
ranking was dominated by strategies whose code reads future bars (centered windows etc.; +1,239% median,
100% profitable on 93 stocks). `sandboxed_eval._has_lookahead` only catches `shift(-N)`. A causality test on
the top 40 daily strategies (AAPL, 11 cut points) found 5 non-causal (incl. the top 3 by beat-rate).
Separate from FELIX-AUDIT.md / FELIX-GATE-FOLLOWUP.md so it never collides with them.

Order C1 -> C2 -> C3 is load-bearing (C3 imports C1 and C2). Each slice is small and edits <=3 files.
SAFETY: informational only. Nothing here may touch `check_graduation`, `check_retirement`, `run_gauntlet`,
or `auto_promote`, and no slice places an order.

## Status: ready

## Next slice -- start here

- **Active:** C2 -- #1322
- **Model:** sonnet

## Queue

- [x] C1 -- #1321 -- `check_causality()` (new `cerebral/trading/causality.py` + test)
- [ ] C2 -- #1322 -- persist verdicts; rollup excludes non-causal (`cross_stock_store.py`, `cross_stock_replay.py`)
- [ ] C3 -- #1323 -- sweep runs the check once per strategy (`plugins/trading_replay.py`)

## Landed PRs

- PR #1324 -- C1 (auto-merged by self_dev_campaign)
