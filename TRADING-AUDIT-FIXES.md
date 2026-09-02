# TRADING-AUDIT-FIXES.md -- self_dev campaign driver for the 2026-09-01 Opus audit

Source: a read-only Opus-model audit of `cerebral/trading/*`, `plugins/scheduler.py`, and their
tests, run 2026-09-01 (see the Claude Code session that produced it -- 47 tool calls, findings
cross-checked against live `cerebral/data/*.db`, `felix-settings.json`, and `cerebral.err.log`,
not just static code reading). Full original writeup is in that session's transcript; this file's
own queue entries (and the GitHub issues they point at) are the actionable, near-diff-level specs
distilled from it -- read an issue directly (`gh issue view #N`) for the full reasoning behind any
one fix, not just the "what to change."

**Read before running this campaign:** every single slice landed against `cerebral/trading/`
across this whole project's history (TRADING.md, 48+ slices) has needed hand-review, and the
large majority shipped a real bug self_dev's own tests didn't catch -- wrong field names,
half-implemented issues, dead code paths, a comparison that's technically right but wrong in
practice. Assume the same is true here. **Hand-verify every PR's actual diff before merging, even
on a green sandbox test run** -- this codebase's `tests_failed`/timeout sandbox verdict has also
been confirmed environmental (not a real signal) multiple times; the only reliable check is
reading the real diff and running the real tests locally.

Each issue below is scoped to one file (or, where a fix inherently spans two closely-related
files, explicitly says so) and written at near-diff-level detail -- this is the technique that's
gotten self_dev to a clean first attempt most reliably on this codebase. If a slice comes back
with "Edit step produced no commit" more than once or twice, don't just keep retrying it as-is --
see `feedback_selfdev_squash_diff_trap` / the S17 entry in TRADING.md's own history for the
reword-and-purge-ledger remedy.

**None of these fixes require touching `tray/`.** Standard practice for this whole project: never
send `tray/` to self_dev.

## Status: ready

## Next slice -- start here

- **Active:** AF1 -- #995
- **Model:** sonnet

AF9, AF7, AF5, AF6, AF16 landed 2026-09-02 (details in Landed PRs). AF16 was the first slice to
land its generated diff UNCHANGED -- correctly took the issue's own permitted fallback (a FIXME
comment, no code change) since `SchedulerPlugin` doesn't expose a broker reference inside
`_run_gauntlet` without a larger refactor; `tests_failed` on a comment-only diff was confirmed the
known environmental sandbox flake (104/104 + full suite green locally). Every other slice so far
has needed a real hand-fix. Being run live/attended (the scheduled 1am unattended run never
started at all; separately, mid-campaign, live-reproduced what's probably the real cause -- the
whole asyncio event loop can stall completely, CPU-idle, heartbeats included, not just scheduler
dispatch -- see project_trading_campaign memory for the full account, not yet root-caused to an
exact call site, worth its own debugging session).

## Queue

Ordered by the audit's own severity ranking (most severe/most confident first), not by ease --
expect the early slices to need more hand-review time, not less.

**Re-ranked 2026-09-01 evening against real same-day trading data** (a second Opus pass, this
time reviewing actual fills/logs/DB rows from today rather than static code) -- see "What today's
real data changed" below the queue for the evidence. Three items moved up because today's data
confirmed they're not theoretical: AF7/#1001 (discovery has NEVER validated a single strategy,
17/20 all-time attempts including all 10 today hit the exact all-flat signature), AF5+AF6/#999+#1000
(a single stuck position vetoed 19% of the whole strategy portfolio all day via this gate), and
AF16/#1010 (caught red-handed: 149 real specs ran for 3 days at the WRONG sizing because of this
exact drift). AF1/#995 moved down -- real code, but today's data found zero evidence it has fired
yet; not worth the review time until AF9 is fixed and positions actually live long enough to reach
a 5%/30% move.

- [x] AF9 -- #1003 -- AlpacaBrokerClient.list_positions ignores strategy_id, every strategy shares one position pool
- [x] AF7 -- #1001 -- sandboxed strategy evaluation silently degrades to all-flat on numpy/pandas signal types
- [x] AF5 -- #999 -- correlation risk gate computed on price levels instead of returns
- [x] AF6 -- #1000 -- correlation matrix rebuilt from scratch on every strategy open instead of once per dispatch pass
- [x] AF16 -- #1010 -- registration-time position sizing and the live risk cap read two different equity numbers
- [ ] AF1 -- #995 -- TP/SL backstop compares live price against yesterday's bar close
- [ ] AF3 -- #997 -- vs_random gauntlet gate is an off-by-one that always returns 0.0
- [ ] AF2 -- #996 -- Sharpe ratio annualized by ann_factor instead of sqrt(ann_factor)
- [ ] AF8 -- #1002 -- rank_for_day_trading's $5 price floor drops the cheap tickers added for penny-stock coverage
- [ ] AF21 -- #1015 -- discovery only introduces one new known-liquid ticker per pass, badly throttling how fast new symbols enter the watchlist
- [ ] AF10 -- #1004 -- StrategyLifecycle.update_live_fill is never called -- ramp stuck at 25%, live auto-retirement can never fire
- [ ] AF11 -- #1005 -- no guard against trading on stale/frozen market data
- [ ] AF12 -- #1006 -- expectancy/confidence math counts opening fills as real trades, live distinct-days not phase-filtered
- [ ] AF4 -- #998 -- capacity_liquidity gauntlet gate fed a hardcoded position_sizes=1.0 instead of the real registered qty
- [ ] AF13 -- #1007 -- fractional-short guard is checked before the confidence-weight multiplier is applied
- [ ] AF20 -- #1014 -- noise_sensitivity gate's independent per-column price noise violates OHLC bar invariants
- [ ] AF18 -- #1012 -- claim_store's suffix-stripping regex diverges from strategy_store's and fails on dotted tickers
- [ ] AF19 -- #1013 -- extract_ticker's single-letter regex now false-positives on the word F in prose
- [ ] AF17 -- #1011 -- ForwardRecord() constructed inside loops leaks sqlite connections
- [ ] AF14 -- #1008 -- dead code: RiskManager.record_daily_loss and _daily_loss_accrued are never called
- [ ] AF15 -- #1009 -- AlertDispatcher._history grows unbounded in a long-lived process

## What today's real data changed (2026-09-01 evening, 159 fills reviewed)

- **AF9/#1003 confirmed at 100%, not "sometimes"**: every one of today's 79 closed round trips
  (all of them) was closed by a DIFFERENT strategy than opened it -- median hold time 35 seconds.
  Today's -$0.03 P&L and 28W/49L record are churn noise from this bug, not a trading result. This
  is why AF9 was already first and stays first.
- **AF7/#1001 confirmed as the reason discovery has NEVER validated anything, ever**: all 20
  all-time gauntlet attempts are UNVALIDATED; 17 hit `monte_carlo_permutation: p=1.000` exactly
  (the mathematical signature of a completely flat/all-zero backtest), including all 10 run today.
- **AF5+AF6/#999+#1000 confirmed as a live portfolio-wide veto, not a theoretical inefficiency**:
  one stuck INTC position (itself a symptom of AF9) triggered `Correlation 0.80 between AMD and
  INTC exceeds limit 0.7` 30 times today, silencing AMD -- 28 of the 149 registered strategies,
  the single largest symbol block -- for the entire session.
- **AF16/#1010 caught actually happening**: the 149 pre-today specs were registered Aug 27-29 at
  ~10%-of-equity sizing intent but filled all day at ~$1.59 (2%-equity-cap scale) until the setting
  finally caught up around 19:43 UTC -- a real 6.3x, multi-day drift between registration-time
  sizing and the live risk cap, exactly what this issue describes.
- **New finding, not one of the original 20 -- investigated further and filed as AF21/#1015.**
  The 16 cheap tickers added earlier today (`_KNOWN_TICKERS` in `cerebral/trading/discovery.py`)
  DO already feed into `prefilter_candidates`' overflow union (fixed 2026-08-26/08-31) -- the real
  gap is that once the watchlist fills every slot, exactly ONE new overflow symbol is introduced
  per pass, deterministically alphabetical, so it can take many real passes to work through even
  the pre-existing backlog before reaching newly-added symbols. Confirmed as the actual reason only
  6 of 16 reached the watchlist today (and those 6 got there via a coincidental book mention, not
  this mechanism at all). Fix: scale the reserved overflow slots with `limit` (roughly 3 slots
  instead of 1 at the current default `discovery_candidate_limit=10`) instead of hardcoding to 1.
- **AF1/#995 (TP/SL stale price) found no supporting evidence today**: max absolute intraday move
  across all 79 round trips was 0.32% -- nowhere near the 5%/30% thresholds. Real bug, just not
  yet consequential; re-check once AF9 is fixed and positions live longer than a few seconds.

## Explicitly NOT queued -- needs a design pass first, don't send blind

Two things the audit flagged that are real gaps but are design-shaped, not fix-shaped -- sending
them to self_dev without a decided design first is how this campaign has produced its worst
PRs historically:

- **`parameter_sensitivity` gauntlet gate is a structural no-op** (`plugins/scheduler.py` always
  passes `params={}`, so the gate's loop body never runs). The real question underneath this --
  "what parameters does an LLM-generated raw-code strategy actually expose to vary?" -- doesn't
  have an obvious mechanical answer; this system's strategies are generated as opaque functions,
  not parameterized templates. Needs a real decision (redesign the strategy representation to
  expose tunable params, or retire this gate as inapplicable to this architecture) before any
  code gets written.
- **Confidence-weight normalization** (part of the original AF13/AF10-area findings): the audit
  found `compute_confidence_weight` returns a raw dollar mean fed into a formula that assumes a
  unitless ratio, making the multiplier a near-total no-op today. The right normalization (mean
  P&L per dollar of notional risked, or something else) is a real quant design choice, not a
  mechanical bug fix -- get it wrong and sizing gets WORSE, not better. AF13 above only fixes the
  fractional-short-guard ordering bug next to this, deliberately not the normalization itself.

## Landed PRs

- PR #1017 -- AF9 (self_dev generated, hand-fixed: deduped a doubled field declaration and fixed
  a pre-existing test-double bug in `_FakeAlpacaClient` that hardcoded every fill's qty to 10
  regardless of what was actually ordered -- the production `AlpacaBrokerClient` change itself was
  correct as generated). A duplicate PR (#1016, same run, six seconds apart) closed unmerged.
  Commit d3efff2 on master.
- PR #1018 -- AF7 (self_dev generated, hand-fixed: the generated diff was correct but its own new
  test proved the exec namespace strategy code runs in was empty -- seeded `pd`/`np` into it so
  any strategy using them internally works even without self-importing, not just ones whose
  return value happens to serialize cleanly).
- PR #1019 -- AF5 (self_dev generated, hand-fixed: the one-line production fix was correct as
  generated, but the pre-existing `test_tick_blocks_high_correlation_open` test's fixture built
  two symbols correlated on price LEVEL trend, not actual returns -- rebuilt the fixture to
  construct genuinely return-correlated series; added a direct unit test for the fix itself).
- PR #1020 -- AF6 (self_dev generated, hand-fixed: the production diff was correct but 15
  pre-existing tests broke because `FakeScheduler`, a test double, didn't accept the new
  `correlation_matrix` kwarg -- same class of gap as AF9/AF5. Also found and fixed a real
  duplicate-fetch bug while verifying: the once-per-pass symbol list wasn't deduplicated).
- PR #1021 -- AF16 (self_dev generated, landed UNCHANGED -- correctly used the issue's own
  permitted fallback, a FIXME comment documenting the drift rather than forcing a broker-reference
  plumbing change `_run_gauntlet` doesn't have easy access to. `tests_failed` on a comment-only
  diff confirmed the known environmental sandbox flake, not a real failure).
