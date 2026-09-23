# TREND-BASKET.md -- self_dev campaign driver for the breadth-gated trend basket strategy

Source: a grill-with-docs design session, 2026-09-22 (same shape of session that produced
IPO-TRADING.md). User wants a second hand-written strategy in the IPO play's spirit -- validated
once against real historical data, not book-sourced, not LLM-generated -- but a different
mechanism: instead of one ticker triggered by a discrete event (IPO day), a 10-symbol basket
triggered by a market-wide breadth condition. Full design reasoning, every real backtest run
during the session (12 rounds of scratchpad Python against real 2005-2026 daily bars and
2020-2026 5-minute bars), and the rejected alternatives are in `docs/adr/0038-trend-basket-
strategy.md`; `CONTEXT.md`'s new "Trend basket play" glossary entry has the condensed version.

**Read before running this campaign:** every trading-system slice landed via self_dev in this
project's whole history has needed hand-review, and the large majority shipped a real bug
self_dev's own tests didn't catch (see IPO-TRADING.md and FELIX-CAUSALITY.md's own campaign notes
for the pattern). Assume the same is true here. **Hand-verify every PR's real diff before merging,
even on a green sandbox test run.**

**None of these slices touch `tray/`.** The Trading panel UI cleanup flagged separately this
session is a different, unstarted thread -- see "Trading panel UI" below.

**Ordering matters, but loosely.** TREND1 (strategy code) has no dependency on TREND2. TREND3
(the dispatcher) imports from both TREND1 and TREND2, so it must land last.

## Status: done

## Next slice -- start here

- **Active:** none -- all 3 slices landed 2026-09-22, queue complete, and live-verified against
  the real restarted Cerebral process (not just landed -- see PR #1348's Landed PRs entry for
  the live-verification details, including one real live-outage-causing bug found and fixed
  post-landing, and one follow-up gap found but not yet fixed: no reentrancy guard on
  `trend_basket_dispatch`).
- **Model:** sonnet

TREND1 and TREND2 have no dependency on each other and could run in parallel by hand, but this
driver processes the queue in order (TREND1 -> TREND2 -> TREND3) through one `self_dev_campaign`
call -- simpler than parallelizing within the tool, and TREND3 needs both done first regardless.

## Design summary (for hand-review context, not part of any single issue)

**The rule** (validated against real 2005-2026 data across bull/gfc/mid/bear22 regimes and a
real permutation-null significance test -- see the ADR for full numbers, including the honest
caveats): when market breadth (share of the Candidate pool trading above its own 50-day moving
average, read from the prior day's close) exceeds 60% and was NOT above 60% the day before (a
rising edge, not "every day it stays on"), open 10 equal-weighted positions in the pool's top 10
names by **momentum x 20-day realized volatility** (20-day return times 20-day daily-return
stdev, descending -- NOT momentum alone, see below), 10% of equity each. Each position exits
independently on a flat 12% trailing stop from its own peak since entry -- no tightening ratchet,
unlike the IPO play -- hard-capped at 20 trading days. A symbol already held from a still-open
trend-basket position is skipped on a later rising edge (one position at a time per symbol).

**Why momentum x volatility, not momentum alone:** the session's first pass ranked purely by
20-day momentum and validated it against a hand-picked 33-ticker universe, which looked strong
(71-77% beat rate vs. a passive basket). Re-testing against a much larger, already-cached
58-symbol universe (`bars_hist.db`, built by the FELIX-CAUSALITY regime-stress campaign) showed
that result was partly a universe-selection artifact -- pure momentum collapsed to a near-coin-flip
54% beat rate at full scale, because the edge concentrates in high-volatility names and the
33-ticker set happened to be tilted toward them. Multiplying by realized volatility partially
recovers this (58-60% beat rate on the full universe) -- see ADR-0038's "Full-universe correction"
for the full numbers. This is the locked, current selection rule; do not build the momentum-only
version described in any earlier draft of this campaign.

**Position sizing:** 10% per trade fits the EXISTING global `max_per_trade_risk_pct` default --
unlike the IPO play, this needs no `risk_override_pct` plumbing (IPO1-3's whole reason for
existing). Skip that entirely here.

**Gauntlet: no bypass, unlike the IPO play.** This strategy's own entry criteria (50-day MA,
20-day momentum) already require ~50+ days of price history before a symbol is even eligible to
be selected, so every candidate it could ever pick already has enough history for a normal
per-symbol `_run_gauntlet` backtest. Register each selected symbol through the Gauntlet exactly
the way `plugins/trading_strategies.py`'s existing `_expand_strategy_ticker` already does for
Expansion (ADR-0026) -- same fixed strategy code, looped across candidate symbols, each one a
real `_run_gauntlet` call. TREND3 should read `_expand_strategy_ticker` first and reuse its shape
rather than inventing a new dispatch pattern.

**Candidate pool:** `cerebral/trading/discovery.py`'s `build_dynamic_universe` (already used by
Expansion and Discovery) is the source, not a hand-picked list. The session's own backtests used
`bars_hist.db`'s 58-symbol research cache as the closest free, already-local proxy for "a broad
universe" -- the live implementation must use the real Candidate pool.

**Validation status (be honest about this in any follow-up):** on the full 58-symbol universe,
momentum x volatility selection beats a passive same-universe basket on 58-60% of gate events
(not the stronger 71-77% an earlier, narrower 33-ticker test showed -- see ADR-0038's
"Full-universe correction"). A real permutation-null significance test
(`cerebral/trading/permutation_null.py`, 500 circular-shift draws/symbol) found 0/58 symbols
individually significant after Benjamini-Hochberg correction -- same headline every one of the 283
book strategies got. What IS solid: absolute participation -- positive median return and a 56-61%
win rate specifically during gate-on (breadth-trending-up) conditions, holding across every
universe and selection variant tested. Regime-stress (gfc/mid/bear22/main), re-run on the full
58-symbol universe with the locked selection: every window shows positive median excess and
beat-rate above 50% (gfc +0.49%/53%/n=19, mid +0.45%/58%/n=113, bear22 +3.76%/67%/n=6,
main +0.82%/60%/n=50) -- modest everywhere, bear22 still thin, but no window failed, unlike every
one of the 165 book strategies FELIX-CAUSALITY.md stress-tested.

**Strategy code's causality/look-ahead check: run 2026-09-22, causal.** `check_causality`
(`cerebral/trading/causality.py`) against real AAPL 2015-2026 daily bars: `causal=True,
mismatches=0, tested=60` -- no look-ahead in TREND1's code.

**Known follow-ups, filed but not built:** #1349 (real 10% position sizing -- currently defaults
to the global 2% setting, see PR #1347's Landed PRs entry) and #1350 (reentrancy guard on
`trend_basket_dispatch`, found during live verification -- see PR #1348's entry).

## Queue

- [x] TREND1 -- #1342 -- hand-written strategy code (new `cerebral/trading/trend_basket_strategy.py`,
      mirroring `ipo_strategy.py`'s shape/docstring convention): a `TREND_BASKET_STRATEGY_CODE`
      string implementing peak-tracking with a flat 12% trailing stop (no tightening ratchet) and
      a 20-bar hard cap, assuming bar 0 of the data passed in is the entry bar (same convention
      IPO's code uses). Same stop-check-before-peak-update ordering IPO's code uses (fixed
      2026-09-03 after a same-bar-inflated-peak bug) -- get this right the first time, don't
      reintroduce that bug. Unit tests covering: stops out on a 12%+ pullback, holds through a
      pullback under 12%, hard-exits at bar 20 if never stopped out, and a regression test for the
      same-bar-peak-inflation case IPO's history already found.
- [x] TREND2 -- #1343 -- breadth gate + momentum x volatility ranking (new `cerebral/trading/
      trend_basket_selection.py`) -- **NOTE: issue #1343's original body describes momentum-only
      ranking; see the comment added on that issue 2026-09-22 for the corrected momentum x
      volatility formula before building this.** A function computing market breadth (share of a
      candidate list trading above its own 50-day moving average) from cached daily bars, and a
      ranking function scoring candidates by 20-day return times 20-day realized volatility
      (daily-return stdev over the same window), descending -- same shape as
      `discovery.rank_for_day_trading` but this scoring formula instead of ATR/liquidity fitness --
      reuse that function's liquidity/price filters, don't duplicate them. A small stateful gate
      class caching one breadth reading per day and exposing whether today is a RISING EDGE
      (breadth just crossed above 60%, wasn't above it yesterday) -- same one-reading-per-day
      caching shape as `cerebral/trading/market_trend.py`'s `MarketTrendGate`. Unit tests: breadth
      computation on a small synthetic price panel, rising-edge detection across a few days of
      readings (on -> on is not a new edge, off -> on is), and the momentum x volatility scoring
      order on synthetic data.
- [x] TREND3 -- #1344 -- wire it together (new tool(s) in `plugins/trading_strategies.py` or a small new
      plugin; `cerebral/main.py` scheduler job table): a per-tick check (cheap -- one cached daily
      breadth read, same posture as `_job_ipo_dispatch`, no separate weekly-refresh job needed
      since breadth isn't sourced from an external calendar) that, on a rising edge, builds the
      candidate pool via `build_dynamic_universe`, filters it through `discovery.rank_for_day_
      trading`'s liquidity/price screen FIRST, then ranks the survivors by TREND2's `rank_by_
      momentum` (TREND2's own docstring pushes liquidity filtering to the caller -- this is that
      caller), takes the top 10 not already actively held from a prior still-open trend-basket
      position, and registers each through `_run_gauntlet` with TREND1's fixed code -- **read
      `_expand_strategy_ticker` in `plugins/trading_strategies.py` first and reuse its per-symbol
      Gauntlet-loop shape** rather than inventing a new one. **Call `RisingEdgeGate.refresh(breadth,
      today)` every tick BEFORE reading `.current`** -- its constructor defaults `.current` to
      `True` ("fail-open initial state", TREND2's own test asserts this), so reading `.current` cold
      without ever calling `refresh()` first would misread a never-initialized gate as an active
      rising edge and dispatch on fabricated state. Activity-logs dispatched symbols + verdicts via
      the existing `_record_activity_fn` path, same convention as IPO6's tools. Needs a way to tell
      "already actively held from a still-open trend-basket position" -- check `StrategyLifecycle`/
      `ForwardRecord` for a still-open position on that symbol from this strategy's provenance
      rather than inventing a new tracking table.

## Trading panel UI

Flagged in this session as in-scope, not yet started as its own thread -- the user wants the
existing Trading panel UI cleaned up, separate from this strategy's mechanism. No design work has
happened on it yet; needs its own scoping pass (probably its own short grill or at least a plan)
before any self_dev slice, standard practice for `tray/` work in this repo.

## Landed PRs

- PR #1348 -- hand-authored emergency fix, not a self_dev slice (2026-09-22, after all 3 slices
  above landed). `_job_trend_basket_dispatch` was wired into `_scheduler_loop`'s automatic 5-minute
  tick with no thread offload -- its real work (real network fetches + up to 10 real Gauntlet
  backtests) ran on the main event loop. **Confirmed as a real live outage**, not a theoretical
  risk: after restarting Cerebral to verify TREND3, the process bound port 7766 and logged a normal
  boot, but every WebSocket handshake hung and existing connections piled up in `CloseWait` -- the
  loop was alive but wedged, invisible to every unit test since none of them exercise the live
  scheduler loop under a real WS server. Fixed by running the dispatch inside `asyncio.run()` on
  its own event loop via `asyncio.to_thread`, matching this project's own existing precedent for
  the same hazard (`_market_trend_gate.refresh`'s own to_thread offload; S14/#859's identical fix
  for the per-strategy dispatch pass in this same loop). Full suite re-run locally clean, 6029
  passed/7 skipped/0 failed. Merged directly (not through self_dev) given the live-outage urgency.
  **Requires another Cerebral restart to take effect** -- the process running at merge time still
  has the pre-fix code in memory.

  **Live-verified after restart, 2026-09-22 22:29-22:40 UTC**: fired `trend_basket_dispatch` via
  the real IPC bridge 3 times against the restarted process. Each call made the server briefly
  busy (real network fetches across the candidate pool take real wall-clock time -- observed
  ~80-100s+ per call with a cold/uncached pool) but **it self-recovered every time**, unlike the
  pre-fix bug which never recovered on its own -- confirms the fix is real, not just theoretically
  correct. Zero errors in `cerebral.err.log` across all 3 calls (grepped specifically for
  `trend_basket`/`Trend basket`), zero unwanted writes to `strategy_specs.db` -- consistent with
  the tool correctly finding no rising edge and cleanly no-oping (expected on most days: real
  rising edges happened only 104-221 times across an 11-year/58-symbol backtest, not daily). Did
  not manage to capture the exact JSON reply content itself (a benign mismatch between a quick
  ad-hoc test client's patience and a genuinely slow real operation, not a code bug).

  **Follow-up found here (#1350), fixed same day** -- see the pool-size entry below: the
  reentrancy guard landed alongside raising the candidate pool size and parallelizing the fetch
  loop, since all three were the same investigation.

- Hand-authored fix, not a self_dev slice (2026-09-23, prompted by "the trading tab looks empty,
  is this working?"). Investigating why nothing had dispatched found a real, more fundamental gap
  than "hasn't had a chance to fire yet": live breadth reads swung 37.5%-62.5% across back-to-back
  calls under the same real market conditions, because `build_dynamic_universe`'s own default
  filter (min_price=$1, min_dollar_volume=$5M, tuned for Discovery/Expansion's day-trading use
  case) let a tiny (6-9 symbol), effectively-random pool of penny/micro-cap noise through every
  call -- confirmed live, none of the draws resembled the backtested universe at all. Fixed by
  passing a stricter liquidity floor ($2/$10M) explicit to the trend-basket call site (not changed
  as the shared function's own default, per ADR-0026 decision 5). Verified against real historical
  data before shipping: all 13 high-volatility names the permutation-null check found the actual
  edge concentrated in (RIOT, SOFI, MARA, NIO, ITUB, BBD, SNAP, LYFT, PBR, IBM, F, GRAB, PLUG)
  clear the new filter with real margin -- the tightest case (PLUG, $2.16) still has 14x the
  dollar-volume floor, confirming this targets liquidity, not volatility. Also fixed a related
  inconsistency found in the same investigation: breadth was computed against the fully
  unfiltered universe while selection alone went through a second, redundant filter pass -- both
  now read the same filtered pool. Full suite re-run locally clean, 6030 passed/7 skipped/0
  failed. **Requires another Cerebral restart to take effect** (same as every code change today --
  Python doesn't hot-reload).

  **Known remaining limitation, not fixed, real trade-off discussed and accepted 2026-09-23**: the
  dynamic Candidate pool's random-sample component draws from ~13,500 tradable US assets, so any
  *specific* historically-validated name (RIOT, SOFI, MARA, NIO...) has roughly a 0.1% chance per
  call of being drawn at all -- no liquidity filter fixes that, it only prunes noise from whatever
  random handful got drawn. Two real fixes were discussed (tune the shared dynamic pool further,
  e.g. drop the random-sample component; or give trend-basket its own curated, volatility-screened
  universe, which would deviate from ADR-0026's "one shared pool" decision) -- user's call was not
  to chase this further right now ("not really worried about fine tuning... there isn't much
  downside" -- the strategy is paper-only with a 12% stop already capping single-position risk).
  Revisit if live dispatch activity stays suspiciously rare even once genuine rising-edge
  conditions occur.

  **Update, same day (2026-09-23), prompted by "the pool is 8 names, that should be much larger
  -- what's the maximum?"**: investigated the real ceiling rather than guessing. Confirmed
  Alpaca's actual API limits live (`get_market_movers` top<=50, `get_most_actives` top<=100 --
  both hard-enforced server-side). Raised `trend_basket_dispatch`'s pool to those values --
  ~84-90 candidates at the same $2/$10M filter, a real, much better mix including several of the
  actual backtested high-edge names (AMZN, GOOGL, META, NVDA, TSLA, F, GRAB, SNAP, SOFI, MARA,
  PLUG, BBD, RIG, VALE) plus major index ETFs, vs. the prior ~8-name draws.

  That size costs real time -- measured 679s (11.3 min) sequential, longer than the 5-minute
  scheduler tick. Diagnosed the actual bottleneck rather than assuming: `fetch_ohlcv` already
  caches locally via `bar_cache.py`, so the cost is the sheer count of distinct never-before-seen
  symbols needing one real fetch each on the first liquidity-filtering pass (measured live: 3.17s
  per distinct symbol, sequential). Parallelized `discovery.rank_for_day_trading`'s fetch loop
  (`ThreadPoolExecutor`, `max_workers=10`) -- measured 2.7x speedup (1.18s/symbol). End-to-end at
  the full ~90-candidate pool: 258s (4.3 min), comfortably under the 5-minute tick. This benefits
  every caller of `rank_for_day_trading` (Discovery, Expansion), not just trend-basket.

  Also landed the reentrancy guard from #1350 in the same change (`_trend_basket_dispatch_running`,
  mirroring `self_dev_campaign`'s `_campaign_running`) -- real margin exists now, but this is the
  safety net for days the pool build runs long anyway. 7 new/updated tests. Full suite re-run
  locally clean, 6031 passed/7 skipped, modulo the known pre-existing `test_sandboxed_eval.py`
  flake. Requires another Cerebral restart to take effect.

- PR #1345 -- TREND1 (self_dev generated, hand-fixed -- the generated `trend_basket_strategy.py`
  never defined `def strategy(data) -> list:` at all: it was a bare script computing a scalar
  `position` that stayed 0 forever (entry never set `position = 1`), and had no shape
  `sandboxed_eval.py`'s real runner could call (`ns['strategy'](bars)` would `KeyError`). The
  campaign correctly caught this as `tests_failed` and left the PR open rather than auto-merging.
  Rewrote to match `ipo_strategy.py`'s actual contract (per-bar signals list, same
  stop-check-before-peak-update ordering) and rewrote the test file to match (call
  `ns['strategy'](df)`, not read back a bare scalar). Full suite re-run locally clean, 6016
  passed/7 skipped, modulo the known pre-existing `test_sandboxed_eval.py` flake. Merged by hand.)
- PR #1346 -- TREND2 (self_dev generated, hand-fixed -- `rank_by_momentum`'s short-history guard
  checked `len(bars) < horizon` instead of `< horizon + 1`, so a symbol with exactly `horizon` bars
  (one short of what a `horizon`-day return needs) slipped through with a spurious 0.0 return
  instead of being excluded, caught by `test_excludes_short_history`. One-line fix. Full suite
  re-run locally clean, 6024 passed/7 skipped/0 failed (not even the usual pre-existing
  `test_sandboxed_eval.py` flake this run). Merged by hand.)
- PR #1347 -- TREND3 (self_dev generated, **hand-rewritten, not just hand-fixed** -- the most
  substantial hand-fix of the whole campaign, same pattern IPO6 warned this slice would likely
  need. The generated diff created a NEW file at the wrong path (`cerebral/plugins/
  trading_strategies.py` -- plugins live at the repo root, not inside `cerebral/`) instead of
  editing the real `plugins/trading_strategies.py`, so it reimplemented a fake gauntlet that
  always returned "ACCEPT" without calling the real one, never imported TREND1's real strategy
  code or TREND2's real `RisingEdgeGate`/`compute_breadth`/`rank_by_momentum`, and called
  `build_dynamic_universe()` with no arguments against a function that requires `broker` +
  `fetch_ohlcv_fn`. Worse: the `cerebral/main.py` edit called `_trading_strategies_plugin.
  _trend_basket_dispatch` at module TOP LEVEL (not inside a function) against a method that only
  existed on the unused duplicate class -- importing `cerebral.main` crashed immediately, which
  is why the campaign's own test run hit 26 collection errors across unrelated test files rather
  than a normal test failure. Deleted the bogus file; added a real `trend_basket_dispatch` tool
  to the actual `plugins/trading_strategies.py` (wires TREND1's code, TREND2's gate/ranking
  functions, reuses `_expand_strategy_ticker`'s real per-symbol Gauntlet-loop shape, filters
  through `rank_for_day_trading`'s liquidity screen first per TREND2's own docstring, skips an
  already-held symbol via `StrategyStore.get()` on its minted id) and a proper
  `_job_trend_basket_dispatch()` in `cerebral/main.py` mirroring `_job_ipo_dispatch`'s shape.
  Self_dev shipped zero tests for this slice; wrote 5 by hand. Full suite re-run locally clean,
  6029 passed/7 skipped/0 failed. Merged by hand.

  **Real gap found and flagged, not fixed in this slice:** position sizing relies on the global
  `max_per_trade_risk_pct` setting, which defaults to **2.0%**, not the 10% this whole design
  assumed -- the ADR's claim that "10% per trade fits the existing global default" was never
  actually verified against `cerebral/settings.py` and was wrong. Unlike the IPO play (which
  built real `risk_override_pct` plumbing through `RiskManager.check_order`, IPO1-3), this
  campaign explicitly scoped that out on the false assumption it wasn't needed. As shipped, a
  dispatched trend-basket position sizes at 2% of equity, not 10% -- 10 slots would total 20%
  invested, not the intended 100%. This is safe (under-deploys capital, doesn't over-risk it) but
  doesn't match the design. **A follow-up slice is needed**, mirroring IPO1-3's `risk_override_pct`
  shape, before this strategy trades at the sizing the backtests were actually validated against.
  Filed as #1349.)
