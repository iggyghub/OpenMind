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

## Final result (2026-09-19, after PR #1327 and PR #1328)

A second strengthening was needed: an AAPL-only check passed a strategy whose look-ahead
(`close[ev+j]` future closes) only fires on high-volatility stocks, and that strategy then ranked as the ONE
"significant" winner. The check now also runs on the 2 stocks where each strategy trades most (any leak wins).

- **283/283 verdicts: 28 non-causal, 255 causal, 0 untestable.**
- **228 strategies ranked vs buy-and-hold; 0 significant** after Benjamini-Hochberg adjustment (best q = 1.00). Best
  remaining: 64% beat rate on only 22 stocks (median excess +54%, p=0.14); the rest sit at ~52% with ~0 excess
  (buy-and-hold clones).
- Clean set (23,180 pairs): median return -6.5% vs +41.9% buy-and-hold; 28.4% of pairs beat buy-and-hold.
- **No cross-stock edge survives.** Next-strongest test would be the random-entry permutation baseline (#1251 axis 2).

## Random-timing (permutation) baseline result (2026-09-19, PR #1330)

1,978 (strategy, stock) pairs across 255 causal strategies (each on the 8 stocks where it trades most), 500
circular-shift draws per pair, 5 bps notional cost on strategy and controls alike.
- Nominal p<0.05 on 10.2% of pairs (chance ~5%), p<0.01 on 2.4%; observed beat the null median on 55.8% of pairs
  (median observed -11.4% vs null -16.7%). A faint aggregate tilt, but pairs share stocks and one regime, so it is a
  hint, not a finding.
- **0 of 247 ranked strategies significant** after Bonferroni-over-own-stocks + Benjamini-Hochberg (best q = 0.37).
- Smoke test: both known look-ahead strategies hit the minimum possible p (0.002), so the null also works as a leak detector.
- Separate finding: the backtest cost model charges by share price, not traded notional (issue #1329, needs a decision).
