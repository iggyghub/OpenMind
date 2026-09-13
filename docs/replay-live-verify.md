# RP9 -- Live Verification Report

**Date:** 2026-09-13
**Campaign:** Historical Replay (RP0-RP9, REPLAY.md)
**Environment:** Real Cerebral checkout (`C:\OpenMind`), live `strategy_specs.db` (286 strategies), real `cerebral/data/bars.db` (cache warmed for this run, not simulated)
**run_id:** `473e487cff31423ebdbf05d0b148d7a5`

**Note on this document's history:** `self_dev_campaign`'s own RP9 attempt produced a first
draft of this file with numbers that do not correspond to any real run -- it was dated
2026-09-03 (before this campaign existed), cited `replay_report(run_id=...)` with no actual
run_id, and `cerebral/data/replay_runs.db` (the file any real `simulate_period` call creates)
did not exist anywhere on this machine. That draft was discarded. Everything below is from an
actual run, executed by hand after the automated attempt was found to be fabricated, per
ADR-0028 rule 6 ("verified running, or it did not ship") -- rule 6 is exactly what this
incident tests, and it failed the first time.

## 1. Cache Warmup

Ran `start_cache_warm(interval="1d")` for the default universe (20 live-strategy symbols
union 35 discovery-watchlist symbols, deduplicated to 38 unique symbols -- some watchlist
entries already had strategies).

- Universe: `AAPL, ABNB, ACVA, ADBE, AMD, BDRX, CBRG, CRM, DAIC, DBGI, F, FEIM, FTFT, FTK, IBM,
  INTC, LYFT, MARA, META, NEOV, NFLX, NVD, NVDA, ORCL, PLUG, RIG, RIOT, SKDD, SMR, SMU, SNAP,
  SOFI, SPCM, SPY, SUNE, TNON, TSLA, UBER` (38 symbols)
- Wall-clock: **30.5s**
- All symbols cached successfully, no errors.

## 2. Portfolio Replay

Ran `simulate_period(start="2026-08-15", end="2026-09-12")` against all 286 live strategies
(no symbol/interval filter) -- roughly 4 weeks, comfortably over the 10-trading-day floor.

- Wall-clock: **495.9s (8m 16s)**
- Strategies replayed: **286/286**, no crashes, no external interrupts.
- This is noticeably slower than REPLAY.md's own pre-RP8 ~5-minute estimate for 286
  strategies (`~1.0s/strategy` sandboxed-eval measurement). The gap is very likely RP8's
  per-strategy news fetch, which was not part of that original cost model -- each strategy now
  also makes a real Alpaca News API call (or a cheap cache hit on a repeat run) inside the same
  loop iteration. Not a regression to fix here; a real cost this campaign's own numbers didn't
  originally account for, worth remembering if replay is ever run against a much larger
  portfolio.

## 3. Replay Report & Census

### Strategy Execution Census

- **Total replayed:** 286
- **Successfully executed (non-flat-on-failure):** 286 (100%)
- **Silently degraded to flat (`flat_reason` set):** 0 (0%)
- **Flat-reason breakdown:** none -- empty census.

This is a genuinely different result than the campaign's own working assumption going in.
REPLAY.md's brief cited STRATEGY-REPAIR.md's finding of "100+ occurrences" of hallucinated
pandas methods across book-ingested strategies as the reason a broken-code census would be
valuable. That finding predates STRATEGY-REPAIR's own SR1-SR4 slices, which added a bounded
one-shot repair retry at generation time (`trading_ideas.py`'s `to_strategy`) specifically to
catch this class of failure *before* a strategy is ever registered. This run is evidence that
fix is working: zero of the 286 currently-registered strategies fail at replay time. That
does not mean no strategy anywhere has ever hallucinated a method -- it means none of the
ones that made it into `strategy_specs.db` did, which is exactly what the repair retry is
supposed to guarantee.

### Net-Return Distribution (all 286 strategies -- none excluded, since none failed)

- **Min:** -21.17%
- **Median:** +0.43%
- **Max:** +36.60%
- **Mean:** +3.47%

Over a single ~4-week window with no cost-of-capital or drawdown-adjusted framing --
read as a rough distribution shape, not a performance verdict. `n_trades` per strategy over
the window ranged 0-13 (mean 3.09); several strategies traded zero times in this specific
window, which is a legitimate outcome (a strategy waiting for its own entry condition), not
a code failure -- correctly distinguished here because `flat_reason` is null for every one of
them.

### News Coverage

`news_event_count` (RP8) summed to 2268 across all rows; 272/286 strategies (95%) had at
least one day with a prominent news event (`n_symbols <= 3`) somewhere in the 4-week window --
expected, given the window includes each symbol's normal cadence of earnings/analyst/press
coverage, not an anomaly.

## 4. Conclusion

RP9 live verification is complete, against a real run this time. The full RP0-RP8 pipeline
(SQLite bar cache, `run_replay` engine, `ReplayStore`, the `trading_replay` plugin's three
tools, cache warm, and news integration) executed correctly end-to-end against the real
`strategy_specs.db` and real historical market data. The headline finding is the opposite of
what the campaign's own brief anticipated: **0% of the 286 live strategies are currently
broken at the code level**, which reads as STRATEGY-REPAIR's generation-time repair retry
having already done its job rather than this campaign finding nothing to fix. No order was
placed, real or paper; no strategy was modified, promoted, or retired by this run or by this
campaign, per REPLAY.md's own SAFETY section.
