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

## Random-timing baseline re-run at 2 bps (2026-09-20)

Cost unified to 2 bps per side of traded notional (commission-free platform; #1329 comment). Same 1,978 pairs:
p<0.05 on 10.2%, p<0.01 on 3.0%, observed beat the null median on 57.2%. **0 of 247 strategies significant**
after adjustment -- unchanged conclusion.

## Regime stress windows result (2026-09-20, PR #1331)

165 causal daily strategies x 30 large caps with 2008 history (4,939 pairs, one sandbox run per pair over
2006->today, position sliced per window, 2 bps cost, yfinance adjusted bars, survivorship-biased).
Windows: gfc (2007-10..2010-01), mid (2010s), bear22 (2021-12..2023-01), main (rolling 5y).
- **Robust (beats buy-and-hold on >=half the stocks with positive median excess in EVERY window): 0 of 165.**
- **Defensive (every window: median excess >= -10% and drawdown cut >= 10 points): 0 of 165.**
- Per-window strategies significant vs buy-and-hold (coin-flip sign test, BH-adjusted): gfc 1, mid 0, main 0,
  **bear22 126 -- an artifact, not an edge.** Median strategy is nearly flat (bear22 median return -1.7% vs
  buy-and-hold -26%; 20% of pairs within +-2% of zero; median 24 trades): a rule that mostly sits in cash "beats"
  a falling market. The same rules lose to buy-and-hold in the 2010s (beat share 3%) and the 5y window (13%).
  Requiring all windows at once is what removes the artifact -- nobody passes.
- Median max drawdown: strategy -14% vs buy-and-hold -39% in bear22, but the strategy is barely invested; the
  protection is exposure, not timing skill (the permutation test is the timing check).
- **Verdict: no book strategy has a robust edge; the "defensive" ones are just under-invested.** Null result.
- Next: out-of-sample selection (pick on main, judge on gfc/bear22), better strategy generation (many rules are
  vague or leaky), 15-20y windows, operator decision on #1329.

## Strategy review category and a correction to the counts (2026-09-20, PR #1334)

53 strategies were named by pasted web-search text (`<<<EXTERNAL_UNTRUSTED_CONTENT`) and shared only 4 distinct
code bodies that do not implement the claim they are named after. They are now held in `strategy_review` (category
`untrusted_text`): kept in the DB, excluded from rankings, sweeps and the consistency rollup, listed in the status
payload and on the History tab, releasable with `clear_review`. Effect: the 5-year sweep now ranks **176** strategies
(was 228), the permutation test **195** (was 247), the stress run **167** -- all still **0 significant / 0 robust /
0 defensive**. The conclusion did not change.

## Day trading: true intraday rules (2026-09-20, PRs #1333-#1337)

The book library held NO real day-trading rules: the interval guess falls back to `1d`, so Aziz's gap scans and
"13-bar Bollinger for intraday charts" were backtested on daily bars. Building the test exposed three bar-cache bugs,
all fixed: (1) intraday bars were keyed by date only, collapsing each day to one bar (#1333); (2) gap-fill only reached
forward, so an old fragment hid everything before it, and the fetch end could touch Alpaca's blocked last-15-minutes
window (#1335); (3) every read took the write lock, so parallel workers hit `database is locked` (#1337).

Eight hand-authored 5-minute rules (`cerebral/trading/intraday_rules.py`: ORB, VWAP reversion, VWAP trend, gap-and-go,
gap fade, intraday Bollinger, first-half-hour momentum, EMA9/20+VWAP), regular session, flat by the close, 0 look-ahead
mismatches. 30 large caps x 2020-2026 (131k regular-session bars each), 2 bps per side, tool `start_intraday_research`.
- **0 of 8 profitable after costs in every regime.** Across all 936 (rule, stock, window) results: gross > 0 on 45%,
  net > 0 on 18%; median gross -1.9%, median net -19.2%. There is no raw timing edge for costs to erode, and costs
  (hundreds to thousands of trades) finish the job. The high-turnover trend rules (VWAP trend, EMA+VWAP) lose 25-59%.
- Only one window-level hit: first-half-hour momentum in the 2020 covid window (81% of stocks positive, +8.1% net,
  q=0.01) -- and 0-3% positive in every other window. A one-regime fluke, not an edge.
- Caveats: 8 fixed, untuned rules (a null on these variants does not rule out other rules); survivorship-biased large
  caps; correlated stocks so p-values are optimistic; 2 bps per side is generous for 5-minute turnover.
- **Verdict: no robust day-trading edge among the classic book rules.** Null result.

## Cost model fixed (2026-09-20, PR #1339, closes #1329)

Backtest costs were charged on the share price instead of the traded notional (0.001% on a $5 stock, 0.3% on
$1,500). `Trade.value` is now |delta| x capital and the default is 2 bps per side of notional (commission-free
broker). Gauntlet check: the production gauntlet (`run_gauntlet` via `run_bars`) never applied this cost model;
`oos_test`/`walk_forward` did but have no production caller -- so live promotion is unchanged. Only the replay,
cross-stock and batch-replay research numbers changed. The permutation, stress and intraday results already used
their own 2 bps notional cost and are unaffected.

The pre-fix 5-year sweep (28,300 rows) is kept as table `cross_stock_results_legacy_cost` in
`cross_stock_results.db`; the sweep was cleared and re-started 2026-09-20 under the corrected cost (23,100 pairs,
~11-30 h; interrupted once at 50 pairs by a Felix restart and resumed; finished 2026-09-21, ~24 h wall clock).

**Result (2026-09-21): still null.** 23,100 of 23,100 pairs; 176 strategies ranked, **0 significant** (legacy: 0 of 176). Every BH q-value is 1.0. Across ~18,100 comparable pairs the median net return is -4.9% vs +51.8% buy-and-hold, and 28.3% of pairs beat buy-and-hold (legacy 28.5%), so correcting the cost model barely moved anything; the old share-price cost was not what hid an edge. Only 3 strategies beat buy-and-hold on >= 50% of stocks with a positive median excess, none nominally significant (best: 64% of 33 stocks, p = 0.08; the top-ranked row is 62.5% of 24 stocks, p = 0.15). The 'buy below $70' rule is again the strongest of them under a plain >= 5-trade filter, but it only beats buy-and-hold in the 2022 bear window, where it sits mostly in cash (see stress windows). Batch-replay (`replay_runs.db`) numbers were NOT re-run.
