# FELIX-CAUSALITY.md -- look-ahead (causality) gate for the cross-stock sweep

Found 2026-09-19 reviewing the finished 5-year sweep: the top of the "most consistent across stocks"
ranking was dominated by strategies whose code reads future bars (centered windows etc.; +1,239% median,
100% profitable on 93 stocks). `sandboxed_eval._has_lookahead` only catches `shift(-N)`. A causality test on
the top 40 daily strategies (AAPL, 11 cut points) found 5 non-causal (incl. the top 3 by beat-rate).
Separate from FELIX-AUDIT.md / FELIX-GATE-FOLLOWUP.md so it never collides with them.

Order C1 -> C2 -> C3 is load-bearing (C3 imports C1 and C2). Each slice is small and edits <=3 files.
SAFETY: informational only. Nothing here may touch `check_graduation`, `check_retirement`, `run_gauntlet`,
or `auto_promote`, and no slice places an order.

## Status: done

## Next slice -- start here

- **Active:** none -- causality gate complete 2026-09-19
- **Model:** sonnet

## Queue

- [x] C1 -- #1321 -- `check_causality()` (new `cerebral/trading/causality.py` + test)
- [x] C2 -- #1322 -- persist verdicts; rollup excludes non-causal (`cross_stock_store.py`, `cross_stock_replay.py`)
- [x] C3 -- #1323 -- sweep runs the check once per strategy (`plugins/trading_replay.py`)

## Landed PRs

- PR #1324 -- C1 (auto-merged by self_dev_campaign)
- PR #1324 -- C1 (auto-merged by self_dev_campaign)
- PR #1325 -- C2 (self_dev-built; tests_failed because Felix silently dropped the CREATE TABLE edit; hand-repaired + rollup gap fixed, full suite 5927 green)
- PR #1326 -- C3 (self_dev wrote only the test file; plugin half hand-built, existing sweep tests stub the causality pass; full suite 5931 green)

Note: 3 of 3 Felix slices in this campaign dropped one edit block (C1 clean; C2 missed CREATE TABLE; C3 missed the whole plugin edit) -- see FELIX-AUDIT.md handoff item 1.

## Result of the first full run (2026-09-19)

- **PR #1327 strengthened the gate after real data showed the first version was too weak:** 11 cut points
  passed two Force Index strategies that 60 caught leaking (6/60, 8/60 on AAPL); the `shift(-N)` static guard
  was treated as "untestable" although it proves a future read. Now: 60 cuts, stop at first mismatch, static
  guard = non-causal, 4 workers.
- **Backfill (283 strategies, ~95 min):** 22 non-causal (2,068 pairs, median return +86.6%), 260 causal, 1
  untestable. Their persisted `cross_stock_consistency` is cleared, so they drop out of the History tab.
- **Clean set (23,629 pairs):** median return -6.1% vs median buy-and-hold +41.9%; 28.6% of pairs beat
  buy-and-hold. Only 3 of 251 clean strategies beat buy-and-hold on >=50% of stocks and their median excess is
  +0.00..+0.08 -- indistinguishable from noise. **No cross-stock edge survives the leak screen.**
- Caveats: the check is a LOWER bound (a leak that shows on no sampled bar is missed); 100 correlated large caps
  in one bull-market window; 1-3% per-trade costs penalise high-turnover rules; effective sample size is small.
- Side find: `conversation_turns` last-N queries sorted a whole 36k-row thread (~9s, blocked the event loop on
  every connect); fixed with (thread_id,id)/(profile_id,id) indexes (commit 6543a58).
