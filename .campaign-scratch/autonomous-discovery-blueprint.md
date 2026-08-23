# Autonomous discovery + day trading: risk gate, intraday, sourcing (2026-08-24 blueprint)

Full design produced by an Opus-model Plan agent, spiked and confirmed
against the real tree before any slice was filed. Implements decisions
#32-#47. Nine slices, in dependency order. Slices S20-S21 are P0 gap
closure, not new features -- nothing after them may be built or armed
until both have landed and been hand-verified.

## Real, load-bearing finding from the spike (2026-08-24), not assumed

**`alpaca-py` is not a declared or installed dependency of this repo.**
Confirmed three ways, not inferred: it appears nowhere in
`pyproject.toml`'s `dependencies` list, nowhere in
`cerebral/requirements.txt`, and `pip show alpaca-py` reports
`Package(s) not found`. Every Alpaca test in this campaign has run
against `StubBrokerClient`.

The consequence traces cleanly through real code:
`AlpacaBrokerClient._connect()` (`cerebral/trading/broker.py:72-83`)
does `import alpaca.trading.client` inside a `try/except ImportError`
and raises `RuntimeError("alpaca-py is not installed...")`.
`dispatch_due_events` constructs `AlpacaBrokerClient(env="live")`
(`live_tick.py:267`) -- construction is lazy and succeeds -- then
`scheduler._run_paper_strategy` calls `run_strategy_tick`, whose first
broker touch is `broker.list_positions()`, which calls `_connect()` and
raises. `_run_paper_strategy`'s broad `except Exception`
(`plugins/scheduler.py:637`) swallows it into
`{"status": "error", "reason": ...}`.

So: **flipping `trading_live_arm` to True today produces a silent
per-tick error loop, not a live order.** This fails *safe*, like S12's
lifecycle-persistence gap did -- but it means decision #43 ("user will
arm live trading") is blocked on a missing package, and decision #39
("Alpaca Market Data primary") needs the same package before it can be
started at all. The parallel to S13's `STATUS_DLL_NOT_FOUND` discovery
is exact: the mechanism was believed present and was never once
exercised against the real thing.

Five smaller confirmed-by-reading findings, each with a recommendation,
are in "Open sub-decisions" below.

## The slices

- **S20 -- Risk gate wiring (P0, decision #47).** `RiskManager`
  (`cerebral/trading/risk_limits.py`) has zero production callers --
  confirmed by scoped grep over `cerebral/` and `plugins/` (excluding
  `cerebral/data/` sandbox clones and `.claude/worktrees/`, which
  produce ~330 false hits): the only references anywhere are the class
  itself and `cerebral/tests/test_trading_risk.py`. Wire it at the one
  choke point every order routes through -- `run_strategy_tick`
  (`live_tick.py:149`), immediately before `broker.place_order` on
  line 193 -- not at each caller. Needs: an optional `risk: RiskManager`
  param on `run_strategy_tick`, threaded from `dispatch_due_events` ->
  `scheduler._run_paper_strategy` (`scheduler.py:597`, add a `risk=`
  kwarg beside the existing `store=`/`fetch=` seams) -> `main.py`'s
  `_scheduler_loop` call site (`main.py:3414`). Inputs
  `check_order` needs are all already available at that point:
  `broker.get_account().equity`, `len(broker.list_positions())`,
  `qty * <last close>` for `trade_value`. A blocked order returns
  `{"status": "blocked", "blocked_by": ...}` and places nothing.
  Also in this slice, because they are the same "the documented rails
  are decorative" bug: (a) `apply_position_ramp`'s returned `size_pct`
  is logged (`live_tick.py:295`) and never applied -- `decide_action`
  opens at raw `spec.qty`, so a "25% ramp" places full size; multiply
  it in. (b) `main.py:255`'s `_trading_lifecycle = StrategyLifecycle()`
  passes no `AlertDispatcher`, so `get_alert_history()` returns `[]`
  forever and every alert in TRADING.md's Alerting section -- including
  "Risk limit prevented a trade" -- is silent in production; construct
  one dispatcher and pass it to both `StrategyLifecycle` and
  `RiskManager`. (c) `cerebral/settings.py` has no
  `max_per_trade_risk_pct` / `max_daily_loss_pct` /
  `max_concurrent_positions` keys, so `SettingsStore.set()` raises
  `ValueError: Unknown setting key` -- decisions #9/#21 say these are
  user-configurable and they are not; add the three keys to `_DEFAULTS`
  and `_TYPES`. **Acceptance test is the one the grill named: an
  over-limit order gets blocked and `broker.place_order` is never
  called** -- assert on the stub's `_orders` dict being empty, not on
  the return value alone.
  Files: `cerebral/trading/live_tick.py`, `plugins/scheduler.py`,
  `cerebral/main.py`, `cerebral/settings.py`,
  `cerebral/tests/test_trading_risk.py` (+ a new dispatch-level test).

- **S21 -- Live-path preflight (P0b, prerequisite to decision #43).**
  Add `alpaca-py` to `pyproject.toml` (and `cerebral/requirements.txt`,
  kept in sync per that file's own comment) -- see the spike finding.
  Then make the live path fail *loudly* instead of silently: a
  `preflight()` on `AlpacaBrokerClient` (package importable, keyring
  creds present for `env`, `get_account()` reachable and `ACTIVE`) that
  `dispatch_due_events` calls once when it first flips a strategy to
  `is_live`, emitting a critical `StructuredAlert` and keeping the
  strategy on the paper broker if it fails -- conservative-continue, per
  TRADING.md's failure behaviour. Also wire `check_correlation_limit`,
  the other `RiskManager` method with zero callers (multi-strategy
  correlation is a documented S6 gate that has never run): build the
  correlation matrix from trailing-60-day closes of the symbols already
  in `broker.list_positions()`, reusing `fetch_ohlcv`. Append the real
  hand-verification steps to `docs/trading-live-verify.md` -- per
  SAFETY, anything needing a real broker connection does not get done
  in a loop session.
  Files: `pyproject.toml`, `cerebral/requirements.txt`,
  `cerebral/trading/broker.py`, `cerebral/trading/live_tick.py`,
  `cerebral/trading/risk_limits.py` (caller only),
  `docs/trading-live-verify.md`.

- **S22 -- Intraday bars (decisions #39, #40).** "The higher-priority
  half of this expansion." `cerebral/trading_data.py`'s `fetch_ohlcv`
  has no `interval` parameter at all today (read directly; the docstring
  says daily and means it). Add `interval: str = "1d"`, passed through
  to `yf.Ticker.history(interval=...)`, and add it to `_cache_path`'s
  key -- the current key is `{symbol}_{start}_{end}.csv`, so a 5-min
  and a daily fetch for the same range would collide on the same file.
  The cache's `file_age.days < 1` freshness check is also wrong for
  intraday (a day-old 5-min bar set is useless); make the TTL a function
  of interval. New `AlpacaMarketDataClient` in
  `cerebral/trading/broker.py` (same module, same `_get_alpaca_credentials`
  keyring path, same paper/live env split -- decision #39's whole point
  is avoiding a backtest-vendor-vs-live-vendor mismatch, so it belongs
  beside the execution client, not in a new file) exposing
  `get_bars(symbol, start, end, interval) -> DataFrame` in the exact
  `Open/High/Low/Close/Volume` + ascending `DatetimeIndex` shape
  `live_tick.py`'s contract header pins down. `fetch_ohlcv` prefers it
  and falls back to yfinance. Per-strategy interval (#40): add an
  `interval TEXT NOT NULL DEFAULT '1d'` column to `strategy_specs` and
  the `StrategySpec` dataclass -- a plain `ALTER TABLE ADD COLUMN` with
  a default, which SQLite supports and the `conversation.py`
  `try/except OperationalError` migration pattern already models. Two
  daily-bar assumptions must move with it: `live_tick.py`'s module-level
  `DEFAULT_LOOKBACK_DAYS = 180` (`live_tick.py:54` -- ~124 daily bars;
  meaningless at 5-min) and `gauntlet.py:212`'s hardcoded
  `np.sqrt(252)` Sharpe annualisation, which silently understates
  intraday Sharpe by `sqrt(bars_per_day)`. Both become interval-derived.
  Files: `cerebral/trading_data.py`, `cerebral/trading/broker.py`,
  `cerebral/trading/strategy_store.py`, `cerebral/trading/live_tick.py`,
  `cerebral/trading/gauntlet.py`, `plugins/scheduler.py` (its
  `_run_gauntlet` backtest wrapper hardcodes `timedelta(days=365)` and
  daily `pct_change`).

- **S23 -- Intraday-aware graduation (decision #41).** The flat
  30-trade honesty-rule minimum lives in exactly two places:
  `ForwardRecord.compute_expectancy_ci` and
  `compute_live_expectancy_ci`'s `is_sufficient = n >= 30`
  (`forward_record.py:81,109`), plus
  `StrategyLifecycle.check_retirement`'s `len(...) >= 30`. Add a
  *distinct trading days* floor alongside the trade count so a fast
  intraday strategy can't cram 30 trades into one session and graduate.
  No schema change needed: `forward_fills.timestamp` is already a real
  UTC ISO string, so this is one query --
  `SELECT COUNT(DISTINCT substr(timestamp, 1, 10)) FROM forward_fills
  WHERE strategy_id = ?`. `is_sufficient` becomes
  `n >= 30 AND distinct_days >= <floor>`. Surface both numbers in the
  strategy card and panel so "insufficient sample" says *which* bar
  wasn't cleared -- the Honesty rule.
  Files: `cerebral/trading/forward_record.py`,
  `cerebral/trading/lifecycle.py`, `cerebral/settings.py` (the floor is
  user-configurable per #21), `tray/lib/trading-panel.js`.

- **S24 -- `plugins/stocks.py` (decisions #35, #37).** One new
  hand-authored ADR-0005 plugin, `PLUGIN_NAME = "stocks"`, following
  `plugins/markets.py`'s exact shape (module-level `PLUGIN_NAME` +
  `REQUIRED_CAPABILITIES` frozenset + `list_tools()` + `create()`;
  auto-discovered from the directory, no registry edit). Tools:
  `stock_fundamentals` (yfinance `Ticker.info` +
  `Ticker.quarterly_financials` -- zero new dependency, yfinance is
  already in `pyproject.toml` for #831), `sec_filings`
  (data.sec.gov, free, no key; honour the 10 req/s limit and the
  required `User-Agent` header, reusing `plugins/http_client.py` rather
  than a new HTTP path), and `sec_new_filings` (the daily filing index,
  filtered to S-1/424B4 for IPO detection). Per #37 this is
  **notification-only** -- it emits through the existing notification
  paths and never calls `run_gauntlet`; the user decides whether to
  trade an IPO with their own strategy. `REQUIRED_CAPABILITIES` is
  `{"external_data_read", "network_egress_cloud"}`, matching `markets`.
  Do not extend `plugins/finance.py` (receipts/OCR) or
  `plugins/markets.py` (quotes, no depth).
  Files: `plugins/stocks.py` (new), `cerebral/tests/test_plugin_stocks.py`
  (new).

- **S25 -- `origin='discovered'` + screening cost (decisions #42, #38).**
  Two small mechanism changes that S27 cannot land without. (a) #42:
  `strategy_versions.origin` carries a hard SQL
  `CHECK(origin IN ('generated','user_edited','mixed'))`
  (`strategy_store.py:62`) inside a `CREATE TABLE IF NOT EXISTS`. See
  "Open sub-decisions" -- this needs a real migration, not a DDL edit.
  Also teach `render_provenance` (`strategy_store.py:98`) a `discovered`
  branch; today an unknown origin silently falls through to the
  `f"strategy (v{v})"` default, which would erase the very fact #42
  exists to record. (b) #38: the sandbox screening cost. See "Open
  sub-decisions" -- the recommendation is that this slice adds *no*
  batching to `sandboxed_eval.py` at all.
  Files: `cerebral/trading/strategy_store.py`, and (conditional on the
  #38 sub-decision) `cerebral/trading/sandboxed_eval.py`.

- **S26 -- Activity Log (decision #46).** The trust prerequisite --
  lands *before* the autonomous loop, per CONTEXT.md's own glossary
  entry ("the trust prerequisite for letting Felix act autonomously
  without a human reviewing each action first"). Storage is not new:
  `conversation_turns` + `ConversationStore` + `main.py`'s `_record_turn`
  are real, persisted and profile-scoped. What is new is a **query
  layer** -- `ConversationStore` has no kind-filtered or time-ranged
  reader today, only `list_recent(profile_id, limit)` (cross-thread,
  unfiltered) and `list_recent_for_thread`. Add `list_activity(profile_id,
  *, kinds=None, since=None, limit=...)`. Retrofit the two loops that
  log to console only: `main.py`'s `_scheduler_loop`
  (`main.py:3420-3421`, currently `logger.info(f"[cerebral] Dispatch
  result...")`) and `plugins/self_dev.py` (which already has the
  `set_record_turn_fn` seam wired at `main.py:7145` and uses it for two
  events -- extend, don't rebuild). Batch routine screening into one
  summary turn per pass ("screened 500, 3 candidates"); log real
  decisions individually. UI: a sixth `data-route` in
  `tray/lib/sidebar-router.js`'s `VALID_ROUTES` (currently
  `conversation, harness, library, trading, settings, profiles`), a
  `<button class="nav-item" data-route="log">` beside the five at
  `tray/windows/main.html:5216-5220`, a `<section class="pane"
  data-route="log" hidden>`, and a new `tray/lib/activity-log.js`
  following `trading-panel.js`'s UMD + `module.exports` + fake-`document`
  unit-test convention (this repo's jest config has no jsdom installed
  and no test uses one -- do not add it). Plus a filtered Activity
  section inside the Trading pane. Two real constraints are in "Open
  sub-decisions" (encryption-at-rest, and the no-active-profile drop).
  Files: `cerebral/db/conversation.py`, `cerebral/main.py`,
  `plugins/self_dev.py`, `plugins/scheduler.py`,
  `tray/windows/main.html`, `tray/lib/sidebar-router.js`,
  `tray/lib/activity-log.js` (new), `tray/tests/activity-log.test.js` (new).

- **S27 -- Autonomous discovery loop (decisions #32, #33, #34, #36, #44).**
  The expansion proper. Decision #33's shape is settled and this slice
  just builds it: **one** convergence path
  (`symbol + hypothesis + code -> run_gauntlet`, unchanged) with
  multiple entry points. New `cerebral/trading/discovery.py` holding
  the trigger logic; the tools it calls are all existing --
  `plugins/browser.py`'s `web_search` and `navigate` (#34, no new
  crawler), `plugins/stocks.py` from S24, and
  `plugins/scheduler.py`'s `_run_gauntlet`, which already accepts
  `claim`/`url`/`book`+`chapter` as alternative idea sources plus the
  `origin`/`parent_version`/`strategy_id`/`components_json` passthroughs
  S17/S18 added -- so the convergence point needs **no signature
  change**, only `origin='discovered'` from S25. The LLM-judge
  pre-filter (#44) reuses the same free routed model `to_strategy`
  already uses (`task_type="coding"` via `self._router`, decision #26) --
  add a `judge_idea(idea, router) -> (bool, reason)` beside
  `to_strategy` in `cerebral/trading_ideas.py`, rejecting vague or
  non-testable claims before the expensive gauntlet, and log both the
  accept and the reject to the S26 Activity Log. Ticker universe (#36):
  a growing watchlist seeded by real sourced signals -- a small
  `discovery_watchlist` table (symbol, first_seen, source, last_screened),
  not a periodic full sweep. Ticker-specific ideas skip screening
  entirely; pattern-general ideas run the cheap pre-filter first.
  Cadence: reuse `SchedulerPlugin`'s existing recurring-event mechanism
  (`list_due_events`/`mark_event_run`) rather than a second background
  loop in `main.py` -- one dispatcher already exists and is tested.
  Files: `cerebral/trading/discovery.py` (new),
  `cerebral/trading_ideas.py`, `plugins/scheduler.py`,
  `cerebral/main.py` (loop wiring only).

- **S28 -- Fundamentals gate at graduation (decision #45).** Narrowly
  scoped by the decision itself: for a **never-before-traded** ticker
  only, at **paper->live graduation** only -- not at idea-sourcing
  time, not blocking paper trading. Hook into
  `StrategyLifecycle.check_graduation` (`lifecycle.py:114`), after the
  CI test passes and before `state.status = "live"`: pull the latest
  10-Q/10-K via S24's `sec_filings`, LLM-scan for red-flag language
  (going concern, restatement, investigation, delisting), and on a hit
  refuse the promotion with a critical `StructuredAlert` (the dispatcher
  S20 wired). Previously-vetted tickers skip the re-check -- a
  `vetted_tickers` row keyed by symbol + filing accession number, so a
  *new* filing re-triggers the scan.
  Files: `cerebral/trading/lifecycle.py`, `cerebral/trading/discovery.py`
  (the vetting store), `plugins/stocks.py` (caller only).

**Decision #43 is not a slice.** The arm mechanism (`trading_live_arm`,
S11b) is already built and needs no new code; the user flips it by hand.
It is gated on S20 and S21 both having landed and been hand-verified --
per #47, nothing reaches `trading_live_arm=True` on top of an unwired
risk gate, and per this blueprint's spike finding, nothing reaches it
without `alpaca-py` installed either.

## Open sub-decisions -- need a call, not a silent invention

Each is a real ambiguity found by reading the code, paired with a
recommendation, the way the grill itself paired every question with one.

1. **#42's CHECK-constraint migration mechanics.** SQLite (3.49.1 here)
   cannot `ALTER` a CHECK constraint -- and the trap is worse than that:
   `strategy_store.py:49` uses `CREATE TABLE IF NOT EXISTS`, so editing
   the DDL string to add `'discovered'` is a **no-op on every existing
   database**. A fresh `tmp_path` test suite would go entirely green
   while the user's real `strategy_specs.db` raises `IntegrityError` the
   first time a discovered strategy is saved. That is precisely the
   test-passes-production-breaks class this campaign's hand-verification
   discipline exists to catch. **Recommendation:** drop the CHECK
   constraint entirely and validate in Python -- one
   `if origin not in _VALID_ORIGINS: raise ValueError` at the top of
   `StrategyStore.save`. It is one code path instead of two, it produces
   a readable error instead of an opaque `IntegrityError`, and it is
   directly unit-testable. Migrate existing DBs with a single guarded
   rebuild in `__init__`: probe
   `SELECT sql FROM sqlite_master WHERE name='strategy_versions'`, and
   if it contains `user_edited` but not `discovered`, run the standard
   four-step (`CREATE strategy_versions_new` without the CHECK,
   `INSERT INTO ... SELECT * FROM strategy_versions`, `DROP`, `ALTER
   RENAME`) inside one transaction. Safe here specifically because
   `strategy_versions` has no foreign keys pointing at it, no separate
   indexes and no triggers -- verified against the actual DDL. The
   regression test must run against a DB **created with the old DDL**,
   not a fresh one, or it proves nothing.

2. **#38's batched-sandbox shape.** There is a hard mechanical blocker.
   `sandboxed_eval.py` transports both the strategy code and the bars
   **as base64 argv**, and its own header comment explains why this is
   not a style choice: a file the parent writes into the workdir before
   `spawn()` is *not* readable by the AppContainer, because
   `_ac_grant_workdir`'s ACE lands on the per-spawn SID after the file
   already exists and Windows does not retroactively propagate a folder
   ACL onto pre-existing children (confirmed empirically during S13).
   Windows `CreateProcess` caps `lpCommandLine` at 32,767 characters.
   One symbol at 180 daily bars is already ~14KB base64 -- roughly 45%
   of the budget. **Batching hundreds of tickers through argv is not
   merely slow, it is impossible**, and `WindowsSandbox.spawn` has no
   `stdin` parameter to route around it (checked the signature at
   `cerebral/sandbox/_windows.py:508`). **Recommendation: don't batch
   the sandbox -- don't use the sandbox for screening at all.** The
   sandbox exists because *generated strategy code is untrusted*. The
   cheap universe pre-filter in decision #33 is Felix's own trusted code
   (price / volume / market-cap / liquidity thresholds), not generated
   source, so it runs in-process at zero spawn cost. Only the handful of
   tickers that survive the pre-filter get a real untrusted evaluation,
   one spawn each -- which fully satisfies #38's actual stated goal
   (don't pay ~1s/spawn x thousands) with **no change to
   `sandboxed_eval.py` whatsoever**. This contradicts #38's literal
   wording ("needs a batch-evaluate entry point in `sandboxed_eval.py`"),
   which is why it is flagged here rather than assumed. If real batching
   is still wanted, it needs its own spike first: add `stdin` support to
   `WindowsSandbox.spawn` and re-verify the AppContainer can actually
   read it, exactly the way S13's icacls spike had to be re-run before
   S13 could be called verified.

3. **#46's content is encrypted at rest, so the Activity Log cannot
   filter in SQL.** `ConversationStore.append` runs every payload
   through `crypto.encrypt` (`cerebral/db/conversation.py:336`), Fernet
   via a keyring-held key. The `kind` column is plaintext, but
   `content_json` is ciphertext -- so a query like "trading-scoped
   entries" cannot be a `WHERE content_json LIKE '%trading%'`. Worth
   knowing: `search_threads` (`conversation.py:532`) already does
   exactly that `LIKE` against ciphertext and therefore silently matches
   nothing on any row written since encryption landed -- a pre-existing
   bug, out of scope here, but it is the reason not to copy that
   pattern. **Recommendation:** give the Activity Log its own plaintext
   discriminator rather than decrypting every row in Python. Introduce
   one new kind, `KIND_ACTIVITY = "activity"`, added to `VALID_KINDS`
   (the renderer already treats unknown kinds as `system_event`, so an
   older renderer degrades gracefully -- that contract is documented at
   `conversation.py:36`), and let `list_activity` filter on the indexed
   plaintext `kind` column. Sub-scoping (trading vs self_dev vs
   scheduler) is a `source` key inside the encrypted content, filtered
   in Python *after* the kind filter has already cut the row count to
   something small. Do not add a plaintext `source` column -- that
   leaks a slice of what encryption-at-rest exists to protect.

4. **#46 and the no-active-profile drop.** `_record_turn` returns
   silently when `_active_profile is None` (`main.py:3185-3186`). That
   is correct for chat -- a first-run state has no identity to attribute
   a turn to -- but for an autonomous loop it means actions taken before
   a profile is selected vanish, which is the exact opposite of what the
   Activity Log is for. **Recommendation:** leave `_record_turn`
   untouched and have the background loops skip *dispatch itself* while
   no profile is active, rather than dispatching un-logged. "Felix does
   not trade when it cannot record that it traded" is the honest rule
   and the smaller diff; a fallback system-profile bucket would create a
   second, unowned identity to reason about.

5. **#46 and thread pollution.** Every turn lands in a thread;
   `append` falls back to `get_or_create_default_thread`, which returns
   the profile's most recent one. Autonomous entries would therefore be
   interleaved into whatever chat thread the user last had open, and
   would bump its `updated_at` so it floats to the top of the thread
   list. **Recommendation:** one dedicated, stable per-profile thread
   titled `"Autonomous activity"`, resolved the same way
   `_LEGACY_THREAD_TITLE` already is (`conversation.py:237` -- look up
   by title, create once, reuse). Reuses an existing pattern, keeps the
   chat list clean, and the Activity Log's own reader is kind-filtered
   so it does not care which thread the rows live in.

## What already exists (reuse, do not rebuild)

| Piece | Where | Use for | Status |
|---|---|---|---|
| `web_search`, `navigate`, `read_pdf` | `plugins/browser.py` | #32/#34 autonomous idea sourcing | Built (Playwright/OpenClaw). Do **not** build a crawler |
| `run_gauntlet` / `_run_gauntlet` | `cerebral/trading/gauntlet.py:173`, `plugins/scheduler.py:377` | #33's single convergence point | Already accepts code/claim/url/book+chapter + origin/parent_version/strategy_id/components_json. **No signature change needed** |
| `to_strategy` + model router | `cerebral/trading_ideas.py:97` | #44 LLM-judge pre-filter | Same free routed model, `task_type="coding"` (#26). Add `judge_idea` beside it |
| `StrategySpec` / `StrategyStore` / `strategy_versions` | `cerebral/trading/strategy_store.py` | #42 provenance, #40 per-strategy interval | Built. `origin` CHECK needs a real migration (sub-decision 1); `interval` is a plain ADD COLUMN |
| `fetch_ohlcv` + file cache | `cerebral/trading_data.py:34` | #39/#40 intraday | Built, **daily only** -- no `interval` param exists. Cache key and TTL both need it |
| `AlpacaBrokerClient` + keyring creds | `cerebral/trading/broker.py:64` | #39 market data, #43 live orders | Execution only, no market-data methods. **`alpaca-py` not installed** -- see spike finding |
| `StubBrokerClient` | `cerebral/trading/broker.py:173` | every test | Real simulated fills/partials/rejects/positions. Tests never hit a real broker (SAFETY) |
| `RiskManager` | `cerebral/trading/risk_limits.py` | #47 | Fully built + tested, **zero production callers**. Wire, don't rewrite |
| `AlertDispatcher` / `StructuredAlert` | `cerebral/trading/alerts.py` | #47 block alerts, #45 refusal | Built. **Never constructed in `main.py`** -- alerts are silent in production |
| `ForwardRecord` + `forward_fills.timestamp` | `cerebral/trading/forward_record.py` | #41 trading-days scaling | Timestamps are real UTC ISO. One `COUNT(DISTINCT substr(...))`, **no schema change** |
| `StrategyLifecycle` (SQLite-backed) | `cerebral/trading/lifecycle.py` | #45 graduation gate | Built + restart-durable (S12). `apply_position_ramp`'s pct is returned but never applied |
| `sandboxed_eval.evaluate_signals` | `cerebral/trading/sandboxed_eval.py` | untrusted strategy code | Real AppContainer sandbox. argv-only transport -- see sub-decision 2 |
| `ConversationStore` / `conversation_turns` / `_record_turn` | `cerebral/db/conversation.py`, `cerebral/main.py:3168` | #46 Activity Log | Real, persisted, profile-scoped, encrypted. Needs a **reader**, not a store |
| `set_record_turn_fn` seam | `plugins/self_dev.py:390`, wired `main.py:7145` | #46 self_dev retrofit | Built; already used for 2 events. Extend it |
| `SchedulerPlugin` recurring events | `plugins/scheduler.py:305` (`list_due_events`/`mark_event_run`) | #36 discovery cadence | Built + tested. Reuse; do not add a second background loop |
| `plugins/http_client.py` | `fetch_html` etc. | #35 SEC EDGAR calls | Built. Reuse rather than a new HTTP path |
| `plugins/markets.py` | `market_price`, `market_quote` | reference shape for `stocks.py` | Quotes only, no OHLCV depth. Copy the plugin shape, don't extend the plugin |
| Trading panel + create-strategy form | `tray/lib/trading-panel.js`, `tray/windows/main.html:6245` | #46 trading Activity section | Built (S19 + follow-up). Nav-tab promotion pattern at `main.html:5216-5220` + `sidebar-router.js:26` |
| `rss_monitor` | `plugins/rss_monitor.py` | possible #37 IPO feed | Real subscribe/check mechanism. Optional -- EDGAR's daily index via `stocks.py` is the simpler path |

`plugins/finance.py` is receipts/OCR (personal accounting). Still unrelated. Still do not extend it.

## Sequencing constraints

```
S20 (risk gate) ─┬─> S21 (live preflight) ──> [#43 user arms, by hand]
                 │
                 ├─> S22 (intraday data) ──> S23 (graduation scaling)
                 │
                 └─> S24 (stocks plugin) ─┬─> S28 (fundamentals gate)
                                          │
                     S25 (origin + cost) ─┼─> S27 (discovery loop)
                     S26 (activity log) ──┘
```

S20 gates everything -- not because of a code dependency, but because
decision #47 says nothing that could reach live capital gets built on
an unwired risk gate. S26 gates S27 by the same kind of rule rather than
a compile-time one: #46 is the trust prerequisite for autonomous action,
so the log has to work before the loop that fills it starts running.
