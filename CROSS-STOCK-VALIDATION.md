# CROSS-STOCK-VALIDATION.md -- Pattern-generalization campaign driver

BATCH-REPLAY (see `BATCH-REPLAY.md`) answers "how has this strategy's rule
performed on its own one registered stock, across every month since 2016."
That's a risk/drawdown-tracking question, not a pattern-validation one --
a rule that fits AMD's 10-year idiosyncrasies looks identical to a real
edge if AMD is the only stock it's ever tested against.

This campaign answers a different question: is a strategy's underlying
rule a genuinely predictable pattern, or did it just curve-fit to the one
symbol it happened to register against? Every `StrategySpec` is hard-wired
to one `symbol` throughout this codebase (registration, dispatch, replay)
-- "run this rule against a different stock" isn't a parameter today.
This campaign adds it, as a second, independent sweep.

Scoped 2026-09-15 in conversation with the user, same style as
BATCH-REPLAY's own scoping (not from a GitHub issue -- grew out of
"every stock with every strategy?" -> "confirm some strategies are
predictable patterns, which takes more testing" -> settling window/basket
size/schedule together).

## Parameters (user-confirmed 2026-09-15)

- **Window per (strategy, stock) pair:** 5 years, one continuous backtest
  (not monthly-chunked like BATCH-REPLAY -- this is a breadth question,
  not a "build up track record over calendar time" one).
- **Stock basket:** 100 symbols, Claude-picked for day/swing-trade
  relevance (liquid, sector-diverse, several already tracked elsewhere in
  the system) -- see S1 for the actual list, stored in code not here so
  it stays a single source of truth.
- **Classification:** strategies whose own description explicitly names a
  stock/company (e.g. "the five Dow stocks...") are exempt -- tested only
  against their own registered symbol, same as today. Everything else
  ("generic pattern") is eligible for the full 100-stock sweep.
- **Schedule:** nightly, midnight-8am ET (user's sleep window), soft-capped
  at 8 real-clock hours -- stops wherever it is when the cap or 8am hits,
  resumes the next night from a persisted cursor.
- **Throughput is unknown, on purpose:** the only real timing data point
  (BATCH-REPLAY's ~8min/286-strategies/1-month) was dominated by
  per-strategy API fetches, not real execution cost, and doesn't transfer
  cleanly to a 5-year single-shot window. Night 1 is the calibration run;
  S3 logs actual pairs/night so later nights (and any completion-estimate
  UI) use measured throughput, not a guess.

## Status: v1 queue done; follow-up queue open

## Next slice -- start here

- **Active:** F1 (#1246) -- make the results table the progress source of
  truth. Do this one first: F3 and F4 both change the same table, and F1
  changes its primary key.
- **Model:** sonnet
- **Hand-review every diff.** self_dev failed outright on three of the five
  v1 slices in this campaign (crashed-on-boot code for S3, no commit for
  S5, test-file-only for S2). Same discipline applies here.

## Follow-up queue (filed 2026-09-15)

From the post-campaign review below. Grouped so each issue is one coherent
change -- findings 1/2/3/5 are a single fix, not three, and splitting them
across branches would produce exactly the conflict cascade this repo has
hit before.

- [ ] F1 -- #1246 -- results table as progress source of truth; delete the
  settings cursor (findings 1, 2, 3, and most of 5). Net code deletion.
- [ ] F2 -- #1247 -- bound the stop path with `wait_for` + `cancel` so a
  wedged pair cannot stall the singular scheduler (finding 4). Independent
  of the others; can land in any order.
- [ ] F3 -- #1248 -- record `benchmark_return` (buy-and-hold) per pair
  (findings 6, 10). Data collection only; commits to no metric decision.
- [ ] F4 -- #1249 -- minimum-trade floor + cost-model sensitivity
  (findings 7, 8).
- [ ] F5 -- #1250 -- **scoping, not a build ticket** -- metric redesign:
  benchmark-relative and magnitude-aware (findings 6, 9).
- [ ] F6 -- #1251 -- **scoping, not a build ticket** -- other validation
  axes: time panel, permutation null, multiple-comparisons deflation,
  parameter stability (findings 10-13).

**Order: F1 -> F3 -> F4** (each touches `cross_stock_results`). F2 is
independent. F5/F6 are `needs-triage` and must not be implemented from
their descriptions -- they record decisions that need a human conversation
first, same discipline as this campaign's own `check_graduation` deferral.

## Queue

- [x] S1 -- #1234 -- classify strategies generic vs stock-specific;
  persist the 100-stock basket and the classification result
- [x] S2 -- #1235 -- cross-stock replay backend: resumable
  (strategy_id, symbol) pair cursor, one 5-year backtest per pair (reuses
  `run_bars`/`evaluate_signals`, NOT `run_gauntlet` -- same reasoning as
  BATCH-REPLAY D1), new results table (this is a different shape than
  `replay_results` -- per pair, not per strategy-month)
- [x] S3 -- #1236 -- nightly scheduler wiring: one new recurring event
  (same `SchedulerPlugin`/`_scheduler_loop` machinery already driving
  paper-trade dispatch, not a new OS-level scheduled task), fires at
  midnight ET, soft-stops at 8 real-clock hours or 8am ET (whichever
  first), logs actual pairs/night for throughput calibration
- [x] S4 -- #1237 -- rollup metric: % of the 100-stock basket where a
  generic strategy shows positive expectancy, persisted per strategy (a
  NEW field -- explicitly not wired into `check_graduation`/
  `check_retirement` as part of this campaign; see SAFETY)
- [x] S5 -- #1238 -- History tab UI section: sweep status/progress
  (mirrors the Batch Replay control added 2026-09-15), pairs/night rate,
  a "most consistent across stocks" list

S1 blocks S2 (need to know which strategies are eligible before sweeping).
S3 depends on S2 (needs the resumable sweep to call). S4 depends on S2's
accumulated results. S5 depends on S2 (status) and S4 (the list).

## Landed PRs

- PR #1239 -- S1: basket + classification heuristic (merged 2026-09-15,
  hand-fixed after self_dev's own attempt correctly self-blocked on
  tests_failed -- real bugs found: the hand-typed 100-stock basket
  actually totaled 119 unique symbols (my own miscount when first
  drafting it in conversation, caught by the test's own len==100
  assertion), `cross_test_eligible` round-tripped as 0.0/1.0 instead of
  False/True (REAL-typed SQLite column, nothing coerced the read back to
  bool), and `is_stock_specific`'s case-insensitive ticker match flagged
  ordinary English words that happen to double as real tickers (ON, GE,
  F, T, C, MA, V) -- "...based on interest rates" misclassified as
  ON-specific. Follow-up commit (028dd46, same day) closed a real gap in
  the merged PR: the one-shot classification pass and the
  `update_cross_test_eligible` persistence method S1's own issue asked
  for were never implemented, only the bare classification function was.
  Ran against the real strategy_specs.db after landing: 306 strategies
  total, 283 generic (eligible for S2's sweep), 23 stock-specific.
- PR #1241 -- S2: resumable cross-stock replay backend (merged
  2026-09-15, hand-implemented from scratch -- self_dev's own attempt
  (PR #1240) only ever produced a test file; no implementation module
  was ever written, and the abandoned test's mocks assumed a
  `bar_cache.BarCache` class that doesn't exist anywhere in this
  codebase. #1240 closed unmerged. `run_pair()`/`build_pairs()` in
  `cross_stock_replay.py`, `CrossStockStore` mirroring `ReplayStore`'s
  shape, and `start_cross_stock_replay`/`stop_cross_stock_replay`/
  `get_cross_stock_replay_status` in `plugins/trading_replay.py` --
  same resumable-cursor architecture as batch replay, but the cursor
  names the last-completed `(strategy_id, symbol)` pair directly rather
  than a raw index, so a newly-classified strategy landing between
  nights can't shift an index and silently skip/re-run pairs.
  Hand-verified live via the IPC bridge same day: `get_cross_stock_replay_status`
  correctly reported 28,300 total pairs (283 eligible strategies x 100
  basket symbols); a real ~20s run processed 13 pairs against live Alpaca
  data (~1.5s/pair once bar_cache is warm) with genuinely varied real
  results per symbol (e.g. one strategy: AAPL net_return +0.55/323 trades,
  MSFT -0.01/7 trades, GOOGL +0.52/342 trades) -- confirming the sweep is
  actually surfacing real cross-stock variance, not a stub. Stopped
  manually after verification; S3 owns deciding when this runs
  unattended.
- PR #1243 -- S3: nightly scheduler wiring (merged 2026-09-15, hand-
  implemented from scratch -- self_dev's own attempt (PR #1242) would
  have **crashed Cerebral on next boot**: `_scheduler_plugin.events.append(...)`
  at module level, but `SchedulerPlugin` has no `events` attribute (it's
  SQLite-backed, not a list) -- an `AttributeError` at import time, which
  is exactly why the sandbox's own test collection failed on an unrelated
  file that merely imports `cerebral.main`. Also used `time.monotonic()`
  with no `import time`, and defined a soft-cap checker nothing ever
  called. #1242 closed unmerged. Real implementation is a plain NY-hour
  check on `_scheduler_loop`'s existing 5-minute tick (not a new
  recurring event -- confirmed by reading `_recurrence_interval` first
  that "daily" recurrence is elapsed-time-since-last-run, not clock-hour-
  anchored, exactly the drift risk this issue's own text flagged), via
  `check_cross_stock_night_window()` with an injectable `now` for direct
  unit testing. Also fixed S2's sweep loop to log actual pairs processed
  per run, not just "pairs available" -- this issue's own throughput-
  logging ask. Hand-verified live: restarted Cerebral, confirmed **no
  AttributeError anywhere in the fresh boot log**, `scheduler_heartbeat`
  advancing normally with zero "Scheduler loop iteration failed"
  warnings, and `get_cross_stock_replay_status` still working post-
  restart -- this was the highest-stakes verification in the campaign so
  far given what the original attempt would have done to a live restart.
- PR #1244 -- S4: rollup consistency metric (merged 2026-09-15, hand-
  fixed after self_dev's own attempt blocked on tests_failed with a
  truncated reason string that never showed the real error -- root cause:
  the new tests referenced `StrategyStore`/`CrossStockStore`/
  `rollup_consistency` without importing any of them, a `NameError` at
  test execution). Also found while reviewing (self_dev's own tests never
  recorded a failed pair, so this never surfaced): the rollup SQL had no
  `WHERE net_return IS NOT NULL`, so a failed pair (missing bars, sandbox
  error) evaluated its CASE to 0.0 and got averaged in as a NEGATIVE
  result -- conflating "couldn't test this stock" with "tested it and
  lost," exactly the fabricated-signal class this campaign's own SAFETY
  section warns against. And separately: nothing in the real sweep loop
  ever called `rollup_consistency` at all -- wired it to run once per
  sweep pass. `cross_stock_consistency` stays an informational-only field
  per SAFETY below; nothing in this PR touches `check_graduation` or
  `check_retirement`.
- PR #1245 -- S5: History tab UI section (merged 2026-09-15, hand-
  implemented from scratch -- self_dev's own attempt produced no commit
  at all, "Edit step produced no commit -- aborting," the same UI-slice
  failure mode BATCH-REPLAY's own S3 timeline hit twice before being
  hand-implemented; went straight there rather than retrying a third
  time). `renderCrossStockPanel`, a second independent control inside
  the same History tab `renderReplayPanel` occupies -- Start/Stop, pairs
  progress, a completion estimate (only shown once real throughput
  exists, never fabricated from zero data), and a most-consistent list
  paired with each strategy's sample size. Needed real backend additions
  to have data to show: `get_tested_count_by_strategy()`, and
  `get_cross_stock_replay_status`'s new `last_run_processed`/
  `last_run_rate_per_hour`/`top_consistent` fields (measured and
  persisted at the end of each sweep pass, not guessed). A real bug
  caught by the test suite before landing: `_escapeHtml` used a
  `document.createElement('div')` round-trip, which silently produced
  `"undefined"` under this file's own Jest harness (no real DOM) despite
  presumably working in the real Electron renderer -- switched to a
  plain string `.replace()`. **Hand-verified live in the actual running
  tray** (not just Jest, learning directly from this campaign's own S2
  History-tab wiring gap): opened Trading > History, both sections
  render side by side, live polling confirmed via the tool-activity feed
  actually firing `get_cross_stock_replay_status` every 2s, real data
  displayed (`Pairs: 22 / 28300`, matching the S2 verification run's
  leftover state; correct empty-state text for throughput/consistency
  since that 22-pair run predated S4's rollup wiring).

## Campaign complete (2026-09-15)

All 5 slices landed and hand-verified live. self_dev produced a usable
(if buggy) first attempt on S1 and S4; it failed entirely on S2 (test-
file-only, invented a nonexistent API), S3 (would have crashed Cerebral
on boot), and S5 (no commit at all) -- every one of those three was
hand-implemented from scratch rather than iterated on. The nightly sweep
is wired and will start on its own tonight (midnight-8am ET, 8h soft
cap); the History tab now shows both this campaign's cross-stock sweep
and BATCH-REPLAY's own sweep side by side.

## Follow-ups -- review findings 2026-09-15 (not yet scoped into slices)

Found in a post-campaign read of the landed code, before the first real
nightly run. None of these were regressions introduced by a bad self_dev
diff -- they survived hand-review because each one reads as correct in
isolation. Ordered by severity.

**Correctness -- fix before trusting any accumulated results:**

1. **A deleted strategy silently restarts the whole sweep AND corrupts the
   consistency metric.** `_find_cursor_position` (`plugins/trading_replay.py:570`)
   returns 0 when the cursor pair isn't found, and its own docstring names
   "the strategy was deleted" as an expected cause. Deletion is a live,
   autonomous operation here -- `auto_combine_strategies`
   (`plugins/trading_strategies.py:445`) hard-deletes the losing strategy via
   `StrategyStore.delete` (`cerebral/trading/strategy_store.py:307`). The
   restart wastes a night, but the real damage is silent: each pass calls
   `create_run()` for a fresh `run_id`, the results PK is
   `(run_id, strategy_id, symbol)`, and `get_consistency_by_strategy` has no
   run filter -- so re-swept pairs insert as NEW rows and get averaged
   twice. Note the `ON CONFLICT` at `cross_stock_store.py:65` can never fire
   as written (always-fresh `run_id`); it reads as dedupe protection but is
   dead code.
2. **Per-pair exceptions leave no row at all.** `store.record_result` sits
   inside the `try` (`trading_replay.py:643`) while the cursor advance sits
   correctly outside (`:652`). An unexpected raise from `run_pair` -- as
   opposed to a `flat_reason` it handles internally -- skips the row, advances
   past it, and never retries. Those pairs become undiscoverable holes, since
   `pairs_done` is cursor-derived and never reconciled against the table.
3. **The sweep never runs again once complete.** At the end of the pairs
   list `pairs[start_idx:]` is empty, so every subsequent night starts a
   task, writes an empty `cross_stock_runs` row, processes nothing, exits.
   No completion state, no refresh cadence, no re-test of stale pairs --
   while the 5-year window is anchored to `today` (`trading_replay.py:621`),
   so results freeze at whenever each pair happened to run.

**Recommended fix for 1-3 together: delete the settings cursor and make the
results table the source of truth.** PK becomes `(strategy_id, symbol)` with
`run_id` demoted to a plain column; resume by skipping pairs already present.
This is a net code *deletion* and collapses all three: deletion/reordering/
new-strategy cases self-heal, re-runs upsert instead of duplicating, the
holes in (2) retry automatically, `pairs_done` becomes a trivial `COUNT(*)`,
and (3)'s refresh story becomes `DELETE FROM cross_stock_results WHERE
created_at < ?`.

**Robustness:**

4. **`stop_cross_stock_replay()` can stall the entire scheduler.**
   `trading_replay.py:557` does a bare `await _cross_stock_task`, called from
   `check_cross_stock_night_window` -> `_scheduler_loop`
   (`cerebral/main.py:3917`). The stop flag is only checked at the top of each
   pair iteration and nothing in `run_pair` -> `bar_cache.get_bars` has a
   network timeout, so a hung fetch hangs the await, which hangs the shared
   5-minute tick -- paper-trade dispatch included. ADR-0028 R5: the scheduler
   is singular, and a background job must not be able to block it. The 8h
   soft cap routes through the same await, so it can't rescue this either.
   Fix: `asyncio.wait_for(..., timeout=120)` + `task.cancel()` on timeout.
5. **The status poll is heavy and leaks connections.**
   `get_cross_stock_replay_status` does a full `list_all()`, rebuilds all
   28,300 pairs, linear-scans them, opens fresh `CrossStockStore()` and
   `StrategyStore()` -- neither ever closed, no context manager
   (`cross_stock_store.py:18`) -- plus a full-table `GROUP BY`. S5's UI polls
   it **every 2 seconds** while the History tab is open, contending with the
   CPU-bound sweep it reports on. Mostly dissolves once (1)'s fix makes
   `pairs_done` a `COUNT(*)`.

**The metric itself -- needs its own scoping conversation, same discipline
as the `check_graduation` deferral in SAFETY below:**

6. **No benchmark.** `AVG(CASE WHEN net_return > 0 ...)` is a sign test
   against zero, over 100 hand-picked survivors, in a window the market
   spent mostly rising. Any long-biased strategy scores near 1.0 because the
   stocks went up, not because the rule generalizes -- so "most consistent
   across stocks" will rank closet-beta first, defeating this campaign's
   entire stated purpose. Cheap fix, bars are already fetched: score
   `strategy_return - buy_and_hold_return` on the same symbol/window. Store
   excess return per pair so the rollup can be re-derived later without
   re-running backtests.
7. **No minimum-trade floor.** This campaign's own S2 verification data
   shows it: `MSFT -0.01/7 trades`. Seven trades in five years is noise
   casting a full vote, equal in weight to a 342-trade result. `n_trades` is
   already stored; `WHERE n_trades >= 20` is one line.
8. **Zero-cost baseline, on a sign test -- and inconsistent with the
   gauntlet.** `cerebral/trading/replay.py:77` sets `cost_config = {}` -- an
   explicit zero-cost baseline that `run_pair` inherits, so every
   `net_return` here is gross of commissions and slippage. Because the
   metric thresholds on *sign*, it's maximally sensitive to exactly that
   offset, and high-turnover strategies are systematically flattered --
   compounding with (7), which lets high-turnover noise in unfiltered.
   The sharper problem: the gauntlet, which decides whether a strategy
   actually graduates, uses real spread costs
   (`cerebral/trading/gauntlet.py:77` and `:122`:
   `{"min_spread_pct": 0.01, "max_spread_pct": 0.03}`). So strategies are
   **validated for promotion under realistic costs but measured for
   cross-stock consistency under zero costs** -- the two numbers aren't
   comparable, and the cross-stock one is the more optimistic. Directly
   relevant to (6)/F5: wiring consistency into a graduation gate would mix
   a zero-cost signal into a cost-aware one. Note `replay.py:77`'s comment
   justifies itself as "reuse gauntlet default convention" when the
   gauntlet's convention is not zero-cost -- the comment is wrong about
   what it cites, which is likely how this survived review.
9. **Binary sign discards magnitude.** +0.1% on 60 stocks and -40% on 40
   scores 0.60 and reads as "consistent." Median excess return, or
   mean/stdev across the basket, costs the same query and carries far more
   signal.

**Methodology gaps -- other axes of the same question, see the review
conversation 2026-09-15:**

10. **100 correlated large-caps in one window is far less independent than
    it looks.** Effective sample size is closer to a handful than to 100, so
    cross-stock breadth alone can't defeat curve-fitting to *this regime* --
    every pair shares the same 5 years. Time-axis validation is the more
    orthogonal test, and BATCH-REPLAY already holds that data (per
    strategy-month since 2016); combining the two sweeps into one panel is
    strictly more informative than either alone.
11. **Random-entry / permutation baseline** is the cheapest real
    null-hypothesis test and reuses `run_bars_verbose` directly: same trade
    count and holding period, random entry times (or shuffled bar returns).
    A strategy that can't beat matched-exposure random entry has no edge,
    which is the rigorous version of (6).
12. **Multiple-comparisons deflation.** 306 strategies x 100 stocks =
    28,300 tests; roughly 1,400 will clear p<0.05 by pure chance. Nothing
    currently tracks the number of trials, so "we found 40 great strategies"
    is not yet distinguishable from "we found exactly what chance predicts."
    Relevant precedent: the ~18 near-duplicate strategies already found and
    deferred elsewhere in the trading campaign -- near-dupes make the
    "independent" confirmations less independent than the count suggests.
13. **Parameter-neighborhood stability** has the highest diagnostic power
    for overfitting and is the most work: perturb the strategy's numeric
    constants +/-10-20% and check that performance degrades smoothly rather
    than falling off a cliff. These are LLM-generated strategies so the
    constants are code literals, not a declared parameter vector -- needs an
    AST walk over numeric literals, then the existing sandboxed eval.

## SAFETY

- **No slice may place an order, real or paper.** Same constraint as
  BATCH-REPLAY: this reads history and writes a results row, nothing else.
  `run_gauntlet`/`auto_promote` must never be called from this path.
- **The new rollup metric (S4) is informational only in this campaign.**
  Wiring it into `check_graduation`'s refusal gate or any other live
  decision is explicitly out of scope here -- a deliberate, separate
  decision for later, not a default extension once the number exists
  (same discipline BATCH-REPLAY's own S4 held to for `check_retirement`'s
  unit mismatch).
- **Classification (S1) errs toward "stock-specific" on ambiguity.**
  Wrongly sweeping a stock-specific strategy against 100 stocks wastes
  compute; wrongly exempting a genuinely generic strategy just means it
  keeps only its current single-symbol history a while longer. The first
  failure mode is cheap and reversible, so ambiguous cases should default
  to exempt, not swept.
- **This is a second, independent sweep from BATCH-REPLAY's --
  they run concurrently, not instead of each other.** Both are resumable
  background jobs; neither should assume it has the machine to itself.
  Watch for the same thread-pool/scheduler contention concerns as any
  other concurrent trading-domain background job (ADR-0028 rule 5: the
  scheduler is singular).
