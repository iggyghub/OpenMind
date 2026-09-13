# REPLAY.md -- Historical Replay campaign driver

Felix has 286 validated strategies and no way to ask "what would these have done
last week?" The gauntlet answers *is this one candidate valid* against a 365-day
window at registration time; nothing replays the whole registered portfolio over
an arbitrary past span. This campaign adds that: a cached historical bar store, a
portfolio replay engine reusing the exact execution path the live dispatcher uses,
a results store, and two tools (`list_strategies`, `simulate_period`).

**Read before running this campaign:** every slice landed against `cerebral/trading/`
across this project's history (TRADING.md, 48+ slices; TRADING-AUDIT-FIXES.md, 21 more;
STRATEGY-REPAIR.md, 4 more) has needed hand-review, and the large majority shipped a
real bug self_dev's own tests didn't catch. Assume the same here. **Hand-verify every
PR's actual diff before merging, even on a green sandbox test run.**

**None of these slices touch `tray/`.** RP0 is the only slice that changes live
trading behaviour, and it is a one-line bug fix that should land first and alone.

## Measured facts (probed live this session -- do not re-derive)

| Fact | Value | Source |
|---|---|---|
| Live strategy specs | 286, **all `interval='1d'`**, 20 symbols | `strategy_specs.db` |
| Top symbols | TSLA 46, ORCL 41, AMD 28, CRM 27, META 18 | same |
| One sandboxed strategy eval | **~1.0s**, spawn-dominated (299 bars, 3 runs: 1.06/1.04/0.99) | timed `evaluate_signals` |
| `backtest_func` calls per `run_gauntlet` | **2** (base + noise gate; sensitivity adds 2/param, scheduler passes `params={}`) | `gauntlet.py:261,320,334` |
| Alpaca bars history floor | ~2016-01 (IEX feed inception), same for 1d and 15m | live probe |
| Alpaca rate limit | 200 req/min | `X-Ratelimit-Limit` |
| Daily bars, 5 sym x 10yr | 0.63s (SDK auto-paginates) | live probe |
| 15m bars, 5 sym x 10yr | 229.6s, 744,401 rows -- volume-bound, not rate-limited | live probe |
| News API pagination | **manual** (`limit` + `next_page_token`); 1yr of TSLA > 50 articles | live probe |
| News symbol tagging | roundups tag 5+ tickers at once ("4 ETFs To Watch" -> TSLA/AAPL/AMZN/MSFT) | live probe |

Four corrections to the brief, found in the code:

1. **The bar cache is at `cerebral/cache/`, not `plugins/cache/`** (`trading_data.py:23`,
   `os.path.dirname(__file__)` = `cerebral/`). It holds **1533 `1d` files, 14 `15m`,
   4 `5m`, 11MB**.
2. **That cache is write-only on the Alpaca path.** `fetch_ohlcv` tries Alpaca first
   and `return`s on success (`trading_data.py:76-86`) -- the TTL read block below it is
   only reachable when Alpaca throws. Every Alpaca-served call re-fetches over the
   network and writes another file.
3. **The cache key includes the exact date range**, and callers pass a sliding
   `today - 365 .. today` window, so it accumulates near-duplicate copies daily.
   Live example: `AAL_2026-07-23_2026-09-01_1d.csv` and `AAL_2026-07-24_2026-09-02_1d.csv`.
   That is what the 1533 files are.
4. **No parquet engine is installed** -- `pyarrow` and `fastparquet` both `ModuleNotFoundError`.
   Parquet would be a new ~50MB dependency. See D2.

## Decisions

### D1. Light replay loop, not `run_gauntlet`

Write a thin per-strategy loop; do **not** reuse `run_gauntlet` for portfolio replay.

- `run_gauntlet` defaults `auto_promote=True` and writes `strategy_store` + places
  `paper_broker` orders. Running it 286 times per replay would re-register the whole
  portfolio and fire 286 paper trades. That is disqualifying on its own.
- Its six gates answer *is this strategy valid* over a long window. Over a 5-day replay
  span, a Monte Carlo p-value and a walk-forward split are statistically meaningless.
  Replay asks a different question: *what did this strategy do, in these dates*.
- Cost: 2 `backtest_func` calls + a 1000-iteration permutation loop + a 10k-sample
  bootstrap per strategy, vs. 1 sandbox spawn. ~5 min for 286 strategies versus tens of
  minutes.

Escalation path when replay flags something: call the existing `run_gauntlet` tool on
that **one** strategy. It already exists and already does this.

### D2. Bar cache: one SQLite DB, not parquet, not per-range CSV

`cerebral/data/bars.db`, table `bars(symbol, interval, ts, open, high, low, close, volume)`
with `PRIMARY KEY (symbol, interval, ts)`.

- **Not parquet** -- it is a new dependency (fact 4 above) for something stdlib already
  does. Every other store in this repo is SQLite (`discovery_attempts.db`,
  `vetted_tickers.db`, `forward_fills.db`, `lifecycle.sqlite`, `strategy_specs.db`).
  Copying that convention is rung 2 of the reach ladder.
- **Not per-range CSV** -- that is the bug producing 1533 files for 20-odd symbols.
  Keyed by `(symbol, interval)` with a real row key, a 2016-2026 fetch and a
  2016-2025 fetch share storage and a range query is `WHERE ts BETWEEN ? AND ?`.
- **Invalidation: none needed for past bars.** Historical bars before today never change.
  Gap-fill on read: `SELECT MAX(ts) WHERE symbol=? AND interval=?`, fetch only
  `(max_ts, end]`, `INSERT OR REPLACE`. Nothing is ever re-fetched.
- **Known ceiling:** a split occurring *after* a cached fetch retroactively rewrites
  every earlier adjusted price. Handled by a `fetched_at` column plus a manual
  `refresh=True` full-refetch escape hatch, not by polling a corporate-actions endpoint.
  Corporate actions are rare; name the ceiling in a `ponytail:` comment.
- Cached bars are always **`Adjustment.ALL`** (see RP0). The cache stores one adjustment
  basis and documents it rather than keying on it.

Capacity sanity: 15m x 10yr is ~149k rows/symbol. 300 symbols is ~45M rows -- large but
well inside SQLite's range with that primary key. Daily is ~2.5k rows/symbol, trivial.

### D3. Fetch job: sequential and resumable, no thread pool

The intraday leg is **serialization-bound, not rate-limited** (229.6s for 5 symbols with
no evidence of throttling, against a 200/min ceiling the SDK never approaches). Concurrency
buys little and costs a scheduler-contention argument under ADR-0028 R5.

- Loop `for symbol in universe: ensure_cached(symbol, interval)`.
- **Resumability falls out of the cache** -- a symbol whose rows are already present is a
  no-op. Kill the job mid-run and restart it; it continues. No checkpoint file, no state.
- Retry: three attempts with `time.sleep(2 ** attempt)` around the single
  `get_stock_bars` call. Not a retry framework.
- Runs as a background task via the `start_discovery` / `stop_discovery` pattern
  `scheduler.py:428,466` already has. It is network + pandas, not GPU or LLM, so it does
  not contend for the singular scheduler -- but it must not block the event loop
  (ADR-0026 recorded a live book-ingestion stall from exactly that).

### D4. News: per-day article count with a prominence filter. No LLM scoring.

**Recommended: count articles per (symbol, day), dropping any article tagged with more
than 3 symbols.** Roughly ten lines, zero LLM calls, and it yields an
unusual-news-day flag that a replay can correlate against realised volatility.

Rejected: LLM-scoring every article's sentiment, on both cost and quality.

- *Cost:* one article per `complete_fn` call, at `sentiment.py`'s existing cost shape. A
  single year of TSLA news already exceeded one 50-article page, so a few hundred tickers
  x 10 years is order 1e5-1e6 articles -- days of wall clock against the one endpoint that
  ADR-0028 R5 names as the scarce singular resource, for a batch job.
- *Quality:* the measured roundup-tagging noise means most per-symbol-tagged articles are
  not about that symbol. Spending the expensive mechanism to carefully score noise is the
  worst available trade. The prominence filter removes the noise more cheaply than
  scoring can.
- *Deferred, not rejected:* LLM-scoring the **top-K event days a replay actually flags**
  is a small, bounded call volume and is the natural next rung. ADR-0028 R2 -- build it on
  the third repeat of actually needing it, not now.

News is the **last** slice. Bar replay is useful with zero news; nothing upstream blocks
on it.

### D5. Results land in a new `ReplayStore`, and do NOT auto-retire anything

New `cerebral/trading/replay_store.py` -> `cerebral/data/replay_runs.db`, mirroring
`DiscoveryAttempts` (same shape, same one-file-per-concern convention). **No new columns
on `strategy_specs`** -- `StrategyStore`'s docstring is explicit that it is deliberately a
tiny table, and replay results are a different concern with a different lifetime.

```
replay_runs(run_id PK, start, end, interval, n_strategies, created_at)
replay_results(run_id, strategy_id, symbol, gross_return, net_return,
               n_trades, max_drawdown, sharpe, flat_reason)
```

`flat_reason` is the important column. `evaluate_signals_verbose` already returns the real
sandbox failure reason (SR1, #1176) and `evaluate_signals` silently degrades to all-flat.
A replay that reports "0.0% return" for 200 strategies whose code actually raises
`AttributeError` would be a lie. **Persisting that reason is the single most valuable
thing this campaign produces on day one: a census of how many of the 286 live strategies
are broken code rather than bad hypotheses.**

**Explicitly not in this campaign:** wiring replay output into `lifecycle.check_retirement`
or `discovery.py`'s promotion path. Those own promote/retire against real forward fills
with thresholds tuned to that signal. Replay is *evidence*, and the honest first
deliverable is a read-only report plus a tool. Auto-acting on it needs a threshold the
user picks after seeing the first real report -- that is a separate decision, on separate
evidence. This is the part of the project most likely to be over-built; do not build it here.

### D6. Ticker universe: grow organically. Do not source an S&P 500 list.

Replay can only replay symbols that **have strategies**, and there are 20. A
few-hundred-ticker universe is what strategy *discovery* needs, and `discovery.py` already
has `build_dynamic_universe` (Alpaca movers + most-actives + a random sample of all
tradable assets, ranked by `rank_for_day_trading`'s liquidity/ATR filter).

So the cache warms for `StrategyStore.list_all()` symbols (20) union
`DiscoveryWatchlist.symbols()` (35) -- about 40 unique, **under a minute at daily
granularity**. It grows by itself as discovery finds tickers. If a broad static universe
is wanted later, `broker.get_all_assets()` already exists and it is a one-line slice then.

### D7. Build the interval plumbing, but do not run the intraday warm

`interval` is already a `StrategySpec` field and the whole path is interval-parametric, so
intraday costs nothing extra to *support*. But **all 286 live strategies are `1d`** -- a
3.8-hour 15m cache warm would have literally nothing to replay against today.

Ship the batch tool; treat "spend four hours warming 15m data" as a runtime decision, made
when the first intraday strategy is registered. Largest single scope saving here, straight
from ADR-0028 R2.

### D8. New tools go in a new `plugins/trading_replay.py`

`scheduler.py` is 2157 lines and already hosts `run_gauntlet`, `edit_strategy`,
`run_discovery`, `upload_book`, `halt_strategy`, `check_ipo_calendar` and more behind a
docstring that still says *"Tools: create_event, list_events, update_event, delete_event,
run_gauntlet. SQLite-backed. No external calendar deps."* Adding two more trading tools
there makes a known problem worse; extracting all of it is a large refactor across every
live trading tool and is not this campaign's job.

New plugin with only the new tools, plus its `cerebral/tests/test_plugin_trading_replay.py`
(**ADR-0034: without that exact filename the plugin boots silently disabled with
`REASON_NO_TEST_FILE` and no traceback**).

File one separate issue for the `scheduler.py` junk-drawer extraction. Do not do it here.

**No planner change is needed.** `_build_tool_catalog` (`planner.py:39`) already injects
every registered tool's name + one-liner into the system prompt, so *registering*
`list_strategies` is the introspection wiring. One fewer slice than expected.

## Status: ready

## Next slice -- start here

- **Active:** RP9 -- #1196
- **Model:** sonnet

## Queue

- [x] RP0 -- #1187 -- `broker.py`: pass `adjustment=Adjustment.ALL` -- **live bug, land first and alone** (Model: opus)
- [x] RP1 -- #1188 -- `cerebral/trading/replay.py`: extract scheduler's `backtest` closure (pure refactor) (Model: sonnet)
- [x] RP2 -- #1189 -- derive a `Trade` list from position diffs; wire `compute_backtest_result` for net-of-cost returns (Model: opus)
- [x] RP3 -- #1190 -- `cerebral/trading/bar_cache.py`: SQLite bar store with append-only gap fill; route `fetch_ohlcv` through it (Model: opus)
- [x] RP4 -- #1191 -- `cerebral/trading/replay_store.py`: `ReplayStore` + `replay_runs.db`, incl. `flat_reason` (Model: sonnet)
- [x] RP5 -- #1192 -- `replay.py`: `run_replay(specs, start, end)` engine over the cached store (Model: opus)
- [x] RP6 -- #1193 -- `plugins/trading_replay.py`: `list_strategies` + `simulate_period` + `replay_report` (+ ADR-0034 test file) (Model: sonnet)
- [x] RP7 -- #1194 -- batch cache warm as a start/stop background task, with retry/backoff (Model: sonnet)
- [x] RP8 -- #1195 -- news: dated fetch + paging loop + prominence filter + per-day count, cached in `bars.db` (Model: sonnet)
- [ ] RP9 -- #1196 -- `docs/replay-live-verify.md` + first real 286-strategy replay; report the broken-code census (Model: sonnet)

## Landed PRs

- RP0 -- #1198 -- `broker.py`: pass `adjustment=Adjustment.ALL`
- RP1 -- #1199 -- `cerebral/trading/replay.py`: extract scheduler's `backtest` closure into `run_bars`
- RP2 -- #1200 -- `derive_trades` + `compute_backtest_result` wiring -- Felix's own `self_dev_campaign`
  attempt (via `scripts/trigger_campaign.py` over tray IPC, not the external `run-replay.ps1` loop)
  wrote the real implementation and correctly caught 3 failing tests itself, blocking rather than
  merging broken code. Hand-fixed and merged directly (same as STRATEGY-REPAIR's SR1): `derive_trades`
  used `position.diff()` raw, whose first element is always NaN, and `NaN != 0` spuriously "traded"
  bar 0 every time; `compute_backtest_result` was fed the cumulative equity curve instead of per-bar
  fractional `daily_returns` (its `cumulative_net_return` property compounds via `(1+r)`, which only
  means something for returns, not price levels); its `BacktestResult` return is a dataclass, not a
  dict, so the prior `isinstance(dict)` check always fell through. Two of the three failing tests were
  themselves buggy (an index-length mismatch, an off-by-one signal count) rather than the
  implementation -- fixed both and added one new test locking in the constant-nonzero-position edge
  case. All 8 `test_replay.py` tests + the 35-test gauntlet suite pass.
- RP3 -- #1201 -- `bar_cache.py` SQLite store -- same pattern as RP2: Felix's `self_dev_campaign`
  attempt wrote the store and its own tests, correctly caught 3 setup-time errors, and blocked
  instead of merging. Hand-fixed and merged: `AlpacaMarketDataClient` was imported lazily, so
  `cerebral.trading.bar_cache` had no persistent attribute for the test's own
  `patch("cerebral.trading.bar_cache.AlpacaMarketDataClient")` to target -- moved to module level,
  which surfaced a real, worse bug underneath: `df["Date"]` was read as a column access, but "Date"
  was only ever an *index name* on a meaningless `RangeIndex` (`read_sql_query` with no `index_col`),
  and the real `ts` column had already been dropped by the preceding column-select -- every call
  where the cache already had data would `KeyError`. Also found and fixed: `refresh=True` still
  silently narrowed to the cached gap instead of re-fetching the full range, defeating its own
  purpose; two of the PR's own tests pre-populated SQLite rows against a table that was never
  created (`sqlite3.connect` to a new file creates an empty file, not a schema); and, biggest gap,
  **the PR never wired `fetch_ohlcv` to actually use `bar_cache` at all** -- explicitly part of
  RP3's spec ("so the whole system benefits, not just replay"), untested by anything in the given
  test file, and would have landed a slice that changed nothing for the live system. Wired it,
  added 2 new integration tests, and extended `test_trading_data.py`'s existing fixtures so its
  pre-existing tests don't silently start touching the real production `bars.db`. 16/16 new tests
  pass; 291/292 in the broader regression sweep (the one failure is a pre-existing stale assertion
  in `test_plugins_time_notes.py`, confirmed on a clean master checkout, flagged separately).
- RP4 -- #1202 -- `replay_store.py` `ReplayStore` + `replay_runs.db` -- **the first slice where
  Felix's own code needed no fix.** Its `self_dev_campaign` attempt still blocked on
  `tests_failed`, but the cause was full-suite flakiness (`self_dev_io.py`'s test gate runs the
  ENTIRE `cerebral/tests/ tests/` suite, ~5769 tests, ~8 minutes): two failures
  (`test_plugin_n8n.py`, `test_session_worker_s12.py`) unrelated to `replay_store.py` in any way,
  both passing cleanly in isolation and on a clean master checkout, plus the already-known stale
  `test_plugins_time_notes.py` assertion (flagged under RP3). `replay_store.py`'s own 2 tests
  passed on the first run, standalone and inside the full sweep both times. Merged as-is, no code
  changes needed.
- RP5 -- #1203 -- `run_replay` engine -- **the largest gap so far.** Felix's `self_dev_campaign`
  attempt wrote ONE test calling a `run_replay` that didn't exist anywhere in `replay.py`, and
  reinvented a local `StrategySpec` (fields `symbol, interval, code`) instead of importing the
  real one from `strategy_store.py` -- incompatible with what RP6's `StrategyStore.list_all()`
  will actually hand it. The test also called `replay_store.get_runs()` (the real method is
  `list_runs()`) and accessed results via `.attribute` (`result.run_id`, `result.spec_code`) --
  `ReplayStore` rows are `sqlite3.Row`, `["column"]` access only, and there is no `spec_code`
  column. Every one of these would have failed regardless of whether `run_replay` existed.
  Implemented the real engine by hand: `run_bars_verbose` (mirrors SR1's
  `evaluate_signals`/`_verbose` split so `run_bars`'s existing 3-tuple contract for
  `plugins/scheduler.py` stays untouched) plus `run_replay(specs, start, end, bar_cache=None,
  replay_store=None)` -- a thin per-strategy loop (D1: not `run_gauntlet`), per-interval warm-up
  margin so genuine indicator warm-up doesn't read as a code failure, scoring restricted to the
  requested window, and a try/except per strategy so one broken strategy can't abort the run.
  Rewrote the test against the real `StrategySpec`/`ReplayStore` APIs and added a second test for
  the one-broken-strategy-doesn't-abort-the-run case. 10/10 `test_replay.py` pass; 353/354 across
  the full scheduler/gauntlet/discovery/live_tick/replay regression set (the one failure is the
  already-flagged pre-existing `test_plugins_time_notes.py` assertion).
- RP6 -- #1204 -- `plugins/trading_replay.py` -- **didn't even collect.** The PR imported
  `cerebral.core.plugin` (does not exist anywhere in this repo; the real path every plugin
  uses is `cerebral.mcp.orchestrator`) and `cerebral.trading.replay.store`/`.replay.replay`
  (both are flat modules, not a `replay/` package). Past the import, nearly every API in the
  plugin and both its test files was invented rather than checked: `ToolResult(output=...)`
  instead of the real `content=`; a bare `handler=` field instead of the real
  `list_tools()`/`call_tool(name, args)` dispatch every plugin here actually uses; `s.id`
  instead of the real `StrategySpec.strategy_id`; `run_replay(start, end, specs)` instead of
  the real `(specs, start, end, ...)`; `ReplayStore` rows treated as dicts with `.get()`/
  `.attribute` access when they're `sqlite3.Row` (`["column"]` only) with no `interval`
  column at all; `get_runs()` instead of the real `list_runs()`. Rewrote the plugin as a
  proper class (mirroring `settings_control.py`) and both test files against the real APIs,
  including a genuine `MCPOrchestrator(verify_test_files=True)` discovery smoke test against
  the real `plugins/` directory -- the original "smoke test" only re-imported the class and
  checked a nonexistent `.TOOLS` attribute, never touching the orchestrator at all. Also
  fixed one bug of my own found while testing: closing `StrategyStore`/`ReplayStore` in a
  `finally` block is wrong here, since `scheduler.py`'s own convention never closes these
  (cheap per-call SQLite connections) -- matched that instead. 8/8 new tests pass; 618/619
  across the full regression sweep (the one failure is the already-flagged pre-existing
  assertion).
- RP7 -- #1205 -- `start_cache_warm`/`stop_cache_warm` -- a smaller gap this time, three
  real bugs: `from cerebral.trading.bar_cache import bar_cache` (the module exports
  `get_bars`, not a `bar_cache` attribute -- fix is importing the module itself, same
  pattern `run_replay` already uses); `DiscoveryWatchlist.symbols()` called on the bare
  class when it's a real instance method (`TypeError` on first use), and its
  `List[str]` return unioned with a `set` via `|` (also `TypeError`); and, the more
  serious one, `bar_cache.get_bars` -- a plain synchronous function doing real SQLite +
  network I/O -- called directly inside an `async def`, blocking Cerebral's entire event
  loop on every fetch, exactly the ADR-0026-documented failure class. Wrapped it in
  `loop.run_in_executor`. The PR also shipped zero test coverage for either tool despite
  its own issue spec asking for retry/stop/universe-dedup tests -- added 8 covering
  retry-then-succeed, give-up-after-3, stop-halts-progress, stop-with-nothing-running,
  concurrent-run refusal, and the plugin's own dispatch. 16/16 pass; 626/627 across the
  full regression sweep (the one failure is the already-flagged pre-existing assertion).
- RP8 -- #1206 -- news fetch/cache -- **the deepest gap of the campaign: news fetching was
  100% non-functional**, not just untested -- `AlpacaMarketDataClient` has no `get_news`
  method at all. Checked the real installed `alpaca-py` API
  (`alpaca.data.historical.news.NewsClient` + `NewsRequest`) and implemented it for real:
  it's an attribute-based Pydantic model (`article.id`, `.symbols`, `.created_at`;
  `news_set.data["news"]`, `.next_page_token`), not the dict-shaped fake the PR's own tests
  assumed. Also: `fetch_news`/`count_news_events` never called the module's own
  `init_news_db()` (`no such table: news` on any fresh DB); `datetime.timedelta(days=1)`
  isn't a thing (`timedelta` is a sibling class, not a `datetime` attribute); `replay_store.py`
  (RP4) was never touched despite `replay.py` already passing it a `news_event_count` kwarg
  it didn't accept (immediate `TypeError` on every replay); and a real design bug -- the news
  fetch lived inside the per-strategy try/except, so a transient news-API failure would get
  attributed as the *strategy* failing, directly contradicting RP8's own "must never alter
  which strategies get replayed" requirement. Fixed all of it, added an autouse fixture so
  pre-existing tests can't accidentally hit a real Alpaca News call if real credentials exist
  in this box's keyring, and surfaced `news_event_count` on `replay_report`'s rows (RP6) since
  the column existing was pointless if the report never showed it. 84/84 across every
  replay-adjacent suite; 645/646 across the full sweep (the one failure is the already-flagged
  pre-existing assertion).

## Slice detail

### RP0 -- `adjustment=Adjustment.ALL`

`cerebral/trading/broker.py:528-533` builds `StockBarsRequest` with no `adjustment`
parameter, so Alpaca returns **raw, unadjusted** prices. Reproduced live: AAPL's
2020-08-31 4:1 split reads as a ~74% one-day crash (499 -> 129).

This corrupts the **current live/paper system**, not just replay. TSLA (46 strategies;
5:1 in 2020, 3:1 in 2022) and NVDA (9 strategies; 4:1 in 2021, 10:1 in 2024) are both
actively traded here and both have splits inside the 365-day registration window's reach.

**Pick `ALL`, not `SPLIT`.** Decisive reason: the yfinance fallback in the very same
function already returns split- *and* dividend-adjusted bars (`trading_data.py` docstring:
*"Data is adjusted for splits and dividends"*). Today the two vendor paths silently
disagree about what a price means. `ALL` makes them agree. Secondary reason: strategy code
computes returns via `Close.pct_change()`, and a dividend ex-date drop is a price drop the
holder was compensated for in cash -- total-return adjustment is closer to realised P&L
for a long/flat signal.

Land alone. Verify by re-fetching AAPL across 2020-08-31 and asserting no single-day move
beyond a sane bound. Note in the PR that any cached pre-fix CSVs under `cerebral/cache/`
are now wrong-basis -- RP3 replaces that cache wholesale, and until then stale files are
only read on the (unreachable) Alpaca-failure path.

### RP1 -- extract the backtest closure

`plugins/scheduler.py:979-996` defines `backtest(bars, params)` as a nested closure:
`evaluate_signals` -> right-align short signal lists -> `shift(1)` -> `pct_change` ->
`cumprod` -> `compute_max_holding_days`. Replay needs byte-identical semantics.

Copying it would create a second implementation that drifts -- `live_tick.py`'s own header
documents three incompatible `strategy(data)` shapes that already drifted apart in exactly
this way. So: move it to a new `cerebral/trading/replay.py` as
`run_bars(code, bars, interval) -> (equity, position, metrics)` and have scheduler's
closure call it. Direction is `plugins/` -> `cerebral/`, which respects the seam rule
(#153/#385).

**Pure refactor, zero behaviour change.** New file created here because RP5's engine lands
in it too -- one new file across the campaign, not two. Test: the existing gauntlet tests
must pass untouched, plus a direct unit test of `run_bars` on a synthetic ramp.

### RP2 -- trades and net returns

`compute_backtest_result` (`cost_model.py:67`) wants `gross_returns: List[float]` and
`trades: List[Trade]`. The scheduler closure produces **neither** -- it returns an equity
curve and never calls the cost model at all. Every number the gauntlet reports today is
gross of spread and slippage.

Derive trades from the position series: each bar where `position.diff() != 0` is a
`Trade(index, direction, price=Close, value=abs(delta) * Close)`. About six lines. Feed
`compute_backtest_result` so replay reports both gross and net.

Independently landable: it is additive, `run_bars` keeps returning what it already returns.

### RP3 -- the SQLite bar cache

New `cerebral/trading/bar_cache.py` per D2. `get_bars(symbol, start, end, interval)`:
query the range; if `end` exceeds the stored max, fetch only the gap through
`AlpacaMarketDataClient`, `INSERT OR REPLACE`, re-query. Route `fetch_ohlcv` through it so
the whole system benefits, not just replay.

Fix the two live cache bugs while here: the unreachable read path (Alpaca returns before
the TTL check) and the sliding-window key. Leave the old `cerebral/cache/` CSVs in place
and unread -- deleting 1533 files is a separate cleanup, and the bar store makes them
inert.

**Do not widen `live_tick.py`'s `_lookback_days`.** 180 days is correct for a live tick;
widening it makes every live dispatch slower for no gain, and STRATEGY-REPAIR.md's SAFETY
section fenced that file off for the same reason. Replay takes explicit `start`/`end`
instead; `scheduler.py`'s 365/30 defaults stay as defaults.

### RP4 -- `ReplayStore`

Per D5. Mirror `DiscoveryAttempts` (`discovery.py:163`) closely enough that it reads as the
same pattern. Nothing consumes it yet -- lands green on its own unit test.

### RP5 -- the replay engine

`run_replay(specs, start, end) -> run_id` in `replay.py`. For each spec: pull cached bars
for `(symbol, interval)` over the window, call `run_bars`, call `compute_backtest_result`,
write a `replay_results` row. Use `evaluate_signals_verbose` so `flat_reason` is real.

**Measured cost: ~1.0s per strategy, so ~5 minutes for all 286, sequential.** Do not build
a thread pool -- ADR-0028 R5 says nothing assumes concurrency, and five minutes in a
background task is fine. Leave a `ponytail:` comment naming the ceiling and the upgrade
(the spawns are independent subprocesses, so a pool is the obvious escalation if the
portfolio grows an order of magnitude).

Guard the short-window case: a 5-day daily replay is 5 bars, and a strategy with a 20-bar
indicator warm-up produces an all-flat signal that is *correct behaviour*, not a failure.
Distinguish it from a code failure in the report, or the first run reads as a catastrophe.
Fetch a warm-up margin of bars before `start` and evaluate on the full series, scoring only
the requested window.

### RP6 -- the tools

`plugins/trading_replay.py` per D8, with `cerebral/tests/test_plugin_trading_replay.py`.

- `list_strategies(symbol=None, interval=None)` -- thin wrapper over
  `StrategyStore.list_all()`, which already exists. Returns id/symbol/interval/qty.
- `simulate_period(start, end, strategies=None, interval=None)` -- calls `run_replay`,
  returns the `run_id` plus a summary.
- `replay_report(run_id)` -- per-strategy rows plus the `flat_reason` census.

Smoke-test plugin discovery with `MCPOrchestrator(verify_test_files=True)`; a bare
`MCPOrchestrator()` defaults that to `False` and will not catch a missing test file.

### RP7 -- batch cache warm

`start_cache_warm` / `stop_cache_warm` following `start_discovery` / `stop_discovery`
(`scheduler.py:428,466`) -- background task, sequential, resumable, retry with backoff, per D3.
Default universe per D6 (strategy symbols + watchlist). Daily granularity only by default;
intraday available behind an explicit `interval` argument but not run automatically, per D7.

### RP8 -- news

Dated historical fetch against Alpaca's News API with a manual `next_page_token` paging
loop (bars auto-paginate, news does not). Cache into a `news` table in the same `bars.db`,
deduped on article id. Store `n_symbols` per article; the per-day count for a symbol counts
only articles with `n_symbols <= 3`. Surface as one column on `replay_results`. Per D4, no
LLM anywhere in this slice.

### RP9 -- live verification

`docs/replay-live-verify.md` following the existing `docs/*-live-verify.md` convention, plus
a real run over the full 286 against a recent multi-week window. **The deliverable of this
slice is the broken-code census**, not a performance number: how many live strategies
actually execute, how many degrade to flat, and with which sandbox errors. ADR-0028 R6 --
verified running, or it did not ship.

## SAFETY

- **RP0 is the only slice that changes live trading behaviour.** Land it alone, before
  everything else, and hand-verify against a known split date.
- **Nothing in RP1-RP9 may touch `live_tick.py`, `risk_limits.py`, or any broker order
  path.** Replay reads history and writes a results table. It places no orders, real or
  paper. `run_replay` must never construct a broker client.
- **`run_gauntlet` must not be called from the replay path.** Its `auto_promote=True`
  default writes `strategy_store` and fires paper trades; a portfolio-wide replay calling
  it would re-register all 286 strategies. Escalation to the gauntlet is a *user* action on
  a single strategy, through the tool that already exists.
- **No slice may auto-promote, auto-retire, or auto-tune anything** from replay evidence
  (D5). `lifecycle.py` and `discovery.py` keep sole ownership of those transitions.
- **`evaluate_signals`' contract is unchanged** -- `List[int]`, degrade-to-flat on any
  failure. Replay uses the additive `evaluate_signals_verbose` for its reason string, same
  as SR1 established.
- **The 15m batch warm is not run by any slice.** Shipping the tool is in scope; spending
  four hours of fetching is a runtime decision, made when an intraday strategy exists.
- A `ponytail:` comment must name the ceiling on each deliberate shortcut: sequential
  replay spawns, the post-fetch-split staleness window, the `n_symbols <= 3` news
  threshold.
