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

## Status: active

## Next slice -- start here

- **Active:** S5 -- #1238
- **Model:** sonnet

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
- [ ] S5 -- #1238 -- History tab UI section: sweep status/progress
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
