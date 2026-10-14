# STRESS-WINDOWS-HANDOFF.md -- continue the cross-stock validation work (2026-09-19)

Start here in a fresh session. Numbered, discrete entries on purpose.

## Next slice -- start here

**Also done 2026-09-20: true intraday rules + runner (#1336), review category (#1334), bar-cache fixes (#1333/#1335/#1337); 0 of 8 day-trading rules profitable -- see FELIX-CAUSALITY.md.**

**DONE 2026-09-20 (PR #1331 merged, run complete): stress windows found 0 robust / 0 defensive of 165 strategies;
the permutation baseline re-run at 2 bps is still 0/247.** Results are in FELIX-CAUSALITY.md. Remaining ideas are under
"Suggested later" (out-of-sample selection, better strategy generation, longer windows, #1329 decision). The
"Remaining work" list below is historical.

## Pending: cost-corrected 5-year sweep (started 2026-09-20 ~13:00 ET, runs unattended)

The only unfinished item. The 5-year cross-stock sweep is re-running under the corrected cost model (PR #1339,
#1329 closed): 23,100 pairs, slow (~800-2,000 pairs/hour, so roughly 11-30 h). Everything else is merged and closed.

0. DONE 2026-09-21: cost-corrected sweep finished, 0 of 176 significant (same as legacy); see FELIX-CAUSALITY.md. Nothing pending.
   (history) 2026-09-20 14:37 ET: the sweep had been killed by a Felix restart (50/23,100 pairs; `running` stayed stale-True) and was resumed with `start_cross_stock_replay`.
   #1250 closed (fully built in #1328). #1251 axis 4 (parameter stability) deferred by operator: no strategy is consistent across tests. #1251 stays open only for this sweep.
1. Check: call `get_cross_stock_replay_status`; done when `running` is false and `pairs_done == pairs_total`.
   Do not restart Felix meanwhile (the sweep resumes from its cursor, but a restart wastes time). If it was
   interrupted, call `start_cross_stock_replay` again; it skips finished pairs.
2. Compare `vs_benchmark_ranked` / `vs_benchmark_significant` with the legacy ranking (0 of 176 significant).
   The pre-fix rows are in table `cross_stock_results_legacy_cost` (`cross_stock_results.db`) for a side-by-side.
3. Record the result in FELIX-CAUSALITY.md ("Cost model fixed" section says "Result pending") and update memory
   `project_stress_windows_handoff.md`. A null result is a valid result.
4. Not re-run: batch-replay numbers (`replay_runs.db`).

Later ideas (unchanged): out-of-sample selection, better strategy generation, 15-20y daily windows, more intraday
rules (only 8 untuned variants tested).

## Goal

Find out whether ANY of the ~255 book-derived strategies has a real, robust edge. Three passes so far all say no:
the 5-year sweep after look-ahead screening (0/228 significant vs buy-and-hold), and the random-timing baseline
(0/247 significant). Stress windows (2008 crash, 2010s, 2022 bear) plus a drawdown "risk view" are the next test.
Everything is INFORMATIONAL ONLY: nothing may touch `check_graduation`, `check_retirement`, `run_gauntlet`,
`auto_promote`, or place any order.

## State of master (all merged, all verified live)

1. FELIX-AUDIT S0-S9 complete (see FELIX-AUDIT.md "Campaign complete"). F10 real gates via FELIX-GATE-FOLLOWUP.md.
2. Causality (look-ahead) gate, FELIX-CAUSALITY.md: `cerebral/trading/causality.py` (60 cut points, stop at first
   mismatch, static shift(-N) guard = non-causal), verdicts in table `strategy_causality` (cross_stock_results.db),
   run at the top of each sweep on AAPL + the 2 stocks each strategy trades most. 28 of 283 strategies non-causal.
   Their `cross_stock_consistency` is cleared and they are excluded from every ranking.
3. Benchmark-relative ranking (PR #1328): `cerebral/trading/cross_stock_stats.py` (coin-flip sign test vs
   buy-and-hold, Benjamini-Hochberg across strategies), status payload `top_vs_benchmark`, History tab "Best vs
   buy-and-hold". Result: 0 significant.
4. Random-timing (permutation) baseline (PR #1330): `cerebral/trading/permutation_null.py`, table
   `strategy_permutation`, tool `start_permutation_baseline`, History-tab line. Result: 0/247 significant
   (10.2% of pairs nominal p<0.05 vs ~5% chance = a faint tilt, not a finding).
5. Performance fix: `conversation_turns` (thread_id,id)/(profile_id,id) indexes (commit 6543a58): connect went 10.6s -> 0.6s.
6. Open issue #1329: backtest cost model charges by SHARE PRICE not traded notional
   (`derive_trades` value = |delta| * price; cost = value * 2% / $10,000). Verified 0.001% on a $5 stock, 0.04% on
   $200, 0.3% on $1,500. **Operator decision (2026-09-19): the platform is commission-free, so costs are small ->
   use 2 bps per side of traded notional for ALL research paths.** Legacy `cost_model.py` and the live gauntlet are
   deliberately UNCHANGED until the operator explicitly approves (changing it alters live-promotion outcomes).
   Verify whether `gauntlet.py` builds its Trade objects with `derive_trades` before anyone trusts a gauntlet pass.

## On branch `feat/stress-windows` (WIP, 16 tests green, not merged)

1. `cerebral/trading/historical_bars.py` -- yfinance (adjusted daily) loader with its own sqlite cache
   (`bars_hist.db`), because the normal bar cache is Alpaca-fed and does NOT reach before ~2016 (2008 unreachable).
   `get_daily_bars(symbol, start, end)`; empty frame = no data (remembered, not refetched). Survivorship-biased.
2. `cerebral/trading/stress_windows.py` -- `RESEARCH_COST = 0.0002`; `windows()` = gfc (2007-10..2010-01), mid
   (2010..2020), bear22 (2021-12..2023-01), main (rolling 5y); `evaluate_window(position, returns, cost)` -> net /
   gross / benchmark return, max drawdown, benchmark drawdown, n_trades; `summarize_stress(rows_by_strategy)` ->
   per-window beat share, median excess, median drawdown gain, coin-flip p BH-adjusted per window, plus flags
   `robust` (beats B&H on >= half the stocks with positive median excess in EVERY window) and `defensive` (in every
   window: median excess >= -0.10 and median drawdown gain >= +0.10 -- the risk view).
3. Tests: `cerebral/tests/test_historical_bars.py`, `cerebral/tests/test_stress_windows.py`.

## Remaining work (in order)

1. Store: add table `strategy_stress(strategy_id, symbol, window, net_return, gross_return, benchmark_return,
   max_drawdown, benchmark_max_drawdown, n_trades, cost, created_at, PK(strategy_id,symbol,window))` to
   `CrossStockStore._init_schema` in `cerebral/trading/cross_stock_store.py` + `record_stress(...)`,
   `get_stress_done()` and `get_stress_rows()` (`{sid: {window: [row dicts]}}`, causal strategies only via
   `get_non_causal_ids()`). Mirror the `strategy_permutation` methods added in PR #1330.
2. Runner in `plugins/trading_replay.py` (mirror `_run_permutation_baseline` / `_permutation_pair`):
   - stocks: the first ~30 BASKET symbols with >= ~900 daily bars in 2006-2009 via `historical_bars.get_daily_bars`.
   - strategies: interval == "1d", eligible, causal only.
   - per (strategy, stock): fetch bars 2006-01-01 -> today ONCE, call `run_bars_verbose(code, bars, "1d")`
     (returns `equity, position, metrics, reason`; `position` = signals.shift(1)); ignore its cost-model returns;
     slice `position` and `bars.Close.pct_change()` per window and call `evaluate_window`. One sandbox spawn per
     pair (~7k pairs, ~1h with 4 workers, run in an executor). Record rows; skip pairs already done.
   - tool `start_stress_windows` (register in `list_tools` + `call_tool`), status fields in
     `get_cross_stock_replay_status`: `stress` = `summarize_stress(...)` trimmed (per-window ranked/significant,
     `robust`, `defensive` counts, top 5 rows) and a caveat string.
3. History tab (`tray/lib/trading-panel.js`, follow the permutation line added in PR #1330): one line
   "Stress windows: X of Y strategies beat buy-and-hold in every regime; Z defensive"; jest test in
   `tray/tests/trading-panel.test.js` using `withFakeDocument` / `fakeInteractiveMount`.
4. Set `permutation_null.DEFAULT_COST = 0.0002` (commission-free decision; it is 0.0005 today), update its tests/
   docstring, and re-run `start_permutation_baseline` after clearing `strategy_permutation` so all research shares
   one cost. Comment the decision on #1329 (do NOT change `cost_model.py`).
5. Tests for the runner (fake `get_bars` / `run` injected, like `test_permutation_baseline.py`), full suite
   (`python -m pytest cerebral/tests -q -p no:cacheprovider`, ~13 min, CAPTURE the exit code), jest
   (`cd tray && npx jest`), PR, merge, restart Felix, run `start_stress_windows`, report robust/defensive counts.
6. Suggested later: out-of-sample selection (pick candidates on `main`, judge only on gfc/bear22), better strategy
   generation (many book rules are vague or leaky), 15-20y windows, a decision on #1329.

## Live process and how to drive it

1. Felix/Cerebral IPC: `ws://localhost:7766`. Send `{"type":"call_tool","data":{"name":<tool>,"args":{},"record":false}}`
   and read the `tool_result`. Restart: back up `cerebral.err.log` first, then send `user_text_command` text
   `"restart felix"`; poll `{"type":"health_check"}` until `health_ok` (~60-90s). Code changes need a restart.
2. Tools: `start_cross_stock_replay` (runs the causality pass first), `start_permutation_baseline`,
   `get_cross_stock_replay_status` (all the summaries).
3. Data lives in `cerebral/data/cross_stock_results.db` (`cross_stock_results`, `strategy_causality`,
   `strategy_permutation`) and `felix-settings.json`.

## Lessons that cost time this session (do not relearn)

1. self_dev (Felix builds) reliably drops edit blocks that do not match verbatim: 5 of 7 slices needed hand repair.
   It only handles small single-file slices with exact quoted anchors; multi-file or bulk moves fail silently.
   Always read the real diff. A 2-file slice halves the main.py excerpt; main.py slices must be main.py-only.
2. A retried self_dev slice replays the old failure (run_id is label-derived): clear
   `StepLedger().clear("campaign-<driver>-<label>")` and move the clone dir aside first.
3. Pipe-to-tail hides pytest's exit code: redirect to a file, then `echo exit=$?`.
4. NEVER `git worktree remove` a worktree that has a `node_modules` junction: it deletes the real
   `tray/node_modules`. `cmd /c rmdir <junction>` first. (Memory: feedback_worktree_junction_node_modules.md.)
5. main.py, main.html, test files are CRLF in the working tree (LF in the index): preserve line endings when
   scripting edits (read with `newline=''`).
6. A leak only shows where a strategy actually trades: causality must be tested on stocks that trigger it, and with
   enough cut points (11 was too few; 60 catches subtle ones).
7. `grep -r` over `cerebral/` includes `cerebral/data/sandbox/` clones and can time out: use the Grep tool.
8. One PR per issue with `Closes #N`; commit before any Felix restart (boot rollback can reset an uncommitted tree).
9. Sandbox `test_sandboxed_eval::test_workdir_is_cleaned_up_after_a_run` and `test_plugins_browser::...real_openclaw...`
   are environment-flaky (pass in isolation).
10. Firewall: this whole line of work is informational only.
