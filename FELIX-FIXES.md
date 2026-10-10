# FELIX-FIXES.md -- self_dev campaign driver: the 2026-10-06 whole-system review

Source: whole-system review 2026-10-06 (live `cerebral.err.log` 2026-09-29..10-06, full pytest
6,063 pass / 1 fail, tray jest 976/976, open issues, launcher log, plugin registration). Operator
decision the same day: fix everything found, designed here, built by Felix through
`self_dev_campaign`, with Felix merging its own work (the test gate is the only merge gate).

Each slice is one GitHub issue whose body is the complete instruction self_dev receives: exact
files, exact SEARCH/REPLACE text, acceptance checks. Every SEARCH block was dry-run against
master 2026-10-06 (each matched exactly once), and the full pytest + jest suites were run with all
ten X-slices applied together.

After this driver is `done`, continue with `HELP.md` (the Help sidebar: HELP2, HELP3, HELP4a-c,
HELP5, HELP6a-b, HELP7).

## Status: ready

## Next slice -- start here

- **Active:** Y1 -- #1399
- **Model:** self_dev-default

## Queue

Order matters only where noted. X0 goes first: it removes the two tests that fail for reasons
unrelated to the code (a live network search, and a UTC-vs-New-York date check that fails every
evening after 8 PM ET), either of which would mark a correct slice `tests_failed` and block the run.

- [x] XA -- #1372 -- self_dev applier: uniform-reindent fallback + log the reply when nothing applies. Built by Claude (Sonnet), not self_dev: it is the step that failed X0 (2026-10-09, Budd's edit was right but indented +4)
- [x] XB -- #1377 -- self_dev test gate: await the dangling heartbeat task + trust a clean pytest summary over a flipped exit code (Claude/Sonnet)
- [x] XC -- PR #1380 -- `test_sandboxed_eval` workdir-cleanup test pinned to its own uuid (was racing live Felix + concurrent suites on a machine-global folder) (Claude/Sonnet)
- [x] X0 -- #1357 -- live web-search test opt-in (`OPENMIND_LIVE_TESTS=1`); after-8PM-ET date test uses the market date
- [x] X1 -- #1358 -- `cerebral/tests/test_plugin_google_workspace.py` so the gate stops refusing `google_workspace`
- [x] X2 -- #1359 -- `sentiment.py`: one-line warnings instead of 25-line tracebacks for handled failures
- [x] X3 -- #1360 -- `live_tick.py`: trend basket exempt from the sentiment gates (backtested with no news filter)
- [x] X4 -- #1361 -- `main.py`: per-stock sentiment skips parked strategies and the trend basket (`main.py` only)
- [x] X5 -- #1362 -- `main.py`: timestamps on every log line; heartbeat to DEBUG (`main.py` only)
- [x] X6 -- #1363 -- `tray/main.js`: "Cerebral log" menu opens `cerebral.err.log`; keep git fetch stderr
- [x] X7 -- #1364 -- `broker.py`: `get_daily_bars_multi` (one Alpaca request for many symbols)
- [x] X7b -- #1386 -- cap `get_daily_bars_multi`'s end at now-16min (Alpaca free plan refuses the last 15 min of SIP data; found live in market hours)
- [x] X8 -- #1365 -- `trend_basket_selection.py`: `sp500_members()` loader (needs the committed `cerebral/trading/sp500_members.txt`)
- [x] X9 -- #1366 -- trend basket breadth + picks from the S&P 500, batched fetch (needs X7 + X8 merged)
- [ ] Y1 -- #1399 -- basket exit rule on closes, flat on day 20 (match sp500_backtest.py) [follow-up 2026-10-10]
- [ ] Y2 -- #1400 -- basket slot = 10% of current equity, not starting capital [follow-up 2026-10-10]

## Landed PRs

Live-verified 2026-10-09: trend basket reading through the new path = pool 503, breadth 33.2%, 23.6s (was ~10 min on the movers pool), in market hours.

- PR #1373 -- XA (built by a Claude Sonnet agent, diff hand-reviewed, full suite 6,071 pass)

- PR #1374 -- X0 (auto-merged by self_dev_campaign)
- PR #1375 -- X1 (auto-merged by self_dev_campaign)
- PR #1376 -- X2 (Felix-built; gate false-negative from the #1107 exit-code flake, merged by hand)
- PR #1379 -- XB (Claude/Sonnet; closes #1107/#1110)
- PR #1378 -- X3 (Felix-built; gate hit the sandboxed_eval shared-folder race, merged by hand)
- PR #1380 -- XC (Claude/Sonnet)
- PR #1381 -- X4 (auto-merged by self_dev_campaign)
- PR #1382 -- X5 (auto-merged by self_dev_campaign)
- PR #1383 -- X6 (auto-merged by self_dev_campaign)
- PR #1385 -- X7 (auto-merged by self_dev_campaign)
- PR #1387 -- X7b (auto-merged by self_dev_campaign)
- PR #1388 -- X8 (auto-merged by self_dev_campaign)
- PR #1389 -- X9 (auto-merged by self_dev_campaign)
## Evidence and design (hand-review context, not part of any single issue)

**What the review found, by impact.**

1. *Trend basket measures the wrong stocks (X7-X9).* The backtest behind the Trend Basket card's
   reference (+0.71%/bet) uses the S&P 500; live used Alpaca's ~90 daily movers/most-actives.
   Live breadth 33-47% vs the real S&P 21-29% on the same days. ADR-0038 amendment 2026-10-06
   records the decision. Fetching the 503 members one at a time took 701s cold; one batched
   request took 6.2s, so the batch is part of the fix, not an optimization.
2. *Sentiment gates can block the basket's buys (X3).* It was exempted from the 5% stop backstop
   (2026-09-24) but not from the market-wide and per-stock sentiment gates, which it was never
   backtested with, and which fire hardest exactly when breadth recovers after a selloff.
3. *Sentiment pre-pass scores parked strategies (X4).* ~30 tickers of strategies that can't trade
   (`trading_only_prefix`), a web search + LLM call each; ~600 of those calls failed during Budd
   outages alone.
4. *`google_workspace` refused at every boot (X1).* The ADR-0034 gate keys on `PLUGIN_NAME`; the
   test file was named after the module. The offline fallbacks never loaded.
5. *Logs (X2, X5, X6).* 124,850 lines in a week, ~95% heartbeats, no timestamps, 1,754
   tracebacks all from one handled-failure warning in `sentiment.py`. The tray's "Cerebral log"
   menu item opened the always-empty `cerebral.log`. Rotation itself works (5 kept per kind).
6. *Flaky tests (X0).* See the Queue note.

**Not slices (recorded so nobody re-investigates):**
- OpenClaw "scope upgrade pending approval" loop: known bootstrap deadlock, deliberately left
  (2026-09-16, operator). The `--token` argv warning is a documented choice in
  `plugins/openclaw_channels.py` and the relaunch is already backed off to 300s.
- Budd (bonsai) 502/500 outages: server-side. No local fallback while the desktop owns the GPU.
- #1355 (empty reply at the gateway's 512 max_tokens): fixed by 8095e4f; closed when master was pushed.
- Old git stashes (5): left alone; dropping them destroys work.

**Running it.** `python .campaign-scratch/fire_campaign.py FELIX-FIXES.md 1 <timeout>` per slice
(the Foreman loop: fire, read the real PR diff, land, next). After every landed slice check the
`## Status:` line: `self_dev_campaign` writes `blocked` on a gate failure and never resets it.
After X4/X5 land, Cerebral must restart before the live acceptance checks mean anything.
