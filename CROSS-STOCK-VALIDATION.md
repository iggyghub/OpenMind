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

- **Active:** S2 -- #1235
- **Model:** sonnet

## Queue

- [x] S1 -- #1234 -- classify strategies generic vs stock-specific;
  persist the 100-stock basket and the classification result
- [ ] S2 -- #1235 -- cross-stock replay backend: resumable
  (strategy_id, symbol) pair cursor, one 5-year backtest per pair (reuses
  `run_bars`/`evaluate_signals`, NOT `run_gauntlet` -- same reasoning as
  BATCH-REPLAY D1), new results table (this is a different shape than
  `replay_results` -- per pair, not per strategy-month)
- [ ] S3 -- #1236 -- nightly scheduler wiring: one new recurring event
  (same `SchedulerPlugin`/`_scheduler_loop` machinery already driving
  paper-trade dispatch, not a new OS-level scheduled task), fires at
  midnight ET, soft-stops at 8 real-clock hours or 8am ET (whichever
  first), logs actual pairs/night for throughput calibration
- [ ] S4 -- #1237 -- rollup metric: % of the 100-stock basket where a
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
