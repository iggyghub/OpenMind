# TRADING.md -- Stock trading campaign driver

Design: ADR-0026 (not written yet).
Scaffolded 2026-08-21, grill closed 2026-08-22.

## Status: active

## Next slice -- start here

- **Active:** S37d -- #925 -- main.py: wire reset + broadcast paper_archives.
- **Model:** sonnet

## Queue

- [x] S1a -- #831 -- OHLCV data module (yfinance)
- [x] S1b -- #832 -- Backtest engine + reference strategy
- [x] S2 -- #833 -- Cost/slippage model + OOS + walk-forward gates
- [x] S3 -- #834 -- Full gauntlet + strategy card
- [x] S4 -- #835 -- URL/web/book -> strategy spec
- [x] S5a -- #836 -- Alpaca broker integration
- [x] S5b -- #837 -- Risk limits + failure behaviour
- [x] S5c -- #838 -- Paper forward record + auto-promotion
- [x] S6 -- #839 -- Autonomous live execution + retirement + alerting
  (lifecycle mechanics landed; wiring gaps remain -- see Landed PRs note)
- [x] S7 -- #848 -- Wire autonomous paper-trade execution end-to-end
  (per-strategy forward-record scoping fully landed; paper-trade path now
  correct but one-shot, not recurring; UI component built but unwired --
  see Landed PRs note. Issue #848 updated in place with remaining scope,
  not closed)
- [x] S8 -- #848 -- Close S7's remaining gaps: a real recurring dispatcher
  for scheduled paper trades, a fill-price field on Order (broker.py),
  and wiring the Trading Panel UI into main.html -- landed the fill-price
  field (StubBrokerClient gets a real simulated price, AlpacaBrokerClient.
  get_order extracts the real fill_avg_price); dispatcher and UI wiring
  still open -- see Landed PRs note
- [x] S9 -- #848 -- Close S8's remaining gaps: a real recurring dispatcher
  for scheduled paper trades, and wiring the Trading Panel UI into
  main.html -- both landed, hand-verified end-to-end (not just tests
  passing). Issue #848 closed. See Landed PRs note.

- [x] S10 -- no issue -- Strategy-to-dispatch bridge: pin down the
  `def strategy(data) -> signals` live contract, evaluate real signals on a
  tick, track position state from the broker's own book, and record real
  realized P&L on a close (the hard blocker on graduation). See Landed PRs.

- [x] S11b -- #852 -- Live trading: arm/disarm toggle (part 2), live/paper
  branching in the dispatch loop (part 4). Landed by hand, not via
  self_dev's own PR #854 (3 real bugs found -- see Landed PRs). Part 3
  (production caller of run_gauntlet) not delivered; continuing as S11c.

- [x] S11c -- #852 -- Live trading, Part 3 only: a real, reachable
  production caller of run_gauntlet. Landed by hand, not via self_dev's
  own PR #855 (2 more real bugs found -- see Landed PRs). All four parts
  of S11 now exist; live trading is still not "ready" -- see "Live
  trading: what a full trace actually found" for two more gaps found
  hand-tracing the complete chain.

- [x] S12 -- #856 -- Persist StrategyLifecycle status across a restart
  (Part A) + surface phase (paper/live) in the trading UI (Part B).
  Landed by hand, not via self_dev's own PR #857 (a test-isolation bug
  -- see Landed PRs). Both gaps the full hand-trace found are closed.

- [x] S13 -- #858 -- Sandboxed strategy evaluation, mechanism only
  (unwired). Landed by hand, not via self_dev's own PR #865 (2 real bugs
  found, including a genuine structural sandbox-usage bug -- see Landed
  PRs).
- [x] S14 -- #859 -- Retire in-process compile_strategy exec; route
  both call sites through S13's sandbox. Landed by hand, not via
  self_dev's own PR #866 (a stale import + an outdated test -- see
  Landed PRs).
- [x] S15 -- #860 -- Wire to_strategy to a real free model (currently
  every generated strategy is the hardcoded stub, regardless of source).
  Landed via auto-merged PR #867, hand-fixed on top (async router bug +
  zero tests -- see Landed PRs).
- [x] S15b -- #860 -- Give to_strategy a real, reachable production
  caller -- nothing calls it in the running app yet. Issue #860 updated
  in place, reused across S15/S15b, now closed. Landed by hand, not via
  self_dev's own PR #868 (same missing-await bug class as S15's #867 --
  see Landed PRs).
- [x] S16 -- #861 -- Strategy lineage: versions, structured provenance,
  component tracking.
- [x] S17 -- #862 -- Edit a strategy's code (new version, full
  re-validation, restarts clean on a live strategy -- user-confirmed).
- [x] S18 -- #863 -- Mix strategies into a composite (unanimous/majority
  voting).
- [x] S19 -- #864 -- Trading panel: strategy list, source view, edit
  box, lineage display.
- [x] S20 -- #873 -- Wire RiskManager into the live dispatch path (P0).
  Landed by hand, not via self_dev's own PR #882 (no tests at all, 4 real
  bugs found -- see Landed PRs).
- [x] S21 -- #874 -- alpaca-py dependency + live-path preflight (P0b).
  Narrowed 2026-08-24 after 5 straight self_dev "no commit" failures on
  the original 4-part issue -- correlation-limit wiring split out to
  S21b/#883. Landed by hand, not via self_dev's own PR #884 (no
  test-injection seam for the live broker -- see Landed PRs).
- [x] S21b -- #883 -- Wire check_correlation_limit into the live
  dispatch path (split from #874). Landed by hand, not via self_dev's
  own PR #885 (2 test bugs + 1 real production bug found -- see Landed
  PRs).
- [x] S22 -- #875 -- Intraday bars: per-strategy interval, Alpaca
  Market Data. Landed by hand, not via self_dev's own PR #886 (zero
  tests, 2 real production bugs -- see Landed PRs).
- [x] S23 -- #876 -- Intraday-aware graduation: distinct trading-days
  floor. Landed by hand, not via self_dev's own PR #887 (broken
  monkeypatch + 6 pre-existing tests broken by the tuple-size change --
  see Landed PRs).
- [x] S24 -- #877 -- plugins/stocks.py: fundamentals, SEC filings, IPO
  detection. Landed by hand, not via self_dev's own PR #888 (the plugin
  as written could never have loaded -- see Landed PRs).
- [x] S25 -- #878 -- origin='discovered' lineage + screening cost
  decision. Auto-merged correctly (PR #889, commit 112e99f) --
  hand-verified the CHECK-constraint migration for real against an
  old-schema DB and added the regression tests the blueprint asked for
  but the PR didn't include -- see Landed PRs.
- [x] S26 -- #879 -- Felix-wide Activity Log (new top-level nav tab).
  Landed by hand, not via self_dev's own PR #890 (a bare tray-only stub,
  none of the backend the acceptance criteria required -- see Landed PRs).
- [x] S27 -- #880 -- Autonomous discovery loop: idea sourcing +
  screening. Landed by hand, not via self_dev's own PR #891 (every
  external call referenced a nonexistent function or wrong signature --
  see Landed PRs).
- [x] S28 -- #881 -- Ticker fundamentals red-flag gate at live
  graduation. Landed by hand after 5 consecutive genuine self_dev
  "no commit" failures -- see Landed PRs. **The full S20-S28 blueprint
  is now complete.**

- [x] S29 -- #892 -- Trading pane "Tickers" sub-tab: per-ticker
  progress view. Grilled 2026-08-24 (decisions #48-#51). Adds a
  sub-tab strip to the Trading pane (Strategies/Tickers); the Tickers
  view lists every currently-active ticker (watchlist, mid-gauntlet,
  or backing a paper/live strategy) with a step tracker pre-strategy
  and, post-strategy, a cumulative equity-curve-vs-buy-and-hold-
  benchmark chart per phase (paper and live kept as separate,
  never-joined lines), each trade a hoverable dot showing its
  strategy. Reuses the existing per-strategy canvas chart
  (`tray/lib/trading-panel.js:254`) and the gauntlet's existing
  `vs_benchmark` definition (`gauntlet.py:277`) rather than inventing
  either. Needs a GitHub issue filed before a self_dev campaign can
  pick it up.

- [x] S30 -- #894 -- Persisted per-attempt discovery log: gauntlet
  verdict/reasoning per candidate. Closes the gap S29 (decision #49)
  found -- `DiscoveryWatchlist` has no per-attempt record, so a
  screened-but-strategy-less ticker can't be told apart from one that
  was judged/dispatched and actually rejected by the gauntlet. Adds a
  persisted log of each `run_gauntlet_fn` dispatch's outcome (verdict +
  which gate failed + why), a real "rejected" stage in
  `build_ticker_view`, and that reason surfaced on the Tickers card.
  No live "gauntlet-running" status -- a dispatch is one awaited call,
  nothing to poll mid-flight. See #894 for full acceptance criteria.

- [x] S31 -- #896 -- Manual discovery start/stop with duration + market-
  hours gate for paper dispatch. Separate bugfix (not this slice, landed
  same day): the whole autonomous scheduler loop was silently dead
  (events table schema drift + a tz-aware timestamp comparison crash).
  With that fixed, discovery becomes a fully-automatic always-on
  process with no user control at all -- this slice adds a real
  discovery_enabled/discovery_stop_at toggle (settings-backed,
  start_discovery/stop_discovery tools), Trading panel UI for it, and
  gates paper-trade dispatch to Mon-Fri 9:30-16:00 America/New_York
  (stdlib zoneinfo, no holiday calendar -- disclosed gap). See #896 for
  full acceptance criteria.

- [x] S32 -- #898 -- Manual per-strategy Halt/Resume + trade-count
  visibility on the Strategies list. Found live 2026-08-27 testing book-
  sourced strategies: no way to tell if a strategy is actually trading
  (list shows only name + status, no activity signal) and no way to stop
  one manually -- the only existing halt path is the automatic rolling-
  CI/drawdown check in StrategyLifecycle.check_retirement. Adds public
  halt_strategy/resume_strategy methods on StrategyLifecycle (thin
  wrappers reusing the existing private _halt_strategy, not new logic),
  two matching SchedulerPlugin tools wired to the real `_trading_lifecycle`
  via a post-construction seam (same pattern as _on_trading_change), and
  a trade-count badge + Halt/Resume buttons in the tray Strategies view.
  Resume always lands back at "paper", never straight to "live" -- a
  resumed strategy re-earns live status through the normal 30-trade
  graduation gate again. self_dev's own attempt (PR #899) implemented the
  four files correctly but shipped two real gaps, fixed by hand: the two
  new tools' `Tool(...)` entries were never added to `list_tools()` (the
  dispatch handlers existed, so a raw call_tool IPC message worked, but
  the LLM planner/chat routing and any tool-introspection UI couldn't see
  them at all), and zero test coverage was added for any of it -- writing
  that coverage caught a real bug (`s.live_trades` rendered literally as
  the string "undefined" when missing). Also surfaced, unrelated to this
  slice: scheduler.py's capability manifest hadn't been updated to declare
  `fs_delete` when delete_book's shutil.rmtree() landed earlier the same
  day, caught by a full-suite run (that feature's own commits only ran
  the scoped trading test files) -- fixed separately on master. See #898 for full acceptance
  criteria and the PR for the full bug account.

- [x] S33 -- #900 -- Real book pause/resume + fix stop-on-done bug. Today
  Stop is a hard cancel and Redo always restarts a book from chunk 0 --
  no way to pause overnight and continue without losing hours of
  progress, which the user hit directly running real multi-hour book
  ingestion. Adds `resume_book`: re-extracts + re-chunks the stored file
  (deterministic given the same file + chunk_chars, no new persistence
  needed), verifies the chunk count still matches before trusting the
  index, then continues from `processed_chunks` with `_run_book_
  ingestion` gaining a resume-offset seam so BookStore sees real
  cumulative progress instead of a count that restarts low. Also fixes a
  real bug found live this session: `stop_book`'s orphaned-book branch
  blindly overwrote status to "stopped" with no check the book wasn't
  already terminal -- two genuinely finished books got mislabeled by a
  stray Stop click, hand-corrected via direct DB calls both times.
  self_dev's own edit tool crashed outright on this issue (`Edit failed:
  expected string or bytes-like object, got 'NoneType'`, no commit
  produced) -- landed by hand from the same near-diff-level spec instead,
  same division of labor as S17-S19/S28-S30/S32. Full suite green (5342
  passed, 7 skipped Python; 824/824 JS) before commit. Live-verified
  post-restart: `resume_book` on a real stopped book (1/2 chunks)
  returned `resumed_from_chunk: 1` and finished at 2/2 -- continues from
  the real persisted offset, not 0. See #900 for full acceptance
  criteria.

- [x] S34a -- #903 -- settings.py only: `trading_paper_enabled` (default
  True) + `trading_paper_starting_capital` settings keys.
- [x] S34b -- #904 -- broker.py only: `StubBrokerClient` honors
  `config["starting_cash"]` instead of a hardcoded `10000.0` in all three
  of cash/equity/buying_power.
- [x] S34c -- #905 -- main.py only: construct `_trading_broker` with the
  new starting-capital setting, gate `_scheduler_loop`'s dispatch on
  `trading_paper_enabled`, add a `paper_control` key to the
  `trading_update` broadcast.
- [x] S34d -- #906 -- scheduler.py only: `start_trading`/`stop_trading`
  tools (mirroring `start_discovery`/`stop_discovery`), with explicit
  `list_tools()` registration called out (S32/#898 missed this for its
  own two tools).

  S34a-d supersede the original single-issue S34/#901 (closed) after 5
  straight self_dev failures on it: 2 were real bugs in self_dev's own
  edit tooling (fixed same day, see the self_dev_io.py commits), the
  other 3 were "Edit step produced no commit" -- the file-planning step
  wasn't reliably selecting all 4 needed backend files (and the original
  body also mistakenly asked for a `tray/windows/main.html` edit, which
  `_self_dev_edit`'s own candidate-file list excludes entirely --
  guaranteed to miss). Split into one file per issue so each self_dev
  attempt only has one file to plan against. The Start/Stop + settings
  control in `tray/lib/trading-panel.js` (mirrors the existing
  `_renderDiscoveryControl` pattern, no `main.html` changes needed) is
  hand-built separately once S34a-d land, same as every other
  tray/-touching piece in this campaign (guardrail path, always escalates
  to human review anyway). See #903/#904/#905/#906 for full acceptance
  criteria on each.

  **All 4 landed same day, split confirmed the fix.** S34a/S34b: real
  diffs, sandbox reported `tests_failed` but a full local run on each PR
  branch was clean (5338-5339 passed) -- sandbox test-environment
  flakiness, merged by hand as-is. S34c: a genuine bug -- the edit
  referenced `_settings.get(...)` at its construction line (260), but
  `_settings` itself isn't defined until line 355; fixed by hand (kept
  the library-default `StubBrokerClient()` at 260, re-pointed it at the
  real setting right after `_settings` exists). S34d: repeated the exact
  gap the issue explicitly warned about -- `start_trading`/`stop_trading`
  got dispatch routing and method bodies but never a `Tool(...)`
  registration in `list_tools()` (same class of miss as S32/#898), caught
  by the very test self_dev itself wrote
  (`test_start_stop_trading_tools_exist_in_list_tools`) plus a second,
  unrelated stale exhaustive-tool-name assertion in
  `test_plugins_time_notes.py`; both fixed by hand. Full suite green
  after all four merges (5342 passed, 7 skipped).

- [x] S35a -- #911 -- forward_record.py only: `ForwardRecord.get_total_pnl()`
  -- realized P&L summed across every strategy, all-time.
- [x] S35b -- #912 -- main.py only: wire `total_pnl` into the
  `trading_update` broadcast payload.
- [x] S35c -- #914 -- forward_record.py only: `ForwardRecord.get_all_fills()`
  -- every fill across every strategy, chronological, no strategy_id filter.
- [x] S35d -- #915 -- main.py only: wire `all_fills` into the
  `trading_update` broadcast payload.

  Refined design per the user's 2026-08-28 request: a single Overview
  sub-tab (leftmost-adjacent) with one multi-line graph -- one hoverable
  line per strategy, hover shows that strategy's current status + total
  gain/loss -- plus one grand-total figure near the graph. Supersedes the
  original single-issue S35/#902 (closed): most of what's needed
  (`positions[i].equity_curve`/`status`/`name`) already exists on the
  broadcast from S19, so the backend piece is now just the ONE missing
  grand-total number, split single-file (S34's proven pattern) rather
  than the earlier fill-table-heavy multi-file design. S35a/b landed
  same day (S35a auto-merged cleanly -- first real auto-merge all
  session; S35b needed one retry after a transient `git push` failure,
  likely resource contention with concurrent hand-git-work in the same
  repo, not a real bug). The multi-line hover chart itself
  (`tray/lib/trading-panel.js`, canvas, no charting library, extends the
  existing single-line equity-curve pattern from `renderStrategyCard`) is
  hand-built -- guardrail path, same division of labor as S34's Start/Stop
  control. **Same-day follow-up, also user-requested:** a second "By
  Stock" chart grouping the same fills by symbol instead of strategy
  (which stocks are actually being traded) -- reuses the by-strategy
  chart's draw/hover functions unchanged, just needs the flat per-fill
  data S35c/d add (`positions[]` is pre-aggregated per strategy server-
  side, with no symbol dimension to group by client-side). Also hand-
  fixed the same day: the hover interaction was hit-testing raw
  equity_curve vertices with a fixed pixel radius, which flickered
  between points instead of tracking continuously along a line, and drew
  no visual anchor at all -- reworked to interpolate each line's y at the
  cursor's x, added a highlight dot + guide line, and added $/trade-#
  axis labels the chart previously had none of. See #911/#912/#914/#915
  for full acceptance criteria.

  **All 4 backend slices auto-merged cleanly** (S35a/#913, S35c/#917,
  S35d/#918 -- first real string of clean auto-merges all session; S35b
  needed one retry after a transient git-push race with concurrent
  hand-git-work, not a real bug). Each auto-merge restarted Felix itself
  (the SD-2/SD-3 self_dev_load flow) -- explains the dropped WS
  connections on the firing scripts each time, expected behavior, not an
  error. Live-verified end to end post-restart: `total_pnl: -28.55`,
  `paper_control: {enabled: true, starting_capital: 10000.0}`,
  `all_fills` carrying 432 real fills with symbol/pnl/timestamp, all on
  one real `trading_update` broadcast. Full suite green (5349 passed, 7
  skipped, 1 unrelated flaky test in `test_sandboxed_eval.py` that
  passed in isolation -- not touched by anything in this campaign).

- [x] S36 -- #920 -- broker.py only: `StubBrokerClient.place_order`
  hardcodes a 0.1% simulated commission on every fill -- Alpaca (the real
  broker this campaign targets) is commission-free for US stock trades,
  found live 2026-08-28 ("get rid of fees, i should be trading on
  commissionless platform"). One-line change: `fees=0.0` instead of the
  computed 0.1%. `realized_pnl` already subtracts `fees` from gross, so
  this alone makes paper P&L exactly gross with no code change needed
  elsewhere. **Landed same day (PR #921)** -- first attempt hit a
  transient bonsai 502 (retried per the standing bonsai-outage policy,
  succeeded on retry #2). The one-line fix itself was correct, but its
  own sandbox test run surfaced 3 real pre-existing tests that had
  hardcoded the OLD 0.1%-fee math into their expected P&L values
  (`test_tick_closes_and_records_a_real_realized_pnl`,
  `test_tick_closes_a_short_at_a_profit_when_price_falls`,
  `test_end_to_end_scheduled_strategy_buys_then_sells_with_real_pnl`) --
  fixed by hand to assert exact gross P&L. Full suite green (5345
  passed, 7 skipped) before merge. See #920 for full acceptance criteria.

- [x] S37a -- #922 -- forward_record.py only: `ForwardRecord.reset_paper()`.
  **Revised same day:** archives current paper fills into a new
  `paper_archives` table as one self-contained historical block (JSON
  blob + summary columns), THEN clears the live table -- does not
  destroy history. Also adds `list_paper_archives()` (summary, newest
  first) and `get_paper_archive_fills(archive_id)` (one block's real
  fills, for the Overview tab's collapsible history section). **Landed
  PR #927** after a real detour: the FIRST attempt (before the design
  revision) and even the retry against the revised spec both hit a
  self_dev_campaign "resume" gotcha this campaign has hit before (see
  the 2026-08-22 entry above) -- retrying a slice that already has ANY
  recorded ledger step (even a failed one) replays the cached edit
  instead of regenerating it, so my revised issue text was silently
  ignored until the stale `chain_steps` rows for
  `run_id='campaign-trading-s37a'` were purged by hand from
  `cerebral/data/openmind.db` (plus the matching stale sandbox clone
  dir) -- same remedy as the 2026-08-25 ledger-pollution incident.
  Once genuinely fresh, the real generated code was correct but its own
  new tests called the file's pre-existing `_add_fill_on_day` test
  helper with a `phase=` kwarg it didn't support (hardcoded 'paper')
  -- fixed by hand (optional `phase="paper"` param, existing callers
  unaffected). Sandbox also reported a 600s test-run timeout on both
  attempts; did not reproduce locally (5348 passed, 7 skipped, 1
  unrelated pre-existing flaky test) -- treated as sandbox-environment
  flakiness, not a real hang.
- [x] S37b -- #923 -- broker.py only: `StubBrokerClient.reset()` -- clears
  positions/orders, resets account back to configured starting capital.
  **Landed PR #928** -- its own new test asserted cash decreases after a
  buy, which `place_order` doesn't actually do (a separate, real gap:
  `place_order` updates positions but never touches
  `cash`/`equity`/`buying_power` at all, filed as #929). Fixed the test
  to assert what `reset()` actually guarantees instead. Full suite green
  (5350 passed, 7 skipped) before merge.
- [x] S37c -- #924 -- scheduler.py only: `reset_paper_trading` tool +
  `_reset_paper_fn` seam (mirrors `_lifecycle`/`_on_trading_change`'s
  existing post-construction-binding pattern). **Landed PR #930** after
  one hand-fix: the generated code and its own new tests (added to
  `test_plugin_scheduler.py` as scoped) were both correct, but a
  separate, pre-existing EXHAUSTIVE tool-list snapshot test in a
  different file --
  `test_plugins_time_notes.py::TestSchedulerPlugin::test_list_tools_exposes_all_scheduler_tools`
  -- asserts the scheduler's full tool-name set and wasn't in the
  issue's scope, so it broke on the new `reset_paper_trading` tool.
  Added the new name to that snapshot (same pattern as the existing
  `# S34 (#901/#906)` entries in it). Full suite green (5349 passed, 7
  skipped, 1 unrelated pre-existing flaky test in
  `test_sandboxed_eval.py`) before merge.
- [ ] S37d -- #925 -- main.py only: binds the seam -- calls
  `ForwardRecord.reset_paper()` + `StubBrokerClient.reset()` + a fresh
  `_trading_broadcast()`. **Revised same day:** also adds a
  `paper_archives` summary key to the broadcast payload for the
  collapsible history section.

  User-requested 2026-08-28, then refined same day: "i should be able to
  reset the trading paper charts" -> then "each reset should be stored
  as its own block of information, probably in overview collapsable" --
  a reset must not destroy history, it archives it. The Overview/Trade
  Log charts are pure views over broadcast data, so a real reset has to
  clear the LIVE fill history AND the simulated broker's live
  position/cash state (leaving either one stale would make the numbers
  inconsistent), while preserving what was cleared as a queryable
  archive. Split single-file (S34's proven pattern) since this spans 4
  files. Land in order: S37a, S37b, S37c, S37d (each later one depends
  on the file(s) before it existing). A Reset button + collapsible
  per-archive history blocks on the Overview tab are hand-built once all
  4 land -- guardrail path. See #922/#923/#924/#925 for full acceptance
  criteria.

Per-slice model: sonnet unless the queue entry says otherwise. This checklist is
what `self_dev_campaign` parses to tick/advance -- the "Phased slices" section
below is the detailed human-readable reference for the same 9 slices; S7/S8 are
post-hoc follow-ups not in that original list. S8 reuses issue #848 rather than
a new issue number -- its body was updated in place to the post-S7 scope.
S11b/S11c similarly reuse issue #852 across two slice labels.

## Landed PRs

- PR #840 -- S1a (auto-merged by self_dev_campaign; landed with failing tests
  because the sandbox never had yfinance installed -- patched by hand 2026-08-22,
  see pyproject.toml + cerebral/trading_data.py + its test)
- PR #841 -- S1b+S2 combined (auto-merged by self_dev_campaign; added
  cerebral/trading/cost_model.py + gauntlet.py with oos_test()/walk_forward()
  gates. S1b's own deliverable -- a backtest.run() engine matching
  `def strategy(data) -> signals`, per issue #832 -- was never built as its
  own thing; gauntlet.py invented a different strategy_fn interface instead
  (returns (gross_returns, trades) directly). Queue entry left ticked since
  redoing S1b now would just produce a second, conflicting engine -- but
  decision #5 in the table above no longer matches what's implemented.
  Worth a real decision before S4 builds strategy generation against
  whichever interface is meant to be canonical.)
- PR #842 -- S3 -- never cleanly auto-merged (branch was based on a local
  commit that got superseded before the merge; also independently
  reinvented cerebral/trading/gauntlet.py from scratch, colliding with
  PR #841's version). Resolved by hand 2026-08-22: merged run_gauntlet() +
  StrategyCard into the same gauntlet.py as GauntletGateResult (renamed to
  avoid the class-name collision with S2's GateResult), then fixed 6 real
  bugs the merge exposed -- a numpy.bool_ identity bug, a Monte Carlo gate
  that was statistically meaningless as originally written (permuting a
  fixed set never changes its mean), a pandas indexing bug, an int-cast
  bug, a test fixture that ignored which parameter was perturbed, and a
  wrong expected value in a compound-return test. All 29 trading tests
  pass. See commit 5a446f9 for the full account. PR #842 itself was never
  merged on GitHub -- its content now exists directly on local master via
  this fix instead.
- No PR -- 2026-08-22, a fresh self_dev_campaign call for S4 got blocked
  citing PR #840 (S1a's PR, already merged and hand-fixed hours earlier).
  Root cause: run_id was `campaign-{slug}-s{n}` where n is the loop
  position WITHIN one campaign() call, not a slice identity -- every fresh
  invocation's first-processed slice reused run_id "campaign-trading-s1",
  colliding with S1a's original (persistent, SQLite-backed) ledger entry.
  _run()'s resume logic replayed that old, already-obsolete failure
  instead of doing real work on S4. Fixed in commit (see plugins/self_dev.py
  `_campaign`): run_id now derives from the active slice's label
  (`campaign-trading-s1a`, `-s4`, etc.), stable and unique per slice
  regardless of invocation. The three polluted ledger rows
  (campaign-trading-s1/-s2/-s3) were purged from cerebral/data/openmind.db
  by hand since S1a-S3's real work is already committed to git and there
  was nothing legitimate left to resume from them.
- PR #843 -- S4 -- real fresh work (confirms the run_id fix above worked:
  its branch descended from current master, not a stale point). Added
  cerebral/trading_ideas.py (extract_from_url/from_prose/from_book_claim/
  to_strategy/compile_strategy) + tests. Tests failed on one trivial bug:
  a stub LLM in the test always returns a hardcoded `[1, 2, 3]` regardless
  of input, but the assertion checked against the input instead of what
  the stub actually returns -- fixed by hand, all 6 tests pass.
  SECURITY (found during hand-verification, not by the auto-merge gate):
  `compile_strategy` called `exec(code_str, {"__builtins__": __builtins__},
  ...)` -- unrestricted code execution with full builtins, on code
  ultimately derived from scraped web content. Hardened with two layers,
  neither a substitute for real sandboxing: (1) runs the same
  forbidden-pattern scan builder.py already uses for LLM-generated plugin
  code (cerebral.security.scan_source -- blocks os/subprocess/exec/eval/
  pickle/file-write), (2) exec() itself only gets a minimal safe-builtins
  allowlist (no open/__import__/exec/eval/input), which independently
  caught an obfuscated `__import__` the regex scan alone missed. Neither
  layer is a real sandbox -- route this through the ADR-0010 sandbox
  self_dev already uses for untrusted code before S5+ runs strategy code
  against a live broker connection.
- PR #844 -- S5a -- real fresh work (based on current master post-S4, same
  as #843). Added cerebral/trading/broker.py (BrokerClient Protocol,
  AlpacaBrokerClient, StubBrokerClient) + tests. One real test failure:
  `StubBrokerClient.cancel_order` did `Order(**self._orders[order_id],
  status="CANCELED")` -- tried to ** an Order dataclass instance instead
  of a dict, always raised TypeError. Order is a plain mutable dataclass;
  fixed by mutating `.status` in place instead of reconstructing.
  SECURITY/CORRECTNESS (found during hand-verification, untested by the
  gate since no test exercised it): `AlpacaBrokerClient.place_order`
  hardcoded `limit_price="0.01"` for every limit order -- the
  `BrokerClient` Protocol had no way to pass a real one in the first
  place. Any live limit order would have silently submitted at one cent.
  Added `limit_price: Optional[float] = None` to the Protocol and both
  implementations; both now raise ValueError if a limit order arrives
  with no price. New test covers the missing-price rejection.
- PR #845 -- S5b -- real fresh work (based on current master post-S5a).
  Added cerebral/trading/{risk_limits,failure_handling,alerts}.py + tests.
  Two real bugs, both in the first two files, both blocking every actual
  use of the class (not edge cases):
  (1) `RiskManager.__init__`'s only settings parameter was `settings_store`
  (expected to be a store object with `.get()`), so tests constructing a
  manager with an explicit `RiskConfig(max_per_trade_risk_pct=2.0)` passed
  it positionally into that slot -- `settings_store.get(...)` then failed
  because a RiskConfig isn't a mapping. Added a distinct `config:
  Optional[RiskConfig]` parameter that wins over settings_store lookup.
  (2) `FailureHandler._last_data_timestamp` starts at None (no data has
  arrived yet on construction), but `update_market_data_timestamp` did
  `if ts > self._last_data_timestamp` before checking for None -- crashed
  on the very first tick, i.e. every real use. Guarded the comparison.
  One test itself was also wrong: asserted the literal string
  "per_trade_risk" (the machine-readable blocked_by slug) appeared in a
  log line that actually reads "Per-trade risk 300.00 exceeds..." (the
  human-readable reason text) -- fixed the assertion to match what's
  really logged. alerts.py had no issues. 11/11 new tests pass, 55/55
  across the trading suite.
- PR #846 -- S5c -- real fresh work (based on current master post-S5b).
  Added cerebral/trading/forward_record.py, extended gauntlet.py and
  scheduler.py, plus a Trading panel render function. One real bug
  blocking every real use: the test's `isolated_db` fixture monkeypatched
  `_DB_PATH` with `str(tmp_path / ...)`, but forward_record.py calls
  `_DB_PATH.parent.mkdir(...)` -- a pathlib-only method, not str-compatible
  (unlike trading_data.py's os.path-based `_CACHE_DIR`, which does accept
  a plain string -- the two modules use different path conventions).
  Fixed by keeping the fixture's override as an actual Path.

  Also found reading gauntlet.py's new auto-promote block before landing
  it (untested by any test -- no test passed scheduler=/paper_broker= at
  all until the new ones added here): it wrote directly to
  `scheduler._con` with raw SQL, reaching into the plugin's private
  connection from cerebral/ when scheduler.py's own diff in this same PR
  already added a proper public method (`_run_paper_strategy`) for this
  -- "seam rule respected" is an explicit S5c acceptance criterion this
  violated. Routed through the public method instead. Worse: it seeded
  the forward record with a fabricated `("INIT", pnl=0)` fill on every
  gauntlet pass -- a fake trade that would have silently counted toward
  the 30-trade honesty-rule minimum without being a real one, undermining
  exactly what S5c exists to protect. Removed entirely; ForwardRecord's
  own constructor already creates its table, no seed was ever needed.
  Also removed `card.forward_record = forward_record` and the
  `paper_broker.forward_record = ...` assignment -- both set attributes
  neither StrategyCard nor BrokerClient declare or read; pure dead code.
  Added 3 new tests covering the fixed auto-promote path (all 3 needed
  removing -- no test exercised it before). 21/21 gauntlet+forward_record
  tests pass, 63/63 across the trading suite.

  **Real gap, not fixed here (needs its own slice):** the "auto-promote"
  feature only *schedules* a recurring paper-trading event via
  scheduler's run_paper_strategy -- nothing yet *consumes* that scheduled
  event and actually calls `paper_broker.place_order(...)` to place real
  paper trades and record real fills. S5c's own acceptance criterion #1
  ("Gauntlet pass triggers automatic paper trading -- no human step") is
  only partially met: scheduling happens, trading doesn't yet.
  `run_gauntlet`'s `paper_broker` parameter is accepted but genuinely
  unused (marked with a `# ponytail:` comment explaining why) until that
  consumer exists. Separately, the forward record has no per-strategy
  identifier in its schema (`forward_fills` has no strategy/hypothesis
  column) -- once more than one strategy is ever promoted to paper
  trading simultaneously, their fills would commingle in one table with
  no way to separate them, corrupting each strategy's own expectancy/CI.
  And the Trading panel's `renderForwardRecord(record, ...)` expects
  `record.ci.mean/lower/upper/is_sufficient`, `record.trade_count`,
  `record.fills`, `record.equity_curve` as plain properties, but
  ForwardRecord's actual Python API only exposes methods with different
  names and shapes (`compute_expectancy_ci()`, `get_fills()`,
  `get_equity_curve()`) -- a serialization layer between the two doesn't
  exist yet either. None of this blocks S5c's own tests (nothing wires
  any of it together yet), but S6 cannot autonomously execute live
  trades on top of "scheduling that never runs" -- resolve all three
  before S6 lands.
- PR #847 -- S6 -- real fresh work (based on current master post-S5c).
  Added cerebral/trading/lifecycle.py (StrategyLifecycle: paper->live
  graduation on rolling-CI, position-size ramp 25%/50%/100%, drawdown +
  rolling-CI retirement, alert emission) and extended forward_record.py
  (phase column distinguishing paper/live fills, add_live_fill,
  compute_live_expectancy_ci) and risk_limits.py (check_correlation_limit,
  the multi-strategy correlation block docs/trading-live-verify.md
  describes). 5/5 new lifecycle tests + the existing trading suite passed
  in the sandbox, but the campaign's merge gate reported `tests_failed`
  anyway -- caused by pre-existing, unrelated breakage in the shared
  cerebral/tests/ suite it runs as its gate (books-campaign test bugs +
  two same-day stale-test issues in the self-dev/chain-engine suites; all
  fixed by hand in separate commits so the global gate is green again).

  One real bug found while hand-verifying lifecycle.py before landing it
  (untested -- test_trading_lifecycle.py only covered the drawdown-breach
  retirement path, never the rolling-CI path): `check_retirement`'s
  rolling-CI check computed each "recent P&L" as
  `live_equity_curve[-1] - live_equity_curve[i - 1]` -- always relative to
  the *current* running total, not the consecutive difference between
  trades. That collapses the true per-trade variance and can produce a
  false negative: a strategy quietly losing money (true last-30 mean
  +0.17, correct lower-CI bound -0.16, should retire) computed a buggy
  mean of +2.39 and lower-CI bound of +1.66, staying live instead of
  halting -- the exact "genuinely broken code looks fine to the gate"
  failure mode this whole campaign's hand-verification step exists to
  catch, just in the money-safety code path this time instead of a
  test file. Fixed with `np.diff(equity_curve, prepend=0.0)` (correct
  per-trade P&L in one line) and added a regression test that fails
  against the old formula and passes against the fix.

  **S6 does NOT resolve the three gaps S5c's note said must be resolved
  first, and the campaign's stated goal ("autonomous live execution +
  retirement + alerting") is not actually achieved end-to-end:**
  1. **No paper-trade consumer, and worse than documented.** S5c's note
     said gauntlet.py's auto-promote *schedules* via
     `scheduler._run_paper_strategy(...)` but nothing *consumes* the
     scheduled event. Checked plugins/scheduler.py directly (its full git
     history, not just the working tree): `_run_paper_strategy` has never
     existed there. The real `SchedulerPlugin` only has generic calendar-
     event CRUD (`_create_event`/`_list_events`/`_update_event`/
     `_delete_event`) -- there is no execution loop of any kind. The
     method exists only as a local `FakeScheduler` class inside
     test_trading_gauntlet.py. In production `run_gauntlet`'s
     `scheduler` parameter defaults to `None`, so
     `if auto_promote and verdict == "VALIDATED" and scheduler:` is
     always false and auto-promotion has never fired for a single real
     gauntlet pass. "Scheduling happens, trading doesn't yet" (S5c's
     framing) overstates it -- neither has ever actually run.
  2. **Still no per-strategy forward-record scoping.** S6 added a `phase`
     column (paper vs. live) to `forward_fills`, a different axis than
     the one S5c flagged. There is still no strategy/hypothesis
     identifier column. `StrategyLifecycle.check_graduation(name,
     record)` takes a `ForwardRecord` per call, implying the caller is
     responsible for passing a strategy-scoped record, but no such
     scoping exists anywhere -- if more than one strategy is ever
     promoted to paper trading, their fills still commingle in one
     table with no way to separate them.
  3. **No panel wiring at all (broader than the "shape mismatch" S5c
     described).** Searched tray/ for `renderForwardRecord` and any
     reference to `ForwardRecord`: zero matches. The function S5c's note
     described a shape mismatch against doesn't currently exist in the
     tray codebase, so it isn't an active bug -- but it also means there
     is no Trading Panel UI displaying lifecycle status, live/paper
     fills, or alerts. `StrategyLifecycle.get_open_positions` and
     `get_alert_history` are stubs with no consumer either.

  Net: S6 delivers real, tested, now-bug-fixed graduation/ramp/retirement/
  correlation *logic*, safe to build on. It does not deliver autonomous
  live execution -- nothing in the current codebase can place a real
  paper or live trade without a human manually calling the broker client.
  A follow-up slice needs to: (a) give the scheduler plugin (or some
  other real event loop) an actual paper-trading consumer that calls
  `broker.place_order(...)`, (b) add a strategy identifier column to
  forward_fills and thread it through ForwardRecord/StrategyLifecycle,
  and (c) build the Trading Panel UI this campaign has deferred
  incrementally since decision #17 in the grill record. Not filed as a
  GitHub issue by this pass -- that needs the user's go-ahead first.
- PR #849 -- S7 -- opened by self_dev_campaign, closed unmerged in favor
  of a hand-verified merge (commit 7c104cb on top of e7d4917). Two
  separate obstacles, neither in the PR's own 135-line diff
  (cerebral/trading/forward_record.py, lifecycle.py, plugins/scheduler.py,
  tray/lib/trading-panel.js):

  First, self_dev's sandboxed test run reported tests_failed against a
  bug unrelated to this PR: tests/test_step_ledger.py (repo-root, not
  cerebral/tests/) had the same stale-mock issue commit 2a0f455 already
  fixed in cerebral/tests/test_spill_store.py -- a local _ScriptedPlanner
  test double missing the all_tools kwarg ChainEngine.run() gained from
  the tool-awareness campaign. Missed because my own earlier "cerebral/
  tests/ is green" checks never covered the repo-root tests/ dir that
  self_dev_io.py's test_fn also runs as part of the merge gate. Fixed on
  master directly (commit 88fd9da) since it was pre-existing and
  unrelated to S7.

  Second (the important one): self_dev_campaign's run_id is deterministic
  per slice label (`campaign-trading-s7`), and its step-ledger resume
  logic replays already-recorded phases instead of re-running them --
  once the "test" phase recorded a tests_failed result, every retrigger
  replayed that exact same stale result byte-for-byte (confirmed: two
  consecutive retriggers after the unrelated fix landed produced
  identical truncated pytest output), regardless of what changed on
  master. self_dev_campaign doesn't expose a `restart` flag to clear
  this (only the single-slice `self_dev` tool does), and forcing one
  would have re-run the stochastic edit step, producing a different diff
  than the one that needed reviewing anyway. Fetched PR #849's branch
  into an isolated git worktree instead, merged master in by hand
  (matching the PR #842/S3 precedent of resolving a stuck slice outside
  the automated loop), and hand-verified the actual diff against issue
  #848's three gaps -- the S6 postmortem's lesson that passing tests
  never means the substance is right.

  That hand-review found the substance mixed: gap 2 (per-strategy
  scoping) is done correctly and completely -- a real `strategy_id`
  column, threaded through every ForwardRecord read/write method, and
  `StrategyLifecycle.check_graduation` actually passes `strategy_id=name`
  through, not just accepted-and-ignored. Gap 3 (UI) got a real,
  reasonable `renderLiveStrategyCard` component in tray/lib/trading-
  panel.js -- but zero callers anywhere, not referenced in main.html, no
  data path from the backend; a user would never see it (note: the
  pre-existing `renderStrategyCard` in the same file has the same
  problem, not new to this slice).

  Gap 1 (paper-trade consumer) was the worst of the three: `plugins/
  scheduler.py` now has a real `_run_paper_strategy(strategy_name,
  broker, forward_record, config=None)` method, but it was unreachable
  by construction -- 4 real bugs, all on the only path that could ever
  call it, none caught by the PR's own tests because nothing in the PR
  exercised that path:
  1. gauntlet.py's only call site still passed one positional dict
     (`{"strategy_name":..., "interval": "5m"}`, unchanged from S5c/S6)
     against a method now requiring 4 params -- guaranteed TypeError,
     uncaught (outside the method's own try/except).
  2. Inside the method, `broker.place_order(..., price=0.0,
     order_type="market")` used kwargs that don't exist on the real
     `BrokerClient.place_order(symbol, qty, side, type,
     limit_price=None)` -- would also have raised TypeError.
  3. `config=None` (the default, and how gauntlet.py actually calls it --
     no config dict at all) but `config.get(...)` was called before the
     try block -- crashes on exactly the real call shape.
  4. `order.price` / `order.fees` -- `Order` (cerebral/trading/broker.py)
     has never had either field, in AlpacaBrokerClient or
     StubBrokerClient, going back to S5a (#844). Not a typo to silently
     patch: neither broker implementation has ever computed a real fill
     price, so there's nothing genuine to read.

  Fixed 1-3 directly (gauntlet.py now calls the method correctly with
  its own already-accepted-but-unused `paper_broker` param and a real
  `ForwardRecord()`; the ponytail comment on `paper_broker` claiming it
  was unused is now stale and was corrected). Fixed 4 by recording an
  explicit `price=0.0`/`fees=0.0` placeholder with a `# ponytail:`
  comment rather than fabricating a number -- adding a real price field
  to Order and populating it (Alpaca's `filled_avg_price` for the live
  client; StubBrokerClient has no pricing concept at all today) is a
  separate, legitimate piece of work, now issue #848's gap 2.

  Even fixed, gap 1 is still only partially closed: `_run_paper_strategy`
  now runs correctly, but exactly once, at the moment a gauntlet run
  passes -- nothing re-invokes it on the `"interval": "5m"` it's given.
  A promoted strategy gets one paper trade and can never accumulate the
  30 trades check_graduation needs from this alone. Documented as a
  ponytail comment at the gauntlet.py call site; a real recurring
  dispatcher is issue #848's gap 1, now the top priority.

  New cerebral/tests/test_plugin_scheduler.py covers the fixed method
  directly (3 tests: execute-and-record, no-config-doesn't-crash,
  no-broker-skips) -- all 3 would have caught bugs 1-3 above had they
  existed before this PR shipped untested. Updated test_trading_gauntlet.py's
  FakeScheduler fixtures to the real signature, added a paper_broker=None
  no-op case. Full suite green: 5038 passed, 7 skipped, 0 failed
  (cerebral/tests/ + repo-root tests/ -- the actual scope self_dev_io.py's
  test_fn runs as the merge gate).

  Issue #848 updated in place (not closed, not replaced) with the
  post-S7 remaining scope: a real recurring dispatcher, Order's missing
  price field, and wiring the UI into main.html. Do not risk live
  capital until at least the dispatcher exists -- without it,
  "autonomous execution" doesn't exist regardless of how correct the
  one-shot path now is.

- PR #850 -- S8 -- auto-merged by self_dev_campaign, and the first slice
  this campaign where that auto-merge genuinely reached GitHub's
  origin/master (every slice since S3 had been hand-verified and landed
  on local master instead, PR left open as a paper trail -- see S3/#842).
  That surfaced a real, previously-invisible infra problem, initially
  misdiagnosed as "S8 is an unreviewed from-scratch reimplementation" --
  worth recording both the wrong first read and the real finding, since
  the wrong read nearly threw away good work.

  clone_fn (cerebral/self_dev_io.py) clones from this local checkout, not
  GitHub -- confirmed the sandbox's own commit (030ccb3, in
  cerebral/data/sandbox/self_dev/campaign-trading-s8) has local master's
  real tip (201284d) as its direct parent, so the edit step worked
  correctly against the full S3-S7 history. But `gh pr merge` still
  squash-merges against GitHub's own copy of the base branch, and GitHub's
  `master` had been stuck at PR #841 since S3 (nothing since had ever been
  pushed there). Squash-merging a branch whose real diff was 21 lines
  against a base 25 commits stale produced a single ~3000-line commit
  (14e4ac5) that looked exactly like an independent from-scratch
  reimplementation of the entire trading module in a `gh pr view`/`git
  diff` against origin/master -- because GitHub had no reference point
  closer than #841 to diff against. First read of that misleading diff
  concluded S8 hadn't touched gaps 1/2/3 in any real way and nearly got
  discarded via a full-history force-push with no cherry-picking. Caught
  before acting: `git log` on the sandbox's own local clone directory
  showed its actual commit's parent was 201284d (local master's real
  tip), not #841 -- diffing 201284d..030ccb3 directly (bypassing GitHub's
  stale-base view entirely) gave the true, small, legitimate slice diff.

  The real diff (2 files, 21 lines): `Order` (cerebral/trading/broker.py)
  gains `price`/`fees` fields; `StubBrokerClient.place_order` now computes
  a real simulated price (`100.0 + hash(symbol)-derived offset`, a
  deterministic per-symbol pseudo-quote) and a simulated 0.1% fee instead
  of having no pricing concept at all; `AlpacaBrokerClient.get_order`
  extracts Alpaca's real `fill_avg_price` (place_order's own return still
  can't -- Alpaca doesn't confirm a fill price until a status check,
  which nothing calls yet); `plugins/scheduler.py`'s `_run_paper_strategy`
  now records the order's real price/fees instead of the explicit 0.0
  placeholder S7 had left. Cherry-picked directly onto local master
  (commit c7dd1a4) rather than kept as part of the misleading squash
  commit. Full suite still green after: 5038 passed, 7 skipped, 0 failed.

  Gap 2 (Order fill price) is now substantially real for paper trading
  (the stub broker tests actually exercise) and partially real for live
  (Alpaca's confirmed fill price is extractable, just not wired to a
  post-place_order status check yet). Gaps 1 (recurring dispatcher) and 3
  (UI wiring into main.html) are untouched -- confirmed directly in the
  true diff, not assumed.

  Reconciliation: force-pushed local master over origin/master with the
  user's explicit go-ahead (asked first given the scale -- rewriting
  already-merged public history). Local master, now including S8's real
  fix as its own clean commit, is origin/master. PRs #843-847 remain open
  on GitHub as they've been all campaign; #850's squash commit (14e4ac5)
  is no longer part of origin/master's history.

  Structural fix still needed, separate from any one slice: `gh pr merge`
  squash-merging against GitHub's stale base is what actually produces
  the misleading diffs and the eventual force-push need -- either push
  local master to origin after every hand-verified landing (closing the
  gap self_dev's own merges widen every time), or stop trusting
  `gh pr view`/`git diff <base>..<head>` against a GitHub PR without first
  confirming the PR's base is actually current. The second habit is now
  established (this entry exists because of it); the first isn't.

- PR #851 -- S9 -- true diff verified via the sandbox's own local clone
  (cerebral/data/sandbox/self_dev/campaign-trading-s9), per the S8 lesson:
  e545998..3f8a646, 3 files, 98 lines. The campaign's own test run
  reported `tests_failed` with a real, specific error --
  `NameError: name '_scheduler_loop' is not defined` at
  cerebral/main.py:248, `asyncio.create_task(_scheduler_loop())` called at
  module import time, ~3000 lines before that function's own def. That
  diagnosis was right but shallow -- hand-reviewing the rest of the real
  diff (not just what broke collection) found three more bugs and one
  structural gap, all on the path this whole slice exists for:

  1. **The NameError itself.** Fixed by moving the task creation into
     main()'s existing startup block, alongside `heartbeat`/`rss_task` --
     the established pattern in this file for background loops (they
     start when the event loop is actually running, not at import time),
     confirmed by reading how `_worker_heartbeat_loop` gets started
     elsewhere in the same file.
  2. **Broken idempotency.** `list_due_events()` compared `last_run_iso`
     (set to "now" when a trade executes) against `start_iso` (the
     event's fixed original schedule time) -- two different quantities
     that are essentially never equal, so the "already ran" check was
     never true even right after running. A dispatched strategy would
     have refired on every single 5-minute tick forever instead of
     respecting its own recurrence interval. Rewrote with real interval
     math (`_recurrence_interval`, supporting daily/weekly/monthly plus a
     new `Nm`/`Nh` short-interval pattern trading needs) and added
     `mark_event_run(event_id)` as its own method -- the dispatcher (which
     already holds the event row) calls it after dispatch, instead of
     `_run_paper_strategy` reaching back into the events table by a fuzzy
     title match, mixing trade-execution and event-bookkeeping concerns.
  3. **`_VALID_RECURRENCES` had no short-interval support at all** --
     only `daily`/`weekly`/`monthly`, so even a correctly-timed
     `_create_event(..., recurrence="5m")` call would have been rejected
     outright. Added the `Nm`/`Nh` pattern alongside the calendar literals
     (a distinct axis -- a duration, not a named cadence -- so folded into
     a separate regex rather than the existing literal set).
  4. **The structural gap, worse than the other three:** nothing in the
     entire codebase ever called `scheduler._create_event()` for a
     trading strategy. gauntlet.py's auto-promote block (S7/S8) called
     `_run_paper_strategy` directly, once, at the moment a gauntlet
     passed -- completely bypassing the event system this slice's
     dispatcher polls. Even with bugs 1-3 fixed, `list_due_events()`
     would have returned `[]` forever, because nothing populated the
     table it reads. Fixed by changing gauntlet.py's auto-promote call
     from executing a trade itself to registering a recurring event
     (`recurrence="5m"`) -- the dispatcher is now the sole execution
     path, `ForwardRecord`/broker are no longer threaded through
     `run_gauntlet`'s call site at all (the dispatcher supplies its own
     shared instances). `paper_broker` still gates whether auto-promotion
     happens, it just no longer flows into the scheduler call itself.
     Updated test_trading_gauntlet.py's `FakeScheduler` fixtures from
     asserting a `_run_paper_strategy` call to asserting a
     `_create_event` call with the right title/recurrence.

  Also fixed, lower stakes: tray/lib/trading-panel.js used `export
  function` (ES module syntax, which a plain `<script src>` tag can't
  load at all) and its `initTradingPanel()` called `window.sendEvent` /
  listened via `window.addEventListener('message', ...)` -- neither
  exists anywhere in this app. The real transport is ws-bridge.js's
  `onMessage` callback, routed through main.html's own `handleEvent`
  switch on `event.type`, exactly like every other panel (confirmed by
  reading ws-bridge.js directly, not assumed). Converted to the same UMD
  wrapper thinking-panel.js already uses; split into `initTradingPanel()`
  (mount + loading placeholder) and `renderTradingUpdate()` (the real
  render, called from a new `'trading_update'` case in `handleEvent`).
  Wired a "Trading" `lib-tab`/`lib-sub` into main.html's existing Library
  pane, matching the memory/insights/.../github pattern exactly (a real
  nav tab, not a hidden or orphaned mount point) -- confirmed live by
  running the project's own render-smoke.test.js against the edited
  main.html in an isolated worktree, which caught a real regression (a
  hardcoded `expect(tabBtns.length).toBe(7)` from before this slice added
  an 8th tab) and required 2 new assertions for the new wiring, both
  added. Also added a broadcast call after each dispatch cycle so the
  panel updates live on real trades, not only when the tab is opened.

  Verification went beyond "tests pass": added
  `test_end_to_end_due_event_dispatches_a_real_paper_trade`, which
  schedules a strategy, runs the exact dispatch logic `_scheduler_loop`
  uses, and asserts a real fill with a real (S8) simulated price and the
  correct `strategy_id` lands in `forward_fills` -- then that the event
  is correctly NOT due again immediately (idempotency), with a companion
  test confirming it IS due again once its recurrence interval has
  actually elapsed. That's the full chain this multi-slice effort (S6
  through S9) exists to build, exercised directly, not inferred from
  passing unit tests on isolated pieces.

  `cerebral/main.py` and `tray/windows/main.html` both had another
  concurrent session's uncommitted local changes in the live working
  tree at merge time -- this slice was built and tested entirely in an
  isolated git worktree against master's last committed state, then
  merged via `git stash` (scoped to just those two files plus BOOKS.md)
  / fast-forward / `git stash pop`, auto-merging cleanly. Verified after:
  both sets of changes coexist correctly (main.py still compiles, the
  other session's own additions are untouched), full suite green (5045
  passed, 7 skipped, 0 failed) and the tray JS suite green (28 suites,
  746 tests) with both sessions' panels present together.

  Full suite in isolation before merging: 5039 passed / 7 skipped / 0
  failed (cerebral/tests/ + repo-root tests/); tray JS in isolation: 27
  suites / 737 tests (one suite fewer than the main tree only because
  campaign-panel.test.js is another session's untracked file, absent
  from the worktree's git history -- not a regression).

  **Net: all three gaps issue #848 tracked are genuinely closed** --
  verified by tracing the real call chain by hand, the same discipline
  every slice in this campaign since S6 has needed to actually mean
  something. Issue #848 closed. Live/real-money trading remains
  unwired (no live Alpaca credentials configured) -- an accepted,
  explicit scope boundary, not a remaining gap of this issue.

- S10 -- no PR yet (branch `worktree-agent-a9b641d8731a59557`, left for
  review, not merged). S9 left the dispatcher *running* but *disconnected
  from any strategy*: `_run_paper_strategy` placed a hardcoded `buy 1
  "SYMBOL"` -- a literal placeholder ticker, always buy, no sell path
  anywhere, no strategy consulted -- and every `add_fill` in the codebase
  passed `pnl=0.0`. Since `check_graduation` requires `lower > 0` on the
  P&L confidence interval and every recorded P&L was exactly 0.0, **no
  strategy could mathematically ever graduate**; the "autonomous execution"
  goal was blocked on arithmetic, not on wiring.

  **The interface decision (decision #5, finally pinned down).** Three
  incompatible shapes existed with no bridge (documented as open since the
  S1b/S2 entry above). Resolved by scoping rather than unifying:
  `run_gauntlet`'s batch `backtest_func(prices, params) -> (equity,
  metrics)` and `oos_test`/`walk_forward`'s `strategy_fn(data) ->
  (gross, trades)` keep their shapes -- validating a whole history is a
  different job from deciding one bar. The LIVE path gets decision #5's
  `def strategy(data) -> signals`, the only per-tick shape of the three,
  and the only one `compile_strategy` already builds. Contract, now
  documented at the top of `cerebral/trading/live_tick.py` and enforced by
  its tests: `data` is the DataFrame `fetch_ohlcv` returns (capitalised
  `Open/High/Low/Close/Volume`), `signals` is a sequence of **target
  positions** (1 long / 0 flat / -1 short), one per bar, and the dispatcher
  reads the LAST element as "what to hold now". Target state, not an
  action, so a missed tick, a partial fill or a Felix restart self-corrects
  on the next tick instead of double-entering.

  New: `cerebral/trading/live_tick.py` (signal evaluation, position-state
  decision, realized-P&L, `run_strategy_tick`, `dispatch_due_events`) and
  `cerebral/trading/strategy_store.py` (the symbol + strategy source a
  scheduler event's bare title can't carry -- its own tiny table, not extra
  columns on the scheduler plugin's generic `events`, since cerebral/ must
  not depend on plugins/). `gauntlet.py`'s auto-promote now registers a
  spec before scheduling; `plugins/scheduler.py::_run_paper_strategy`
  delegates to `run_strategy_tick`; `cerebral/main.py`'s `_scheduler_loop`
  is now 5 lines calling `dispatch_due_events` (the whole pass moved into
  live_tick so it's testable without importing main), with
  graduation/ramp/retirement wired in after each dispatch.

  Four real bugs found by hand while building on top of this, none caught
  by any existing test:
  1. **`_generate_stub_strategy` produced strategies that could never
     emit a signal.** It read `data.get("close", [])` -- lowercase -- and
     pandas' `.get()` returns the default for a missing column, so against
     every DataFrame `fetch_ohlcv` produces it silently got `[]`. Not a
     crash, just a strategy that permanently says nothing. Fixed to
     `data["Close"]` and to the 1/0/-1 target-position encoding (the old
     1/-1 had no way to express "hold nothing"); `to_strategy`'s LLM prompt
     now states the contract too, since leaving it vague is what let the
     drift happen.
  2. **`StubBrokerClient` recorded a hardcoded `avg_entry_price=100.0`
     while filling at `simulated_price`.** Any P&L computed from the
     broker's own position -- which is exactly what "numbers from the
     broker, never re-derived" requires -- would have been wrong by the
     whole hash-derived offset. Fixed, along with weighted-average entry
     when adding to a position and stale `current_price`/`market_value`.
  3. **A fully-closed position lingered at qty 0.0 forever**, so "am I in
     this name?" answered yes to a flat book. Dropped from the map instead
     (`find_position` treats a zero-qty row as flat regardless, since real
     brokers differ).
  4. **`filled_qty` was 0.0 on a FILLED order** -- self-contradictory, and
     every caller reading it saw nothing filled on a full fill.

  P&L is computed from the position's entry price vs. the closing order's
  own fill price x qty, minus the *closing* order's broker-reported fee
  only. The opening fee is not re-derived or estimated -- it is already
  recorded on its own fill row's `fees` column, and inventing a number for
  it is the fabrication failure this campaign has caught twice.

  Tests: 25 new in `test_trading_live_tick.py` (signal evaluation including
  garbage/empty/warm-up, position direction across both brokers' different
  side vocabularies, the open/close/hold/flip decision table, P&L long and
  short with fees, full ticks, the dispatcher, and graduation both ways),
  plus a rewritten `test_plugin_scheduler.py` and a genuine end-to-end
  `test_end_to_end_scheduled_strategy_buys_then_sells_with_real_pnl`: a
  real `SchedulerPlugin`, a real recurring event, two dispatch passes with
  a scripted price path, asserting the close's realized P&L equals
  (55 - 50) x 4 - 0.22 fees and the broker's own book is flat afterwards.
  No test touches the network (`fetch` is injected everywhere) or Alpaca.
  Full suite: 5070 passed, 7 skipped, 0 failed (`cerebral/tests/` +
  repo-root `tests/`).

  **Still not connected / still honest gaps after this:**
  1. **Nothing in production calls `run_gauntlet`.** Only tests do. So no
     spec is ever registered and no event is ever created by real code --
     the chain works end-to-end but has no real entry point yet. This is
     unchanged from before S10 and is the next real slice.
  2. **A gauntlet pass without `symbol`/`strategy_code` still schedules an
     inert event.** Preserved deliberately (existing callers/tests), but
     the dispatcher will skip it forever with "no strategy spec
     registered". Better than the placeholder trade it used to place, not
     as good as refusing to schedule what can't trade.
  3. **`compute_expectancy_ci` samples every fill, including opening fills
     recorded at pnl=0.0.** So a 30-"trade" record is really 15 round trips
     and the mean expectancy is diluted by half. Graduation is now
     mathematically reachable (verified by a test), but the honesty rule's
     "30 trades" arguably means 30 *round trips*. Fixing it needs an
     `is_close` column and a schema migration -- not done, deliberately.
  4. **`check_retirement` is wired but inert.** It returns early unless
     status is "live" with a live equity curve, and it is called with
     `worst_backtest_dd=0.0` because nothing stores the gauntlet's worst
     backtest drawdown (`StrategyCard` carries an equity curve but no
     drawdown metric). 0.0 disables the drawdown branch entirely -- honest,
     since inventing that threshold would fabricate the number that decides
     when to halt a strategy.
  5. **Graduation does not start live trading, by design.** It flips the
     lifecycle status to "live" and logs loudly; the dispatcher holds a
     `StubBrokerClient` and records `phase="paper"` fills only. No code
     path added here can reach `AlpacaBrokerClient` -- live execution waits
     on the manual arm/disarm toggle, which is not built.
  6. **`compile_strategy` still isn't real sandboxing** (SAFETY section,
     unchanged) and it now runs on every dispatch tick, not just once at
     validation -- routing it through the ADR-0010 sandbox matters more
     after this slice, not less.
  7. **`StrategyLifecycle.get_open_positions` is still the fake stub** that
     returns the strategy's own name as a `symbol`. `find_position` /
     `broker.list_positions()` is the real answer now; the panel-facing
     stub was left alone as out of scope.

- No PR -- S11 part 1 -- Alpaca live key/secret wired into Felix's
  existing Credentials window (tray/windows/main.html) via two new IPC
  handlers (set_alpaca_credentials / clear_alpaca_credentials). Turned
  out simpler than expected: cerebral/trading/broker.py's
  `_get_alpaca_credentials` already read from a dedicated
  `service="cerebral_alpaca"` keyring entry, completely bypassing
  CredentialStore (the per-profile system every other integration in
  this app uses) -- broker.py needed zero changes, since nothing was
  ever writing to the place it already reads from except a one-off
  manual script. Deliberately did NOT force Alpaca into
  `_STATIC_TOKEN_PROVIDERS`/CredentialStore: that system is per-profile
  and single-value-per-provider; a brokerage account is one per Felix
  instance, not per profile, and needs two values (key + secret).
  Setting credentials here does NOT enable live trading by itself --
  parts 2-4 of S11 (the arm toggle, a real caller of run_gauntlet,
  live/paper branching in the dispatch loop) don't exist yet, so there
  is still no code path anywhere that can place a live order. Full suite
  green: 5076 passed, 7 skipped, 0 failed; tray JS 28 suites/746 tests.

- PR #854 -- S11b -- opened by self_dev_campaign, closed unmerged (see the
  PR's own closing comment for the full account) -- landed by hand instead
  on top of the real diff (verified via the sandbox's own clone,
  `cerebral/data/sandbox/self_dev/campaign-trading-s11b`, `git diff
  2634e9a..selfdev/69f7f8e7`, 4 files / 155 lines, based correctly on
  current master). Three real bugs found hand-verifying before landing:

  1. **A dead, unused duplicate settings key.** The diff added BOTH
     `trading_arm_enabled` (never read or written anywhere else in the
     diff) and `trading_live_arm` (the one actually wired into
     `dispatch_due_events`'s `arm=` and covered by the new tests) to
     `_DEFAULTS`/`_TYPES`. The dead key is exactly what made
     `test_all_returns_all_keys` fail (the reported `tests_failed`) --
     removed it; kept `trading_live_arm` only.
  2. **`phase="live"` was never threaded through.** `dispatch_due_events`
     correctly chose between `broker` and a new `AlpacaBrokerClient(env=
     "live")` based on `arm` + graduation status, but the call into
     `scheduler._run_paper_strategy` never passed a `phase` argument at
     all -- every fill, even one placed through the live broker, would
     still have been recorded with `phase="paper"` (the default baked
     into `run_strategy_tick`). That's not a cosmetic label: it's the
     exact "every surfaced number is labelled backtest/paper/live, mixing
     them is a bug" rule this campaign's own Honesty rule exists to
     enforce. Fixed by adding `phase: str = "paper"` to `SchedulerPlugin.
     _run_paper_strategy` and threading `phase="live" if is_live else
     "paper"` from `dispatch_due_events` all the way through to
     `run_strategy_tick`'s existing (previously unused by any caller)
     `phase` parameter.
  3. **The registered "production caller" (Part 3) crashes on every
     invocation, independent of its own logic bugs.** It registered a
     `CommandRegistry` command (`trading_gauntlet_run`, phrases like "run
     gauntlet") whose handler is a `lambda data: ...` expecting a `data`
     dict -- but `cerebral/main.py`'s command dispatcher calls matched
     handlers as `await cmd.handler()`, zero arguments, always (confirmed
     by reading the call site directly). `CommandRegistry` is a
     phrase-matched, zero-argument command system by construction --
     `match()` doesn't even parse `/name arg1 arg2` into a payload, just
     the bare command name. No handler shaped this way could ever receive
     strategy code/symbol as input. Independently, the handler's own
     `run_gauntlet(...)` call passed `positions=` where the real
     parameter is `position_sizes` -- a second, unrelated crash had the
     first one somehow not applied. A second, better implementation
     (`_handle_gauntlet_run`, correctly async/`data`-dict-shaped, correct
     kwarg name, a real MA-cross-style backtest wrapper) existed in the
     same diff but was never registered or called from anywhere --
     orphaned dead code.

  Landed Parts 2 and 4 only (both real, both now covered by 4 new
  regression tests in `test_trading_live_tick.py`: disarmed+graduated
  stays paper, armed+ungraduated stays paper, armed+graduated goes live
  with `phase="live"` and a real `AlpacaBrokerClient(env="live")`
  instance -- constructing it is safe, `__init__` never connects, only
  `place_order`/`get_account`/etc. do via lazy `_connect()`, which no
  test calls -- and the pre-S11 no-`arm`-passed call shape still defaults
  to paper). Part 3 not delivered -- PR #854 closed unmerged with the
  full bug account in its closing comment; issue #852 updated in place
  (not closed) scoped down to just Part 3, continuing as S11c. Full
  suite green: 5083 passed, 7 skipped, 0 failed (`cerebral/tests/` +
  repo-root `tests/`).

  **Live trading is still not possible end-to-end after this slice** --
  Parts 2 and 4 are real and correctly gated, but Part 3 (the only way to
  actually reach `run_gauntlet` in the running app) still doesn't exist,
  so no strategy can ever graduate through real production code today
  (only through tests). Do not risk live capital.

- PR #855 -- S11c -- opened by self_dev_campaign, closed unmerged (full
  bug account in the PR's own closing comment) -- landed by hand instead
  on top of the real diff (verified via the sandbox's own clone,
  `campaign-trading-s11c`, `git diff ae3476b..selfdev/90239321`, 2 files
  / 89 lines, correctly based on S11b). Two real bugs found hand-verifying:

  1. **The registered tool called the real `run_gauntlet` with parameter
     names that don't exist on it.** `run_gauntlet(code=code, symbol=
     symbol, hypothesis=hypothesis, provenance=provenance, scheduler=self,
     paper_broker=StubBrokerClient())` -- but `run_gauntlet`'s real
     signature has no `code` parameter at all, and its first two
     parameters (`backtest_func`, `prices`) are required positionals with
     no default. Guaranteed `TypeError` on the very first real call --
     it never once fetched data, compiled the strategy, or built a
     backtest.
  2. **The result was read as a dict.** `result.get("verdict", ...)` --
     but `run_gauntlet` returns a `StrategyCard`, a `@dataclass` with no
     `.get()` method. A second, independent crash had the first one
     somehow not fired.

  Neither bug surfaced in the PR's own test, which monkeypatched
  `plugins.scheduler.run_gauntlet` out entirely with a fake accepting
  `code=`/`symbol=` directly -- exercising the wrong signature instead of
  the real one. The reported `tests_failed` was a third, unrelated bug in
  the same test: `plugin.call_tool(...)` was called without `await`
  (`call_tool` is `async`), so the assertion ran against a bare coroutine
  object.

  Fixed properly rather than patched around: `SchedulerPlugin._run_gauntlet`
  now builds a real backtest wrapper -- compiles the strategy code via
  `compile_strategy`, calls it against the fetched bars, turns its 1/0/-1
  target-position signals into an equity curve (shifted one bar so the
  strategy never trades on a close it hasn't seen yet: `position =
  signals.shift(1)`) -- and calls `run_gauntlet` with the real kwarg names
  (`strategy_code=`, `position_sizes=`, etc.), reading `card.verdict`/
  `card.sharpe`/`card.gates` as real dataclass attributes. Registered as a
  proper MCP tool (`run_gauntlet`, schema requiring `code`/`symbol`/
  `hypothesis`) on `SchedulerPlugin.list_tools()`/`call_tool()` -- the
  same pattern `create_event`/`list_events` already use, reachable via
  `_orc.call_tool` (chat/tray), not the broken `CommandRegistry` shape
  the first attempt used.

  3 new tests use the real, unmocked `run_gauntlet` against synthetic
  two-regime price data (`_trend_prices`: a clean uptrend then a clean
  downtrend, engineered so a real MA-cross strategy compiled through
  `compile_strategy` reliably beats its own buy-and-hold benchmark and
  clears every gate) -- proving the full chain for the first time: tool
  call with real code+symbol+hypothesis -> `run_gauntlet` runs a real
  backtest -> `VALIDATED` -> `StrategySpec` registered in the *same*
  `strategy_specs.db` `_trading_strategy_store` reads (confirmed by
  checking both call sites construct `StrategyStore()` with no path
  override, i.e. the same module-level `_DB_PATH` default -- not the same
  Python object, but the same file, which is how this app already
  coordinates plugin state) -> event scheduled -> `dispatch_due_events`
  (called with the real registered spec, no mocking) actually evaluates
  the compiled strategy and places a real (paper) order. One pre-existing,
  unrelated test (`cerebral/tests/test_plugins_time_notes.py::
  TestSchedulerPlugin::test_list_tools_exposes_four_tools`) asserted an
  exact 4-tool set; updated to 5 and renamed. Full suite: 5087 passed,
  7 skipped, 0 failed.

  **All four parts of S11 now exist.** This is the first point in the
  campaign where that's true. Do not read that as "live trading is
  ready" -- see the next section for what a full hand-trace of the
  complete chain (gauntlet -> graduation -> arm -> live fill -> UI)
  actually found.

- PR #913 -- S35a (auto-merged by self_dev_campaign)
- PR #917 -- S35c (auto-merged by self_dev_campaign)
- PR #918 -- S35d (auto-merged by self_dev_campaign)
### Live trading: what a full trace actually found (2026-08-23)

Per this campaign's own standing rule (a slice's code existing is not
the same as the feature being ready), traced the full chain by hand
after S11c landed -- not just "do the parts exist," but "does invoking
them in order actually produce a live order that's honestly visible."
Two real gaps found, neither part of S11's original four parts:

1. **A strategy's lifecycle status does not survive a Felix restart.**
   `cerebral/main.py`'s `_trading_lifecycle = StrategyLifecycle()` is a
   plain in-memory dict (`cerebral/trading/lifecycle.py`'s `_states`) --
   no SQLite backing, unlike `ForwardRecord`/`StrategyStore`. A restart
   (routine in this campaign -- self_dev's own auto-merge triggers one,
   plus tonight's manual one) silently resets every strategy back to
   "paper" via `get_state`'s lazy-create-as-paper default. This fails
   *safe*, not open -- a restart can never cause an unarmed/ungraduated
   strategy to start trading live, only the reverse (a previously-"live"
   strategy quietly needs to re-earn graduation) -- but it means "live"
   status is not itself a durable fact today, only a cache of one. Since
   `check_graduation` reads real persisted `ForwardRecord` data, a
   strategy that had genuinely earned graduation before a restart should
   re-earn it again within one dispatch cycle if the underlying evidence
   still holds -- not verified end-to-end, since doing so would require
   actually restarting Felix mid-trace.
2. **A live fill is recorded correctly but is invisible as "live" in the
   UI.** S11b correctly threads `phase="live"` all the way to the
   recorded `ForwardRecord` row (verified by 4 tests). But
   `cerebral/main.py`'s `_trading_broadcast` builds `recent_fills` as
   `[{"symbol":..., "side":..., "pnl":...} for f in fills]` -- no
   `phase` field at all -- and `tray/lib/trading-panel.js` has zero
   references to `phase` anywhere (checked directly, not assumed). A
   user watching the Trading panel could not tell a live fill from a
   paper one. This directly violates the Honesty rule this campaign
   documented from the start ("every surfaced number is labelled
   backtest/paper/live -- mixing them is a bug") for the one case that
   matters most: real money moving.

Neither gap is large, but both are exactly the class of thing this
campaign's hand-verification discipline exists to catch before saying
"ready" -- the arm toggle is necessary but not sufficient; the full
`Status:` line above says so. User confirmed 2026-08-23: continue
closing these, with the do-not-risk-live-capital posture unchanged.
Filed as issue #856 (S12) -- see its Landed PRs entry below.

- PR #857 -- S12 -- opened by self_dev_campaign, closed unmerged (full
  bug account in the PR's own comment) -- landed by hand instead on top
  of the real diff (verified via the sandbox's own clone,
  `campaign-trading-s12`, `git diff 829cc2a..selfdev/16766876`, 3 files
  / 77 lines, correctly based on current master). The persistence
  mechanism itself (SQLite-backed `StrategyState`, load-on-init,
  save-on-every-mutation) was sound and kept largely as written -- the
  real bug was structural, not logical: `StrategyLifecycle.__init__` had
  no way to inject a db path at all, unlike `ForwardRecord`/
  `StrategyStore`'s established convention. Every `StrategyLifecycle()`
  in the entire test suite (10 call sites across 3 files) now shared ONE
  real, persistent file with no isolation between test runs. That's what
  actually produced the reported `tests_failed`: two tests
  (`test_dispatch_does_not_graduate_on_all_zero_pnl`,
  `test_dispatch_stays_paper_when_armed_but_not_graduated`) both use
  strategy name "s1", and a different test earlier in the same run
  (`test_dispatch_graduates_a_strategy_whose_paper_pnl_clears_the_bar`)
  had already graduated "s1" to "live" and *saved it to the shared
  file* -- so a fresh `StrategyLifecycle()` constructed in an unrelated
  test picked up stale "live" state instead of a clean default. This is
  not just a test-suite inconvenience: the exact same bug would have
  meant every strategy in *production* shares undifferentiated state the
  moment more than one is ever tracked, since nothing scoped the db by
  profile/session either.

  Fixed by adding `db_path: Optional[Path] = None` to `__init__`
  (defaulting to the real production path, `data_dir() /
  "lifecycle.sqlite"`, when omitted -- `cerebral/main.py`'s
  `_trading_lifecycle = StrategyLifecycle()` needed no change) and
  threading `tmp_path`-scoped paths through all 10 call sites so every
  test is genuinely isolated again. 5 new tests prove the actual claim
  Part A exists to make -- state surviving a *fresh instance* (simulating
  a restart), not just surviving within one already-constructed object:
  graduation, ramp progress past 30 trades, the live equity curve, and a
  halt all persist; a separate test proves two instances against
  different paths genuinely don't share state (the regression this whole
  fix exists to prevent).

  Separately, hand-verifying Part B (not part of PR #857's own diff --
  found while confirming the phase badge would actually be visible):
  `tray/lib/trading-panel.js`'s `renderLiveStrategyCard` has always read
  `data.fills`, but `_trading_broadcast` sends `recent_fills` -- a
  pre-existing key mismatch, present on master since this component was
  built, meaning the Recent Fills table has never rendered a single real
  fill, regardless of what data reached it. Adding a `phase` column to a
  table that never renders anything wouldn't have surfaced anything.
  Fixed the key alongside adding the phase badge
  (`.phase-badge.live`/`.phase-badge.paper`, matching the existing
  status-badge color convention). New `tray/tests/trading-panel.test.js`
  -- a minimal fake-`document` unit test (no new jsdom dependency; this
  repo's jest config has none installed and no test uses one) -- proves
  the fills table actually renders real fill data with a visible
  LIVE/PAPER badge per row, not just that `phase` reaches the broadcast
  payload.

  Full suite: 5091 passed, 7 skipped, 0 failed (`cerebral/tests/` +
  repo-root `tests/`); tray JS 29 suites / 749 tests.

  **Both gaps the full hand-trace (above) found are now closed.** The
  system can run end to end for the first time in this campaign: a real
  strategy submitted via `run_gauntlet` -> validated -> registered and
  scheduled -> dispatched every 5 minutes -> graduates on real paper P&L
  -> (only if armed) would place a live order, correctly labelled and
  durable across a restart. What is still true: no strategy has actually
  been submitted through this in production (only exercised by tests),
  and the arm toggle has never been set to True. Do not risk live
  capital -- the next real step is operational (run a real strategy
  through paper trading for real, over real time, before ever arming
  it), not another slice.

- PR #865 -- S13 -- opened by self_dev_campaign, closed unmerged (full
  bug account in the PR's own comment) -- landed by hand instead on top
  of the real diff (verified via the sandbox's own clone, `campaign-
  trading-s13`, `git diff 028ea97..selfdev/7fd2fdc3`, 3 new files / 202
  lines, purely additive as scoped -- nothing existing touched). Two
  real bugs, both invisible to the PR's own tests because every one of
  them mocked `WindowsSandbox` out entirely:

  1. `sandbox.spawn([...], timeout=20)` never passed the required
     `workdir` positional argument and used the wrong keyword name
     (the real signature is `spawn(cmd, workdir, *, timeout_s=None)`).
     A mocked sandbox accepts any arguments silently, so this never
     surfaced.
  2. **A genuine structural finding about how this sandbox has to be
     used, not just a coding mistake.** The original design wrote
     `strategy.py`, `bars.csv`, and a copy of a `_strategy_runner.py`
     script into the workdir *before* calling `spawn()`. Rewriting the
     tests to use the real (now-working, post-icacls) sandbox instead
     of mocking it immediately exposed this: `WindowsSandbox.
     _ac_grant_workdir` adds a new ACE to the workdir's DACL for a
     fresh per-spawn AppContainer identity, but that grant only applies
     to files that exist -- or are created -- *after* it's added.
     Confirmed empirically with a minimal reproduction: a script file
     copied into the workdir immediately before `spawn()` fails with
     `[Errno 13] Permission denied` when the child tries to open it;
     the identical file, if the child process creates it *itself*
     during its own execution, opens fine. Windows doesn't retroactively
     propagate a folder ACL change onto pre-existing children. Every
     real invocation of the original design would have silently
     returned an all-flat signal, unconditionally -- exactly why the
     fully-mocked tests couldn't have caught it, and exactly the same
     lesson S11c/PR #855 already taught this campaign once (mocking out
     the exact function whose real signature/behavior matters hides the
     mismatch instead of testing it).

  Fixed by redesigning `evaluate_signals`: strategy code and bars data
  now travel as base64-encoded command-line arguments into an inline
  `python -c` bootstrap -- no file the sandboxed child needs to *read*
  exists before it runs. `signals.json` is the only file involved,
  written by the child itself mid-execution, which works correctly per
  the finding above. Deleted the now-unused `_strategy_runner.py` --
  keeping an unwired file with the same name as the real mechanism is
  exactly the kind of leftover this campaign has repeatedly found
  causing confusion later.

  `test_sandboxed_eval.py` was rewritten to exercise the real,
  unmocked `WindowsSandbox` (auto-skips via `pytest.mark.skipif` where
  it's unavailable) -- including a real containment test that attempts
  a write into the repo itself (not a temp path, deliberately: TEMP is
  one of the few env vars the sandbox keeps, an ambiguous target that
  wouldn't distinguish AppContainer confinement from ordinary Windows
  permissions) and confirms both that the write fails and that
  `evaluate_signals` degrades to an all-flat signal rather than
  propagating whatever the attempt produced. Also fixed the test
  fixture's column casing (`data['close']`, lowercase) to match the
  established `Open/High/Low/Close/Volume` contract used everywhere
  else in this campaign (`to_strategy`'s own prompt, `live_tick.py`,
  every other test fixture) -- a real, if smaller, deviation the first
  attempt introduced. All 5 tests pass against the real sandbox. Full
  suite: 5096 passed, 7 skipped, 0 failed.

  **Mechanism only -- nothing existing is wired to this yet.** S14
  (#859) retires `compile_strategy`'s in-process exec and routes both
  production call sites through this.

- PR #866 -- S14 -- opened by self_dev_campaign, closed unmerged (full
  bug account in the PR's own comment) -- landed by hand instead on top
  of the real diff (verified via the sandbox's own clone, `campaign-
  trading-s14`, `git diff bc7c130..selfdev/69fe26cb`, 6 files / 45
  lines). The core was correct: both `live_tick.py` and `plugins/
  scheduler.py` route through `evaluate_signals` now, `compile_strategy`
  was correctly demoted to `_compile_strategy` (test-only, docstring
  updated), the warm-up-length right-align fix and the `asyncio.
  to_thread` wrap were both present and correct. Two problems:

  1. **The reported `tests_failed` cause.** `cerebral/tests/
     test_trading_ideas.py` still imported the old public
     `compile_strategy` name -- an `ImportError` at collection, since
     it was never updated when the function was renamed (unlike
     `test_trading_live_tick.py`'s matching import in this same PR,
     which WAS updated). Fixed with the same alias pattern:
     `from cerebral.trading_ideas import _compile_strategy as
     compile_strategy`.
  2. **A real behavior change the PR's own test suite didn't account
     for.** `test_run_paper_strategy_reports_a_bad_strategy_instead_of_
     raising` asserted a broken strategy (missing `def strategy(data)`
     entirely) produces `status == "error"` -- but the sandboxed
     evaluator never propagates a strategy's own failure as an
     exception; it degrades to an all-flat signal by design, exactly
     per issue #859's own acceptance criteria ("never an order placed
     on a sandbox failure"). Renamed and rewrote the test to assert the
     correct `"hold"` status. Since a silently-degrading failure with
     no error status is otherwise unobservable, also added
     `logger.warning` calls to every fallback path in `sandboxed_eval.
     evaluate_signals` (sandbox exit failure, missing output, malformed
     output, any raised exception) -- the rewritten test verifies both
     the safe degradation and that it's actually logged.

  Full suite: 5096 passed, 7 skipped, 0 failed.

  **No production code executes untrusted strategy source in Felix's
  own process anymore.** Confirmed by grep: zero production callers of
  the old public `compile_strategy` name remain anywhere in the repo.

- PR #867 -- S15 -- **the first slice this session where self_dev's own
  merge gate reported `auto_merge`** rather than `tests_failed` -- and
  the first proof that "the gate passed" still isn't the same as
  "correct," the exact lesson every `tests_failed` slice this session
  had already taught the hard way. The reported gate result is not
  evidence on its own; the true diff (verified via the sandbox's own
  clone, `campaign-trading-s15`, `git diff ff53387..selfdev/2bde9a6f`,
  1 file / 11 lines) had to be hand-checked exactly like every other
  slice, and it failed that check. Because it auto-merged, `self_dev_
  campaign`'s own `pull_fn` had already fast-forwarded local master to
  the merged commit (`eb2f0f7`) by the time this was reviewed -- unlike
  every prior slice, there was no open PR left to close; the fix landed
  as a normal commit on top instead.

  Two real bugs, both invisible because the PR added **zero tests**:

  1. **`router.complete(prompt, task_type="coding")` was never awaited.**
     `to_strategy` was a plain `def`, not `async def`, and `_router.
     complete` is `async def complete(self, prompt, task_type) -> str`
     everywhere else in this codebase (confirmed directly in `cerebral/
     llm/router.py`). Calling an async method without `await` doesn't
     raise -- it silently returns a coroutine object. That object would
     have been returned as if it were generated Python source, then
     immediately broken downstream (`code.encode('utf-8')` inside
     `sandboxed_eval.evaluate_signals` on a coroutine object raises
     `AttributeError`) -- except the bug was wrapped in a bare `except
     Exception: pass` with no logging, so even that downstream crash
     would have vanished silently and produced the stub as if nothing
     had gone wrong. Every real invocation of the router path would
     have failed completely undetected.
  2. **No test exercised the router path at all.** The PR's diff was
     purely a signature change to `to_strategy` plus the two broken
     lines above -- nothing called it with a `router=` argument, fake
     or real, so the missing `await` had no chance to surface.

  Fixed by making `to_strategy` `async def` (it has exactly two callers
  in the whole repo, both in its own test file -- confirmed by grep, so
  converting it was safe), correctly `await`-ing the router, and
  replacing the bare `except: pass` with a logged warning before
  falling back to the stub (conservative-continue, matching this
  campaign's failure-behaviour convention). `test_trading_ideas.py`'s
  `TestTradingIdeas` moved from `unittest.TestCase` to `unittest.
  IsolatedAsyncioTestCase` (a drop-in replacement -- existing sync test
  methods are unaffected) so its 3 existing `to_strategy` callers could
  become real `async def test_...` methods. Added 3 new tests: a fake
  async router proves the real router path is used and its output
  returned (not the stub); a router that raises proves the fallback to
  the stub still works and never propagates the failure; the no-model
  default path is unchanged. Full suite: 5099 passed, 7 skipped, 0
  failed.

  **Still missing, confirmed by grep (not assumed): nothing in the
  running app calls `to_strategy` at all.** `to_strategy` now correctly
  generates real code when given a router, but there is still no
  reachable production path from "a URL/book claim" to "gauntlet-
  validated strategy code" -- issue #860 updated in place, continuing
  as S15b.

- PR #868 -- S15b -- opened by self_dev_campaign, closed unmerged (full
  bug account in the PR's own comment) -- landed by hand instead on top
  of the real diff (verified via the sandbox's own clone, `campaign-
  trading-s15b`, `git diff 4898c45..selfdev/48f000d9`, 2 files / 48
  lines). The shape was right -- `SchedulerPlugin` now takes a `router`,
  `run_gauntlet`'s schema accepts `claim`/`url`/`book`+`chapter` as
  alternatives to `code`, the idea-sourcing dispatch (`from_prose`/
  `from_book_claim`/`extract_from_url`) was correct. Two real bugs, and
  notably **both are the same failure mode S15's own PR #867 had, one
  session, twice**:

  1. Both fallback branches (`code = to_strategy(idea)` on router
     failure, and on no router at all) called the async `to_strategy`
     (S15) without `await` -- a coroutine object, not code, on either
     path. Only the router-success branch correctly awaited it.
  2. `_run_gauntlet` correctly became `async def` (it has to, to await
     `to_strategy`), and `call_tool`'s dispatch was correctly updated to
     `await` it -- but the 4 existing tests from S11c that call `plugin.
     _run_gauntlet(...)` directly were never updated, which is what
     actually produced the reported `tests_failed`.

  Fixed by simplifying rather than patching around: `to_strategy`
  already handles `router=None` and a router failure internally (falls
  back to the stub, logs a warning -- that's the whole point of S15's
  own fix), so the duplicate try/except-with-manual-fallback in
  `_run_gauntlet` was redundant as well as buggy. Replaced with one
  line: `code = await to_strategy(idea, router=self._router)`. Verified
  (not assumed) that `cerebral/main.py`'s `_router = ModelRouter()`
  (line 124) is constructed well before `_scheduler_plugin =
  _SchedulerPlugin()` (line 249) -- a plain constructor parameter is
  safe, no deferred-seam pattern needed. Updated the 4 existing tests
  to `async def`/`await`; one assertion was also stale (`"code" in r.
  content` on the required-fields check -- `code` isn't required at all
  anymore, only `symbol`+`hypothesis` are) and got split into two tests
  for the two real requirement paths. Added 2 new tests: a fake async
  router proves a `claim` (not code) produces real router-generated
  code that reaches an actual `run_gauntlet` call and gets registered
  (not just that `to_strategy` works in isolation); a no-router case
  proves it degrades to the stub without crashing. Full suite: 5102
  passed, 7 skipped, 0 failed.

  **Issue #860 closed -- both S15 and S15b are genuinely done.** A user
  (or Felix) can now call `run_gauntlet` with a book claim, a URL, or
  plain prose, and -- for the first time in this campaign -- get back
  real, source-derived strategy code that flows through the full
  gauntlet -> registration -> scheduling -> dispatch chain, not a
  generic stub. Worth naming plainly: two slices in a row hit the exact
  same missing-`await` bug independently, on two different functions
  that both needed to become async for the same underlying reason
  (calling the router). Neither was caught by self_dev's own merge gate
  -- S15's variant auto-merged with zero tests; S15b's variant shipped
  alongside otherwise-correct code and only the pre-existing tests
  (unrelated to the new code) caught the second layer of it. Worth
  double-checking `await` explicitly on every `async def` call this
  campaign adds from here on, not just trusting the gate.

- PR #869 -- S16 -- **this session's second `auto_merge`, and like the
  first (S15/#867), the merge gate passing was not evidence of
  correctness.** Also, again, zero test changes in the diff -- the same
  red flag as S15. Auto-merged, so `pull_fn` had already fast-forwarded
  local master to the merged commit by the time this was reviewed; the
  fix landed as a normal commit on top, no PR left to close (verified
  via the sandbox's own clone, `campaign-trading-s16`, `git diff
  d58f6fd..selfdev/372e62b1`, 2 files / 49 lines).

  The real, structural parts were correct: a new `strategy_versions`
  table alongside the unchanged `strategy_specs`, genuinely append-only
  `save()` (a real test now proves two saves produce two version rows,
  not a silent overwrite -- the exact bug `strategy_specs` has always
  had, which this table exists to not repeat), and `gauntlet.py`'s
  auto-promote correctly recording `origin='generated'` with real
  provenance/hypothesis on every VALIDATED pass.

  One real bug: **`render_provenance` called `row.get("components_
  json")` on a `sqlite3.Row`** -- confirmed directly, `sqlite3.Row` has
  no `.get()` method (it supports dict-style `row[...]` access but
  isn't a real `Mapping`) -- guaranteed `AttributeError` on the very
  first real call. Nothing caught it because nothing *could* call it:
  there was also no method anywhere to actually fetch a `strategy_
  versions` row in the first place, so the function was present but
  practically unreachable -- the same "real code, zero path to exercise
  it" shape S15's original PR had.

  Fixed the `.get()` call (`row[...]`, which correctly returns `None`
  for the column's actual NULL value). Added `StrategyStore.
  get_current_version(strategy_id)` -- the missing reader; S17 (edit)
  and S18 (mix) both need to read a strategy's real current version and
  nothing built that path. New `cerebral/tests/
  test_trading_strategy_store.py` (7 tests): the append-only proof,
  `get_current_version` for known/unknown strategies, `render_
  provenance` for `generated`/`user_edited`/`mixed`/unknown origins --
  including the exact case that would have crashed. Extended the
  existing gauntlet auto-promote test (`test_validated_registers_the_
  spec_the_dispatcher_needs`) to assert a VALIDATED pass records real
  lineage, not just the dispatch pointer. Full suite: 5109 passed, 7
  skipped, 0 failed.

- S17 (#862) -- **stopped after 5 consecutive failures, no PR, nothing
  landed.** Every `python scripts/trigger_campaign.py TRADING.md 1`
  attempt failed identically: `"Edit step produced no commit --
  aborting."` -- the model's edit step made no committable change
  against issue #862's description, five times in a row.

  Investigated whether this was the S7-class ledger-replay bug (a
  recorded phase result gets resumed instead of re-run when the same
  run_id is retriggered) before attempt 5, since two byte-identical
  failures back to back matched that precedent's shape. It is not:
  read `plugins/self_dev.py`'s `_run` directly -- the "no commit" branch
  (`if not edit_result.get("committed"): return ToolResult(...,
  is_error=True)`) returns *before* the `self._ledger.record(run_id,
  "edit", ...)` call, so a no-commit failure is never persisted.
  Confirmed empirically too: queried `chain_steps` in
  `cerebral/data/openmind.db` for `run_id='campaign-trading-s17'` --
  only the `clone` phase is recorded, `edit` never is. The sandbox
  clone (`cerebral/data/sandbox/self_dev/campaign-trading-s17`) is
  clean and matches current master (`8c5c795`, S16's tip) -- not stale.
  So all 5 attempts genuinely re-invoked the edit model fresh against
  the same clean, current clone; this is real, repeated stochastic
  failure on this specific slice, not an artifact of the harness.

  Per the standing decision tree for this failure mode: a 5th identical
  failure is past "retry immediately, no wait needed" territory and
  needs human attention rather than a 6th automated attempt -- reported
  to the user instead of continuing. `campaign-trading-s17`'s ledger
  (just the one `clone` row) is left as-is; a future retry can reuse it
  or pass `restart: true` to force a fresh clone if issue #862's
  description itself turns out to need rewording.

  **User chose the rewording path.** Read issue #862's actual body
  against the real current code (not assumed) to find out why 5
  identical model-level failures happened on a slice whose own scope
  read as no larger than several already-landed ones. Real cause found:
  `self_dev`'s edit step is pinned to `custom/budd-quick`
  (`model_task_pin` for `task_type='self_dev'`) -- a real model, not the
  `Model: sonnet` note on this driver file (that field is parsed by
  `parse_driver_model` but never wired into the edit step's model
  selection; it's informational only). budd-quick declares a real
  131072-token context window, so the #760 context-floor issue didn't
  apply -- the failure was the model not producing a single coherent
  SEARCH/REPLACE diff against a 4-file, cross-cutting design problem
  (thread a version-scoped forward-record key through `dispatch_due_
  events`, `_run_paper_strategy`, and `run_gauntlet`'s auto-promote
  block, plus two brand-new tool methods) stated only as prose.

  Rewrote issue #862 from scratch: designed the actual minimal diff by
  hand first (confirmed `forward_record.py` and `lifecycle.py` need ZERO
  changes -- both already take an arbitrary string identity; the whole
  "restarts clean" requirement collapses to computing `f"{id}@v{n}"`
  once in `dispatch_due_events` and threading it through one new
  optional, backward-compatible parameter), then wrote the issue body as
  near-literal before/after code snippets quoted verbatim from the real
  files, not prose describing the goal. Also explicitly ruled out two
  things the original wording implied but neither the schema nor the
  acceptance criteria required (recording a failed edit's verdict in
  lineage -- no column for it, and `store.save()` already never fires on
  UNVALIDATED for a first-time submission either; and any change to
  `forward_record.py`/`lifecycle.py`).

  Attempt 6 (`python scripts/trigger_campaign.py TRADING.md 1`) produced
  a real commit for the first time -- PR #870, `tests_failed` (left
  open, not auto-merged). Verified via the sandbox's own clone
  (`cerebral/data/sandbox/self_dev/campaign-trading-s17`, `git diff
  8c5c795..6ee91b7`, 4 files / 100 lines, correctly based on current
  master): the diff matched the reworded issue's prescribed snippets
  almost character for character in `cerebral/trading/gauntlet.py`,
  `plugins/scheduler.py`, and `cerebral/trading/live_tick.py` --
  confirming the diagnosis. Cherry-picked onto local master (f585ac4).

  Two real problems, neither in the design itself:
  1. **Zero new tests** -- the issue explicitly asked for tests covering
     `edit_strategy` (validated/unvalidated/missing-strategy),
     `get_strategy_code`, and a dispatch-level proof of the version
     scoping; none were added. The same "auto_merge / opened PR with no
     test changes is maximally suspicious" red flag S15/S16 already
     established, just manifesting as `tests_failed` instead of a clean
     auto-merge this time.
  2. **3 pre-existing tests broke** because they weren't updated for the
     new scoping: `test_end_to_end_due_event_dispatches_a_real_paper_
     trade` and `test_end_to_end_scheduled_strategy_buys_then_sells_
     with_real_pnl` (both in `test_plugin_scheduler.py`) asserted
     `record.get_fills(strategy_id="<bare name>")` -- now empty, since
     `store.save()` (S16) always creates a real lineage row, so
     `dispatch_due_events` always finds a current version and uses the
     versioned key. And `test_plugins_time_notes.py`'s
     `test_list_tools_exposes_five_tools` hardcoded the tool count --
     the exact same trap that broke an S11c test at 4 tools; now 7.

  Fixed by hand (commit ab4e1e6): updated the 3 broken tests to the
  versioned keys (`"MA cross test@v1"`, `"penny breakout@v1"`/`"@v2"` --
  the round-trip P&L test's own second `store.save()` call, used to
  flip the strategy's behavior mid-test, is itself a real edit under the
  new design, so its open and close fills genuinely land under two
  different versions now; the live P&L assertion itself is computed from
  the broker's own position math, not read back from storage, so it was
  unaffected), renamed the tool-count test to `..._seven_tools`, and
  added the 7 tests the issue asked for and self_dev's own attempt
  skipped: `test_edit_strategy_validated_records_a_new_version_and_
  moves_the_pointer`, `..._unvalidated_does_not_move_the_dispatch_
  pointer`, `..._requires_an_existing_strategy`,
  `test_get_strategy_code_returns_real_source_and_provenance`,
  `..._unknown_strategy_is_an_error` (all in `test_plugin_scheduler.py`,
  using the real unmocked `run_gauntlet`/`_trend_prices`/`MA_CROSS_CODE`
  fixtures S11c's own tests already established), and two
  `dispatch_due_events`-level tests in `test_trading_live_tick.py` --
  `test_dispatch_scopes_the_forward_record_to_the_current_version`
  (seeds real v1 history, edits to v2, asserts the dispatcher reads/
  writes v2's own empty record while v1's stays intact) and `..._falls_
  back_to_the_bare_id_with_no_lineage` (no `store` passed -> unchanged
  pre-S17 behavior) -- extended `FakeScheduler` with a `dispatch_ids`
  list so both tests assert the exact identity string that crossed the
  scheduler seam, not an inference from side effects.

  Full suite: 5116 passed, 7 skipped, 0 failed (`cerebral/tests/` +
  repo-root `tests/`). PR #870 closed unmerged in favor of this
  hand-verified local landing (same pattern as S7/S8/S9/S11b/S11c/S12/
  S13/S14).

  **Worth naming plainly for future issue-writing on this campaign:**
  a near-diff-level issue body (exact file, exact before/after snippet,
  quoted verbatim from the real current code) took a self_dev attempt
  from 5 straight "no commit" failures to a correct 4-file diff on the
  very next try -- but it did NOT make the model reliably add the tests
  the issue asked for, even when those tests were also described in
  comparable (though less literal) detail. Prescriptive-to-the-line
  works for "what code changes"; it does not yet reliably work for
  "and prove it" -- hand-verification (and hand-writing tests when
  missing) remains load-bearing regardless of how the issue is worded.

- PR #871 -- S18 -- attempt 1 hit a transient Bonsai HTTP 502 (`all
  enabled models unavailable`); reverted TRADING.md's Status line
  (verified only that line had changed), waited 5 minutes per the
  established outage policy, retried. Attempt 2 succeeded -- the first
  self_dev attempt on issue #863 (unlike S17, which needed a rewording
  first) -- and, notably, **did include real tests this time** (91 lines
  in a new `cerebral/tests/test_trading_compose.py`), unlike S17's
  attempt. `tests_failed` was still correct: verified via the sandbox's
  own clone (`cerebral/data/sandbox/self_dev/campaign-trading-s18`,
  `git diff dd0748b..a89345e`, 3 files / 216 lines, purely additive,
  correctly based on current master) -- two real bugs, one the PR's own
  tests caught and one they couldn't:

  1. **A guaranteed `SyntaxError`, caught by the PR's own test.** The
     new `cerebral/trading/compose.py`'s `majority`-mode combine-logic
     string had mismatched brackets in two places --
     `min(len(s) for s in signals_list]` and
     `sum(s[i] for s in aligned]`, both opened with `(` and closed with
     `]`. Any composite generated in majority mode would fail to
     `exec()` at all. `test_majority_alignment_and_logic` (which does a
     real `exec(code, ns)`, not a mock) caught it immediately -- this is
     what produced `tests_failed`. The alignment/sign logic itself was
     correct once the brackets were fixed; no assertion changes needed.

  2. **A deeper bug neither of the PR's `mix_strategies` tests could
     see, because both mock `StrategyStore` and `_run_gauntlet` entirely
     via `MagicMock(spec=StrategyStore)` and a fake `_run_gauntlet`
     capturing its call args.** `run_gauntlet` (`cerebral/trading/
     gauntlet.py`) and `_run_gauntlet` (`plugins/scheduler.py`) never
     had a `components_json` parameter -- S17 added `origin`/
     `parent_version`/`strategy_id` passthroughs but not this one, and
     nothing about S18's own diff added it either. So
     `_run_mix_strategies` packed component identities into the plain
     `provenance` string instead (`f"Mixed strategy ({mode}): {json...}"`)
     -- but `StrategyStore.render_provenance`'s `'mixed'` branch (S16)
     reads a SEPARATE column, `row["components_json"]`, which nothing
     ever wrote. Every real mixed strategy's lineage row would have had
     `components_json = NULL` forever, and `render_provenance` could
     never actually name a single component -- directly failing issue
     #863's own 4th acceptance criterion ("A mix's StrategyCard/lineage
     names every component at its pinned version") for real, invisible
     to both mocked tests since neither one exercises
     `StrategyStore.save`/`render_provenance` for real.

  Fixed by threading `components_json: Any = None` through
  `run_gauntlet` and `_run_gauntlet` (mirroring S17's origin/
  parent_version/strategy_id pattern exactly) into the existing
  `store.save(...)` call, and changing `_run_mix_strategies` to pass a
  real Python list of `{"id", "provenance"}` dicts as `components_json`
  (not a pre-serialized string embedded in `provenance` -- `store.save()`
  already does its own `json.dumps` internally, matching how every other
  origin already uses the parameter). Also simplified a tautological
  `store = strategy_store if strategy_store is None else strategy_store`
  (both branches returned the same value; harmless but confusing) to a
  plain if/else matching `_edit_strategy`'s established pattern, and
  fixed the scheduler tool-count test for the third time this campaign
  (5 -> 7 -> 8 tools across S11c/S17/S18) -- flagged as a background task
  to make that test stop needing a manual edit on every new tool, since
  it has now cost a hand-fix on 3 separate slices for reasons unrelated
  to whatever the slice actually changed.

  Added `test_mix_strategies_end_to_end_persists_real_components_json`:
  the real, unmocked `_run_gauntlet` -> `run_gauntlet` -> `StrategyStore`
  chain, registering two strategies and mixing them, then reading back
  the real `strategy_versions` row and asserting `render_provenance`
  actually names both components -- proving the fixed acceptance
  criterion for real rather than via a captured mock argument. Uses the
  SAME strategy code registered under two different `strategy_id`s
  (deliberately, not two different MA-cross variants) so the test's
  reliability doesn't depend on two different signals happening to agree
  often enough to still clear the full gauntlet -- unanimous composition
  of a signal with itself reproduces the exact original signal,
  eliminating that risk entirely rather than leaving it to chance.

  Full suite: 5122 passed, 7 skipped, 0 failed (`cerebral/tests/` +
  repo-root `tests/`). PR #871 closed unmerged in favor of this
  hand-verified local landing (commits 0c9f574, 7311d69, and this
  TRADING.md update).

- PR #872 -- S19 -- attempt 1 hit a transient Bonsai HTTP 502; reverted
  TRADING.md's Status line (verified only that line changed), waited 5
  minutes, retried. Attempt 2 succeeded. Touches `tray/` and
  `cerebral/main.py`, both GUARDRAIL_PATHS per ADR-0015 -- self_dev can
  only ever draft here, never auto-merge, so `tests_failed` (or any
  merge_decision short of a clean pass) was always going to mean
  "review by hand," regardless of what the diff actually contained.

  Verified via the sandbox's own clone (`cerebral/data/sandbox/self_dev/
  campaign-trading-s19`, `git diff 34783e9..bf27dd3`, 2 files / 121
  lines, correctly based on current master). The structural shape was
  reasonable -- a real multi-strategy list UI, the UMD wrapper kept
  intact -- but **zero test files were touched**, the same red flag
  S15/S16/S18 have each independently confirmed correlates with real
  bugs on this campaign, and this was no exception. Three, none caught
  by anything because nothing tested this code at all:

  1. **Every strategy's provenance/version stayed permanently fake.**
     `_trading_broadcast` (`cerebral/main.py`) read
     `getattr(state, "provenance", "")`, `getattr(state, "version", 0)`,
     `getattr(state, "code", "")` off `state` -- a `StrategyState`
     object from `cerebral/trading/lifecycle.py`, which has none of
     those attributes at all. The real data lives in `StrategyStore`
     (S16-S18's `strategy_versions`/`strategy_specs`, already built and
     working) -- never queried. The `getattr(..., default)` pattern
     didn't crash, it just silently returned the default every single
     time, so this would have shipped to a real user looking like a
     completed feature (version badges, a provenance box) while
     permanently showing blank/zero for every strategy. Fixed to
     actually call `_trading_strategy_store.get()`/
     `get_current_version()`/`render_provenance()`, stripping the
     `"@vN"` dispatch-id suffix S17 introduced (`_trading_lifecycle`'s
     own keys) to recover the base `strategy_id` `strategy_versions` is
     keyed by.
  2. **The Save button called `window.sendEvent` directly** -- the
     exact bug `tray/lib/trading-panel.js`'s own header comment has
     warned about by name since S9 ("neither of which anything in this
     app ever sends"). Whether or not this happens to resolve in a real
     browser (top-level function declarations in `main.html`'s own
     non-module `<script>` block do attach to `window`), it directly
     contradicts the file's own documented architecture -- and it's
     untestable in the Node/jest harness this repo actually uses
     (`window` doesn't exist there at all without a jsdom dependency
     this campaign has deliberately avoided). Fixed by threading the
     caller's real `sendEvent` through as an explicit parameter
     (`renderTradingUpdate(data, container, sendEventFn)`), matching
     every other panel's convention of the CALLER owning the transport,
     never the module.
  3. **No backend route for `strategy_edit` existed at all.** Even with
     bug 2 fixed, `main.py`'s WS dispatcher (the giant `elif t == ...`
     chain) had no branch for it -- the Save button would send into the
     void regardless. Added one, calling the real `_scheduler_plugin.
     call_tool("edit_strategy", ...)` (S17) and re-broadcasting trading
     state afterward.

  Fixed all three, plus extracted the Save button's event-building into
  a pure `buildStrategyEditEvent(strategy, code)` function (matching
  `self-dev-card.js`'s established `buildStateMessage` precedent) so
  the event shape is directly unit-testable without simulating a DOM
  click -- this repo's tray tests deliberately have no jsdom dependency
  (S12's precedent), so a real click-simulation integration test (the
  issue's own "(if practical)" hedge on this exact point) would have
  needed either a new dependency or a hand-built DOM mock detailed
  enough to risk becoming exactly the kind of fake-mock that hides real
  bugs this campaign has repeatedly warned about -- skipped deliberately
  rather than built badly. Added 4 new JS tests instead (multi-strategy
  rendering, provenance/version actually rendered -- not just present in
  the broadcast payload, S12's own lesson -- a no-lineage fallback, and
  the real `buildStrategyEditEvent` shape). No new Python test for
  `_trading_broadcast` itself: checked `cerebral/tests/
  test_main_dispatcher.py` first and confirmed this codebase's own
  convention is to test dispatcher isolation generically, never one
  `elif` branch's business logic in isolation -- adding one here would
  have been inconsistent with how every other branch in that same
  function is (and isn't) tested.

  Full suite: 5122 passed, 7 skipped, 0 failed (`cerebral/tests/` +
  repo-root `tests/`); tray JS: 29 suites, 753 tests, including
  `render-smoke.test.js` (the test that already caught a real main.html
  regression once, at S9). PR #872 closed unmerged in favor of this
  hand-verified local landing (commits e0c92a4, 9230f09, and this
  TRADING.md update).

  **The full S13-S19 blueprint (2026-08-23 -- sandbox, edit, mix, panel
  UI) is now complete.** See "What's next" below for what that does and
  doesn't mean.

- PR #882 -- S20 -- opened by self_dev_campaign after 3 straight Bonsai
  502s (retried per the outage policy; the 4th attempt succeeded), closed
  unmerged in favor of a hand-verified landing (commit bfa1fe0) -- full
  bug account in the PR's own closing comment. Real diff verified via the
  sandbox's own clone (`campaign-trading-s20`, `git diff
  5bda72d..22aca1c`, 4 files / 39 lines, correctly based on current
  master). The PR added **zero tests**, despite issue #873 explicitly
  listing test files to touch -- every one of the four bugs below was
  invisible to its own `tests_failed` gate and would have shipped silent:

  1. `risk.check_order(...)` was called with kwargs `equity=`/
     `positions=`/`daily_loss=` -- none matching `RiskManager.check_order`'s
     real signature (`account_equity`, `current_positions_count`,
     `current_daily_loss`). Guaranteed `TypeError` on every tick where
     `risk` is not `None`, i.e. every real dispatch after this PR's own
     wiring landed. This is what actually produced the reported
     `tests_failed`, once a `FakeScheduler` test double picked up the new
     `risk=` kwarg and called the real (buggy) code underneath it.
  2. **The ramp fix was never actually a fix.** `apply_position_ramp`'s
     `size_pct` was plumbed as a new parameter through
     `run_strategy_tick`/`dispatch_due_events`/`_run_paper_strategy`, but
     nothing ever called `lifecycle.apply_position_ramp()` *before*
     dispatch and passed its result in. The only remaining call to
     `apply_position_ramp` in the diff is post-hoc, inside
     `_apply_lifecycle`, purely for a log line after the trade already
     ran. A "25% ramp" would still have opened at full size -- the exact
     bug #873 named, just moved one line over. Fixed by computing
     `ramp_pct = lifecycle.apply_position_ramp(dispatch_id) if is_live
     else 1.0` inside `dispatch_due_events`'s per-strategy loop, before
     the call to `_run_paper_strategy`.
  3. `current_daily_loss` was hardcoded `0.0` -- permanently inert,
     defeating the entire point of wiring a daily-loss halt (decision
     #47's "daily-loss halt", the second of RiskManager's three gates).
     Fixed with a new `ForwardRecord.get_daily_pnl(day_iso=None)` (one
     `SUM(pnl) WHERE substr(timestamp,1,10)=?` query against columns that
     already exist -- no schema change, no fabricated number, account-wide
     rather than per-strategy since the halt is meant to stop all trading
     for the day) and `current_daily_loss = max(0.0,
     -forward_record.get_daily_pnl())`.
  4. **Applying fix #1 as a straight kwarg rename surfaced a fifth bug**:
     `spec.qty = spec.qty * size_pct` tried to mutate `StrategySpec`,
     which is `@dataclass(frozen=True)` -- would have raised
     `FrozenInstanceError` the first time a live-ramped strategy actually
     dispatched, i.e. exactly when fix #2 made `size_pct != 1.0`
     reachable for the first time. Fixed to a local `open_qty` variable
     instead of mutating spec; `decide_action` closes at the position's
     own qty regardless of `open_qty`, so this only affects the open
     side, matching the ramp's actual intent (gradual entry, full exit).

  A sixth issue, not a crash but silently defeating the point of its own
  acceptance criterion: `RiskManager` was constructed in `main.py` with
  only `alert_dispatcher=`, not `settings_store=_settings` -- so the three
  new user-configurable risk-limit keys (#873's own 4th acceptance
  criterion) would never actually reach a live risk check, freezing
  `RiskConfig`'s hardcoded defaults regardless of what the user set via
  Settings. Fixed, and construction moved to right after `_settings =
  _SettingsStore()` is defined (previously constructed several lines
  before `_settings` existed at all -- the straightforward `settings_store=
  _settings` fix would have raised `NameError` at import time otherwise).

  Added 9 new tests directly exercising the fixed paths: an over-limit
  order blocked with `broker._orders` staying empty (the acceptance test
  #873 names verbatim), a within-limit order still opening normally, the
  daily-loss halt firing off real accrued P&L instead of a fabricated
  zero, the ramp actually shrinking the opened qty without touching the
  frozen spec, a blocked-order alert and a graduation alert both landing
  in one shared `AlertDispatcher`'s history, the three settings keys
  round-tripping through `SettingsStore`, and `RiskManager` honoring a
  live `settings_store` value instead of its baked-in default. Also fixed
  2 stale test doubles the diff broke: `FakeScheduler._run_paper_strategy`
  needed `risk=`/`size_pct=` parameters to accept the new call shape, and
  `test_settings.py::test_all_returns_all_keys`'s expected-key set
  predates the three new settings keys. Full suite green: 5130 passed, 7
  skipped, 0 failed (`cerebral/tests/` + repo-root `tests/`). This slice
  does not touch `tray/`, so no jest run was needed.

  **RiskManager is now a real gate on every live/paper dispatch tick, and
  its three limits (per-trade %, daily-loss halt, max concurrent
  positions) are all reachable and user-configurable.** S21 (alpaca-py +
  live preflight) is next; per decision #47, `trading_live_arm` still must
  not be set True until S21 also lands and is hand-verified.

- PR #884 -- S21 -- opened by self_dev_campaign against the reworded,
  near-diff-level issue #874 (see the entry above this one: the original
  4-part issue failed 5 straight times with an identical "Edit step
  produced no commit", genuinely confirmed via the sandbox clone's own
  `git log` staying at master's tip every time -- not a ledger replay,
  since a no-commit failure is never persisted to the step ledger, so
  every retry re-invoked the model fresh against the same clean clone).
  Narrowed #874 to dependency + preflight only (correlation-limit split
  to a new issue, #883/S21b) and rewrote it as exact before/after code
  snippets quoted from the real files, per S17's exact method. Produced
  a correct diff on the very next attempt -- confirms the S17 finding
  again: a near-diff-level issue body reliably fixes this failure mode.

  Real diff verified via the sandbox's own clone (`campaign-trading-s21`,
  `git diff 24183c6..770a1f1`, 5 files / 60 lines): `alpaca-py` added to
  `pyproject.toml`; `AlpacaBrokerClient.preflight()` (package importable,
  keyring credentials present, `get_account()` reachable and `ACTIVE`);
  `dispatch_due_events` calls it once before ever routing to a live
  broker, emitting a critical `live_preflight_failed` alert and staying
  on paper on failure; a new Preflight section in
  `docs/trading-live-verify.md`. Matched the prescribed snippets almost
  character for character in every file.

  One real bug, again with zero new tests despite the issue explicitly
  listing test files: `dispatch_due_events` constructed
  `AlpacaBrokerClient(env="live")` directly inline, with no injection
  seam. `preflight()` makes a genuine credential/network check
  (`import alpaca.trading.client`, `keyring.get_password`,
  `get_account()`) -- which now fired even inside a pure unit test with
  `FakeScheduler`, since nothing in the diff let a test substitute a
  fake broker. Broke 2 of S20's own tests
  (`test_dispatch_goes_live_only_when_armed_and_graduated`,
  `test_dispatch_applies_live_ramp_to_the_open_qty`) the moment they ran
  against this diff -- both silently fell back to paper because the real
  preflight() correctly failed (no real Alpaca credentials in this dev
  environment). That's the conservative-continue behavior working
  exactly as designed, just not observable or injectable from a test.
  Fixed with a `live_broker_factory: Optional[Callable[[], Any]] = None`
  parameter on `dispatch_due_events`, matching the existing
  `fetch=`/`store=`/`risk=` injection-seam pattern already on that
  function; updated the 2 broken tests to inject a `FakePreflightBroker`
  stub, and added `test_dispatch_stays_paper_and_alerts_when_preflight_
  fails` covering the acceptance criterion the issue named.

  Also added the 5 `preflight()` unit tests the issue's acceptance
  criteria named (package missing, no credentials, unreachable account,
  non-`ACTIVE` status, success) in a new
  `cerebral/tests/test_trading_broker_preflight.py` -- none existed.
  Installed `alpaca-py` into this dev environment (`pip install
  alpaca-py`) so "importable after `pip install -e .`" is genuinely
  verified rather than assumed, and so the preflight tests exercise all
  5 branches for real instead of always hitting "package missing".

  Full suite green: 5136 passed, 7 skipped, 0 failed (`cerebral/tests/`
  + repo-root `tests/`). This slice does not touch `tray/`, so no jest
  run was needed. PR #884 closed unmerged in favor of this
  hand-verified local landing (commit 3c51b33).

  **Both P0 gap-closure slices decision #47 named are now landed:
  RiskManager enforces real limits on every tick, and the live path
  fails loudly instead of silently.** `check_correlation_limit` (S21b/
  #883) and the user's own manual `trading_live_arm` flip (decision #43)
  remain before any real order can ever reach a live account.

- PR #885 -- S21b -- opened by self_dev_campaign against the S21b-scoped
  issue #883, landed on the **first attempt** -- no reword needed, unlike
  #874. Real diff verified via the sandbox's own clone
  (`campaign-trading-s21b`, `git diff c7f8760..584bfb8`, 2 files / 117
  lines): a new `_build_correlation_matrix` (trailing-60-day close-to-
  close Pearson correlation, reusing the injected `fetch` callable) wired
  into `run_strategy_tick` via `RiskManager.check_correlation_limit`,
  gated on `not is_close` -- matching the issue's own scoping (correlation
  is about entering new exposure, never about blocking an exit).

  Worth noting: `_build_correlation_matrix` returns a pandas `DataFrame`
  where `check_correlation_limit`'s real signature expects a
  `Dict[str, Dict[str, float]]` (`correlation_matrix.get(new_symbol,
  {}).get(existing, 0.0)`). This works -- verified directly, not assumed
  -- because both `DataFrame.get()` and `Series.get()` implement the same
  dict-like key lookup (column then row-label). Fragile but correct;
  flagged here so a future refactor of either side doesn't break the
  compatibility by accident.

  The diff included real tests this time (an improvement over S20/S21's
  zero-test PRs), with 2 real bugs in them: `broker._positions.append(
  Position(...))` -- `StubBrokerClient._positions` is a
  `Dict[str, Position]`, not a list, guaranteed `AttributeError`; and
  `test_tick_blocks_high_correlation_open` asserted `blocked_by ==
  "correlation_limit"`, but `check_correlation_limit`'s real code returns
  `blocked_by="correlation"` (checked `risk_limits.py` directly). Fixed
  both.

  **One real bug in production code, not the tests, that fixing bug #2
  exposed:** the corrected correlation test was ALSO getting blocked by
  `per_trade_risk` before ever reaching the correlation check. Traced the
  cause to S20: `risk.check_order(...)` ran unconditionally for both
  opens AND closes. That is backwards for a risk gate -- a per-trade-risk
  cap or daily-loss halt blocking a *close* could trap a losing position
  open exactly when it most needs to exit. This diff's own correlation
  check was already correctly scoped to `not is_close`; `check_order`
  wasn't, and nothing had exercised a close with a large enough
  qty*price to trigger it until this test's synthetic price data
  (~$164/share) did. Fixed by gating `check_order` the same way,
  `if risk is not None and not is_close`, and fixed the test's own
  `RiskConfig` to isolate what it's testing (a generous
  `max_per_trade_risk_pct=50.0`, matching the pattern S20's own tests
  already use).

  Full suite green: 5138 passed, 7 skipped, 0 failed (`cerebral/tests/`
  + repo-root `tests/`). PR #885 closed unmerged in favor of this
  hand-verified local landing (commit fac438a).

  **RiskManager's three original gates (per-trade %, daily-loss halt,
  max concurrent positions) plus correlation are now all wired, all
  reachable from a real dispatch tick, and all correctly scoped to opens
  only.** Nothing in TRADING.md decision #47's original scope remains
  unwired. Per the blueprint's own dependency graph, S20+S21 (both
  landed) are the only code gaps gating decision #43 -- `trading_live_arm`
  can safely go True whenever the user chooses to flip it by hand; S22
  (intraday data) is a separate, independent chain, not a prerequisite.

- PR #886 -- S22 -- landed after a sustained ~50-minute Bonsai 502 outage
  (5 identical 502s before this attempt went through -- notably longer
  than the single-retry recoveries S20/S21b saw, but it cleared on its
  own; no reword needed). Real diff verified via the sandbox's own clone
  (`campaign-trading-s22`, `git diff c2e74ae..69d4ddc`, 6 files / 141
  lines): `fetch_ohlcv` gained `interval=` (cache key + TTL both
  interval-aware), a new `AlpacaMarketDataClient` (preferred over
  yfinance per decision #39), an `interval` column on
  `StrategySpec`/`strategy_specs`, and `DEFAULT_LOOKBACK_DAYS`/Sharpe
  annualisation both became interval-derived instead of flat daily-only
  assumptions.

  **Zero tests again** (3rd time this campaign: S20, S21, now S22).
  The interval parameter threading alone broke 30 existing tests the
  moment it ran:
  1. Every test-side `fetch(symbol, start, end)` stub across the whole
     trading suite broke -- `run_strategy_tick`, `_build_correlation_
     matrix`'s callers, and `_run_gauntlet` all now call `fetch(...,
     interval=...)`, but none of the ~13 stubs across
     `test_trading_live_tick.py`, `test_plugin_scheduler.py`, and
     `test_trading_compose.py` accepted it -- guaranteed `TypeError` on
     every one. Fixed all of them to accept `interval="1d"`.
  2. `_cache_path` gained a required positional `interval` param with no
     default, breaking 2 more tests in `test_trading_data.py`. Gave it
     `interval: str = "1d"` -- minimal and backward-compatible; every
     real call site in the diff already passes it explicitly.

  **Two real production bugs, not just test breakage** -- both
  silently-wrong-number bugs, the same class this campaign's
  hand-verification exists to catch:
  3. `AlpacaMarketDataClient.get_bars`'s interval map sent `"5m"`/
     `"15m"`/`"30m"` all to `TimeFrame.Minute` and `"4h"` to
     `TimeFrame.Hour` -- both fixed 1-unit constants (`TimeFrame.Minute
     == "1Min"`, verified directly), so every one of those four
     intervals would have silently fetched the base 1-minute or 1-hour
     bars regardless of what was actually asked for. Fixed with
     `TimeFrame(amount, unit)` via `TimeFrameUnit`, giving each interval
     its own real construction. New `test_trading_market_data.py` (3
     tests, fake Alpaca client, no network) asserts the exact
     `TimeFrame.value` string for all 9 supported intervals.
  4. `gauntlet.py`'s `_bars_per_year` bucketed `"1h"`/`"4h"` onto one
     shared formula and `"5m"`/`"15m"`/`"30m"` onto another -- a 4h
     strategy's Sharpe would have used the same annualisation factor as
     a 1h one (~4x too large); a 30m strategy the same factor as 5m
     (~6x too large). Sharpe feeds graduation decisions, so this is the
     same fabricated/miscomputed-number failure class this campaign has
     caught in the money-safety path before (S6's rolling-CI bug, S10's
     P&L math), just in the annualisation step this time. Rewrote to
     derive bars-per-day per-interval from its own numeric prefix rather
     than a lookup table with shared buckets. New `TestBarsPerYear` (4
     tests) asserts 1h≠4h and 5m≠15m≠30m are all numerically distinct
     and match the real formula.

  Full suite green: 5145 passed, 7 skipped, 0 failed (`cerebral/tests/`
  + repo-root `tests/`). This slice does not touch `tray/`, so no jest
  run was needed. PR #886 closed unmerged in favor of this
  hand-verified local landing (commit 6e365f8).

- PR #887 -- S23 -- landed on the first attempt, real diff verified via
  the sandbox's own clone (`campaign-trading-s23`, `git diff
  ba3d315..aba368b`, 5 files / 63 lines): `ForwardRecord.compute_
  expectancy_ci`/`compute_live_expectancy_ci` now return a 6-tuple
  (`mean, lower, upper, is_sufficient, trade_count, distinct_days`)
  instead of 4, a new `get_distinct_days()`, `check_graduation` unpacks
  the new shape, a `distinct_days_floor` SettingsStore key (default 30),
  and the Trading panel surfaces both numbers.

  **The diff's own new test was broken in a way worth naming, not just a
  typo:** `monkeypatch.setattr("cerebral.trading.forward_record.
  datetime", dt)` where `dt` is the `datetime` *module* -- but
  `forward_record.py` does `from datetime import datetime`, so
  `forward_record.datetime` is the `datetime` *class*, not the module.
  Patching it to the module broke every other `datetime.now()` call in
  the file. Rewrote using a small `_add_fill_on_day` helper that inserts
  a fill with a controlled timestamp directly via SQL -- `add_fill()`
  itself always stamps "now", with no way to inject a date through the
  public API -- cleaner than monkeypatching a stdlib class, and the same
  technique the fix needed in 3 more places.

  **Breaking the tuple size broke 6 pre-existing tests the diff never
  touched:** `test_zero_trades_ci`/`test_compute_expectancy_ci`
  (`ForwardRecord`'s own suite) unpacked 4 values; 3 mocked
  `compute_expectancy_ci.return_value` tuples in
  `test_trading_lifecycle.py` were still 4-tuples, raising `ValueError`
  on `check_graduation`'s new 6-value unpack; and 2 of S20's own
  dispatch-level tests asserted graduation on 30 fills recorded at the
  same instant -- correctly no longer sufficient now that they land on
  one calendar day. Fixed all 6, and rewrote the two dispatch-level ones
  to spread fills across 30 real distinct days, exercising the new floor
  through the *full* dispatch path -- a real gap the diff's own test
  never checked (it only exercised `ForwardRecord` in isolation, never
  `check_graduation`/`dispatch_due_events` together).

  **One production-coupling smell, not a crash but worth fixing:** both
  CI methods constructed a fresh `SettingsStore()` internally on every
  call -- with no path argument, that defaults to the *real production*
  settings file, so every CI computation (including from an isolated
  `tmp_path`-scoped test) read/touched the live `felix-settings.json`
  Cerebral's own running process might be concurrently writing to. Added
  an optional `floor: Optional[int] = None` parameter to both methods,
  matching this campaign's established DI convention (`fetch=`/`store=`/
  `risk=`/`alert_dispatcher=` are all injected the same way).

  Also fixed the now-stale `test_settings.py::test_all_returns_all_keys`
  expected-key set (missing `distinct_days_floor`) -- the same recurring
  gap this campaign hit at S20 and S21.

  Full suite green: 5147 passed, 7 skipped, 0 failed (`cerebral/tests/`
  + repo-root `tests/`); tray JS: 29 suites, 761 tests (this slice
  touches `tray/lib/trading-panel.js`, so the jest run was required).
  PR #887 closed unmerged in favor of this hand-verified local landing
  (commit 1f7344a).

- PR #888 -- S24 -- opened by self_dev_campaign, closed unmerged after a
  hand-rewrite -- the substance wasn't salvageable, unlike every prior
  slice this campaign. Real diff verified via the sandbox's own clone
  (`campaign-trading-s24`, `git diff 422d606..f4f8d25`, 2 files / 442
  lines): a new `plugins/stocks.py` and its test file.

  **The plugin as written could never have loaded, on top of a broken
  import.** Two separate, compounding structural problems, both
  confirmed by tracing the real loader
  (`cerebral/mcp/orchestrator.py::_load_plugin_file`), not assumed:
  1. `from plugins.http_client import get as http_get` -- `http_client.
     py` has no `get()` function; it exposes a private async
     `_default_fetch(method, url, ...)` and an `HttpClientPlugin`
     class. This is what actually produced the reported `tests_failed`
     (an `ImportError` on collection).
  2. **Fixing the import alone would not have made this loadable.**
     `list_tools()` returned `List[str]` instead of `list[Tool]`,
     `create(ctx: Any)` required a positional argument, and there was
     no `call_tool` method or plugin class at all. The real loader
     calls `module.create()` with **zero arguments** (confirmed reading
     the call site directly), then calls `plugin.list_tools()`
     expecting `Tool` objects with `.name`/`.description`. `create(ctx)`
     would have raised `TypeError` the instant real plugin discovery
     reached this file -- issue #877 explicitly said "following
     plugins/markets.py's exact shape," but the diff didn't follow it.

  Two more real, not just structural, bugs:
  3. `_sec_filings`'s CIK/filing lookup was built on the legacy
     `browse-edgar` XML endpoint with fragile manual parsing -- the
     diff's own inline comments admitted the uncertainty ("EDGAR index
     XML structure varies", "not always present for URL"). Rewrote
     against SEC's real JSON APIs: the static ticker->CIK map at
     `sec.gov/files/company_tickers.json` and the per-company
     submissions JSON at `data.sec.gov/submissions/CIK{cik}.json`,
     which lists every recent filing's form/accession/date/
     primaryDocument directly -- no XML guessing needed.
  4. `sec_new_filings` fetched a `.bz2`-compressed EDGAR index, but
     `_default_fetch` always decodes JSON or text, never raw bytes --
     decoding a bz2 binary as text would have corrupted it before
     decompression could run. Switched to EDGAR's plain-text `.idx`
     daily index (`daily-index/{year}/QTR{q}/master.{date}.idx`,
     pipe-delimited, no compression), which fetches cleanly through the
     existing text HTTP path. Also, `ctx.notify(...)` doesn't exist
     anywhere in this codebase (grepped directly) -- replaced with an
     optional `notify_fn:` constructor parameter, matching this
     campaign's DI convention for every other cross-boundary seam.

  Rewrote `plugins/stocks.py` as a proper `StocksPlugin` class around
  the reusable pieces (yfinance fundamentals, IPO-form filtering), and
  `test_plugin_stocks.py` from scratch against the real shape (11
  tests) -- plus a direct load-through-the-real-discovery-mechanism
  check confirming zero registration errors, which the original diff
  would have failed. Full suite green: 5162 passed, 7 skipped, 0 failed
  (`cerebral/tests/` + repo-root `tests/`). This slice does not touch
  `tray/`, so no jest run was needed. Landed as commit 9efa94b.

- PR #889 -- S25 -- the first slice this campaign since S3/S8 to
  auto-merge and have local master genuinely stay in sync automatically
  (`self_dev`'s `_run` now does a `pull origin master --ff-only` after a
  successful merge -- confirmed via `git reflog`, a real fix for the
  gap S8's postmortem flagged). Given the stakes (a live SQLite schema
  migration touching every existing user's `strategy_specs.db`), did
  NOT just trust `merge_decision: auto_merge` -- hand-verified the
  actual migration by building a database with the pre-S25 DDL (CHECK
  constraint, one real pre-existing row) and running the real
  `StrategyStore` against it directly:
  - The CHECK constraint is genuinely gone from the new DDL.
  - The pre-existing row survived the rebuild intact.
  - `save(origin="discovered")` -- an `IntegrityError` against the old
    schema -- now succeeds.
  - A second open against the same (already-migrated) file (simulating
    a restart) is idempotent: no re-migration, no duplicated rows.
  - `save()` now validates `origin` in Python and raises a readable
    `ValueError` for a bad value, instead of relying on the dropped
    SQL CHECK.
  - `render_provenance` gained the `'discovered'` branch the blueprint
    named -- an unrecognized origin no longer silently erases that the
    strategy was ever marked `'discovered'`.
  - The #38 sandbox-batching sub-decision was correctly left
    unimplemented, exactly as the blueprint recommended -- only a
    docstring was added to `sandboxed_eval.py` explaining why no
    batching exists, no functional change.

  The diff itself shipped **zero tests**, despite the blueprint's own
  explicit warning about this exact failure mode: "a fresh tmp_path
  test suite would go entirely green while the user's real
  `strategy_specs.db` raises `IntegrityError`... the regression test
  must run against a DB created with the old DDL, not a fresh one, or
  it proves nothing." Added the 4 old-schema-DB migration tests the
  blueprint asked for, plus origin validation and the new
  `render_provenance` branch, and fixed one now-stale test comment
  claiming a `strategy_versions` row "always has SOME origin per the
  CHECK constraint" -- no longer true post-migration. Full suite green:
  5168 passed, 7 skipped, 0 failed (`cerebral/tests/` + repo-root
  `tests/`). This slice does not touch `tray/`. Landed as commit
  12a88c9, directly on top of the genuinely-auto-merged 112e99f.

- PR #890 -- S26 -- opened by self_dev_campaign, auto-merged (commit
  742acad) -- but the real diff (verified directly, not trusted from
  `merge_decision`) was only 2 files / 76 lines: a bare `activity-log.js`
  render stub and its test. None of #879's actual acceptance criteria
  were met -- no `list_activity` query layer, no `KIND_ACTIVITY`, no
  retrofit of `_scheduler_loop`'s console-only logging, no `self_dev.py`
  extension, no dedicated thread, no nav-tab wiring in
  `sidebar-router.js`/`main.html`, no Trading pane Activity section.
  The render stub itself also cached its DOM query
  (`doc.querySelector('[data-route="log"]')`) once at module-load time
  -- a permanent no-op if the Log pane didn't exist in the DOM yet when
  the script first loaded (which it never did, since nothing wired the
  pane into `main.html` either).

  **Given S27 (the autonomous discovery loop) explicitly depends on the
  Activity Log actually working (decision #46: "the log has to work
  before the loop that fills it starts running"), built the rest by
  hand rather than advance the queue on a non-functional log:**
  - `ConversationStore.list_activity(profile_id, *, kinds=, since=,
    limit=)` -- kind-filtered, time-ranged, cross-thread. Filters on the
    plaintext, indexed `kind` column only (`content_json` is
    Fernet-encrypted; a `LIKE` against it cannot work at all --
    `search_threads` already has that exact latent bug, not repeated
    here). New `KIND_ACTIVITY = "activity"`.
  - A dedicated `get_or_create_activity_thread` ("Autonomous activity"),
    resolved the same lookup-or-create-by-title way
    `_LEGACY_THREAD_TITLE` already is -- so autonomous entries never
    interleave into whatever chat thread the user last had open.
  - **A real cross-cutting bug this surfaced, not just a missing
    feature:** every `append()` bumps its thread's `updated_at`, so a
    background loop writing to the Activity thread would make it the
    profile's "most recently updated" thread -- and both
    `get_or_create_default_thread` and `main.py`'s own active-chat-
    thread resolution pick the most-recently-updated thread as the
    default. Without excluding it, the user's very next real chat
    message could have silently landed in the Activity thread instead
    of a new conversation. Fixed by excluding
    `_ACTIVITY_THREAD_TITLE` from `latest_thread()`'s candidate pool --
    verified directly (built a case that reproduced the pollution
    before the fix, confirmed clean after).
  - `_scheduler_loop` retrofit: skips dispatch entirely (not just the
    logging) while no profile is active ("Felix does not trade when it
    cannot record that it traded," decision #46 sub-decision 4), and
    persists one batched summary activity row per dispatch pass -- not
    one per strategy -- naming notable outcomes (opened/closed/blocked/
    graduated) inline rather than hiding them in an undifferentiated
    count.
  - `plugins/self_dev.py` gained a parallel `record_activity_fn` seam
    (same shape as the existing `record_turn_fn`, same fail-open
    default) -- kept genuinely separate rather than repointing the
    existing seam, since `record_turn_fn` writes into the user's
    *active* chat thread for the #810 pending-review card (an inline
    actionable UI element that belongs where the user is looking),
    while `record_activity_fn` writes into the dedicated Activity
    thread instead -- two different destinations for the same event,
    confirmed by tracing what actually reads the existing calls
    (`self-dev-card.js`) before touching them. Both existing call sites
    (a PR needing human review, a manual rollback) now fire both seams.
  - UI: `sidebar-router.js`'s `VALID_ROUTES` gained `'log'` (6th nav
    section), a `<button data-route="log">` + `<section data-route="log"
    hidden>` in `main.html`, a real `activity-log.js` (DOM lookups
    happen fresh on every render call, not cached at load time --
    matching `trading-panel.js`'s own established convention, which the
    original stub violated), a new `activity_poll` -> `activity_log_data`
    IPC round-trip (`cerebral/main.py::_handle_activity_poll`, filtering
    a `source` sub-scope in Python *after* the kind filter, per
    sub-decision 3 -- never a second plaintext DB column), and the
    Trading pane's own filtered Activity section
    (`source: "trading"`, server-side filtered).

  A real `_active_profile is None` guard was also added inside
  `_handle_activity_poll` and `_record_activity` themselves (belt-and-
  suspenders under the loop-level skip above).

  **A genuine mistake made and caught during hand-verification, worth
  recording:** the first two ad-hoc verification scripts against
  `ConversationStore` reassigned the module's `DB_PATH` global expecting
  it to redirect a fresh `ConversationStore()` to a temp file --
  `__init__(self, db_path: Path = DB_PATH)` binds that default at
  function-*definition* time, so the reassignment was silently a no-op,
  and both scripts wrote real test rows (4 turns, 2 threads) into the
  actual running Felix's production `openmind.db` under profile_id=1.
  Caught by inspecting the DB directly before trusting the output;
  the exact rows (ids 14852-14855, threads 15-16, all with matching
  recent timestamps and no other profile-1 activity mixed in) were
  identified and deleted, verified back to the pre-contamination
  max-id state. All subsequent verification passed `db_path=` explicitly,
  matching this repo's own test fixture convention. Logged to
  `.learnings/LEARNINGS.md` so this doesn't repeat.

  New tests: 6 `ConversationStore` tests (activity-thread stability,
  pollution in both directions, kind filtering, profile scoping, `since`
  filtering against real second-precision timestamps), 3 `self_dev.py`
  tests (both real event sites recording an activity entry, survival on
  `record_activity_fn` failure), 9 `activity-log.js` tests (including
  one that directly proves the fresh-lookup-per-render-call fix, using
  two different fake mounts across two calls), plus `VALID_ROUTES`/
  `render-smoke` count updates (6 nav sections now, matching the exact
  "hardcoded tab count breaks on the Nth addition" class this campaign
  hit at S9 and S17). Full suite green: 5178 passed, 7 skipped, 0 failed
  (`cerebral/tests/` + repo-root `tests/`); tray JS: 30 suites, 772
  tests. Landed as commit c1b2b64, directly on top of the auto-merged
  742acad.

- PR #891 -- S27 -- opened by self_dev_campaign, closed unmerged after a
  rebuild -- like S24, the substance wasn't salvageable. Real diff
  verified via the sandbox's own clone (`campaign-trading-s27`, `git
  diff 71d3e70..4aad5ec`, 2 files / 262 lines): a new
  `cerebral/trading/discovery.py` and its test.

  **Every external call in the diff referenced a nonexistent function or
  the wrong signature, none of it checked against the real code:**
  1. `from cerebral.trading_ideas import Idea, judge_idea` --
     `judge_idea` doesn't exist anywhere in `trading_ideas.py`. This is
     the actual `ImportError` behind the reported `tests_failed`.
  2. `from plugins.stocks import get_fundamentals` -- S24's real plugin
     exposes `stock_fundamentals` as a tool reachable via
     `StocksPlugin().call_tool(...)`, not a standalone function.
  3. `from plugins.scheduler import _run_gauntlet` -- `_run_gauntlet` is
     a private *method* on `SchedulerPlugin` (confirmed reading the
     class directly), not a module-level function; this import alone
     fails.
  4. `_dispatch_to_gauntlet` called `_run_gauntlet(claim=..., url=...,
     origin='discovered', ticker=...)` -- the real signature is
     `async def _run_gauntlet(self, args: dict, *, ..., origin=
     "generated", ...)`, one positional `args` dict, not those kwargs
     directly. Would have raised `TypeError` on first real use,
     independent of bug #3.
  5. The whole trigger function was synchronous but called into
     `_run_gauntlet` (async) and Playwright-backed browser tools (also
     async) -- a fundamental async/sync mismatch on top of everything
     else.

  **Rebuilt from the real issue #880 acceptance criteria**, checking
  every real shape first (`plugins/browser.py`'s `web_search`/`navigate`
  via `BrowserPlugin.call_tool`, the actual `_run_gauntlet(args, *,
  origin=...)`, `trading_ideas.py`'s real `Idea`/`to_strategy` pattern):
  - `cerebral/trading/discovery.py`: pure and duck-typed (`Discovery
    Watchlist` SQLite-backed, `extract_ticker`, `process_idea`,
    `run_discovery_pass`) -- **no `plugins/` imports**, matching
    `live_tick.py`'s own "cerebral/ must not import plugins/" layering
    rule, which the original diff violated by importing
    `plugins.browser`/`plugins.stocks`/`plugins.scheduler` directly
    from inside `cerebral/trading/`.
  - `judge_idea(idea, llm=None, router=None) -> (bool, reason)` added
    to `trading_ideas.py`, matching `to_strategy`'s own router /
    conservative-continue pattern exactly.
  - `plugins/scheduler.py`: `web_search_fn`/`record_activity_fn`
    injection seams, `_source_ideas` (real `BrowserPlugin.call_tool`
    by default, an injected fake in every test), `_run_discovery`
    (sources -> screens -> dispatches to the real
    `self._run_gauntlet(..., origin="discovered")`, decision #33's
    single unchanged convergence point), a `run_discovery` tool, and
    `ensure_discovery_event()`/`DISCOVERY_EVENT_TITLE` for cadence.
  - `cerebral/main.py`: the discovery event is checked and consumed in
    `_scheduler_loop` **before** `dispatch_due_events` gets its own
    `list_due_events()` call -- both read the same `events` table, so
    without this ordering the per-strategy dispatcher would have
    mistaken the discovery event for a strategy to run.

  **One real isolation risk caught before it repeated S26's
  contamination mistake:** `SchedulerPlugin`'s new `DiscoveryWatchlist`
  default would have used the real production `discovery_watchlist.db`
  for every test constructing `SchedulerPlugin(db_path=tmp_path/...)`
  without *also* knowing to override the watchlist separately. Fixed
  by deriving the watchlist's path from the scheduler's own `db_path`
  directory (handling `:memory:` explicitly) -- every existing isolated
  test gets an isolated watchlist for free, no test file needed to
  change.

  Verified all 4 acceptance criteria directly against the real
  `_run_gauntlet` (never mocked, matching the discipline S11c's
  postmortem established): a ticker-specific idea reaches it with
  `origin='discovered'` and the source URL in `provenance_json`,
  skipping `judge_idea` entirely; a pattern-general idea is judged
  first and only its watchlist-matched candidates dispatch; a rejected
  idea never reaches `run_gauntlet` (asserted via a `fetch` that raises
  if ever called); both outcomes produce a real Activity Log entry.
  21 new tests (15 `discovery.py` unit tests, 3 `judge_idea` tests, 8
  scheduler-level integration tests using the real compiled
  `MA_CROSS_CODE` fixture). Full suite green: 5205 passed, 7 skipped,
  0 failed (`cerebral/tests/` + repo-root `tests/`). This slice does
  not touch `tray/`, so no jest run was needed. Landed as commit
  3fec2cb.

- S28 -- no self_dev PR -- 5 consecutive identical "Edit step produced
  no commit" failures, each confirmed genuine (the sandbox clone's own
  `git log` stayed at master's real tip every time, never a stale
  ledger replay -- a no-commit failure is never persisted, so every
  retry re-invoked the model fresh). Matching the S17 precedent's exact
  threshold, but given every recent slice (S24, S26, S27) needed either
  substantial hand-completion or a full rebuild regardless of what
  self_dev produced, and this is the campaign's final slice, designed
  and landed it directly instead of spending a 6th cycle on an
  uncertain attempt.

  `StrategyLifecycle.check_graduation` gained optional `symbol=`/
  `latest_accession_fn=`/`fundamentals_scan_fn=`/`vetted_tickers=`
  params -- entirely backward compatible, every pre-existing caller/
  test unaffected when unset. For a never-before-traded ticker, right
  at the paper->live moment (after the CI test passes, before status
  flips to `"live"`), pulls the latest 10-Q/10-K accession via S24's
  `sec_filings`, LLM-scans it for red-flag language (going concern,
  restatement, investigation, delisting), and refuses the promotion
  with a critical `StructuredAlert` on a hit. A new `VettedTickers`
  store (`cerebral/trading/discovery.py`, keyed by symbol + accession
  number, same SQLite-backed injectable-`db_path` pattern as
  `DiscoveryWatchlist`) remembers the verdict so an already-vetted
  ticker skips the SEC/LLM call entirely on the SAME filing, while a
  genuinely new filing re-triggers the scan. Threaded through
  `dispatch_due_events` -> `_apply_lifecycle` so the real dispatch
  chain exercises it, not just `check_graduation` in isolation --
  `result.get("symbol")` from the dispatch result flows straight
  through, no separate strategy-store lookup needed. Fails **closed**
  (refuses promotion) on any fetch/LLM failure, deliberately unlike
  `judge_idea`'s fail-open default -- this gates real capital risk, an
  inconclusive scan must not silently let a promotion through.

  25 new tests: 12 `check_graduation` tests (red-flagged / clean /
  no-symbol-is-a-no-op / cached-clean / cached-red-flagged / new-filing
  re-scans / no-filing-found-is-conservative / gate-skipped-when-CI-
  itself-fails), 5 `VettedTickers` unit tests, plus 1 dispatch-chain
  integration test proving the new params actually thread all the way
  through `dispatch_due_events` -- matching this campaign's own
  precedent (S20's risk gate, S21b's correlation gate) of never trusting
  an isolated-method test alone for a money-safety gate. Full suite
  green: 5219 passed, 7 skipped, 0 failed (`cerebral/tests/` + repo-root
  `tests/`). This slice does not touch `tray/`, so no jest run was
  needed. Landed as commit 4b3e2b7.

**The full S20-S28 blueprint (2026-08-24 autonomous-discovery + intraday-
data expansion) is complete.** Every slice landed and was hand-verified
against the real code, not trusted from a merge decision alone -- see
each entry above for what self_dev's own output actually got wrong
(9 of 9 slices needed either bug fixes, a full rebuild, or a near-diff-
level issue reword; only S25 and S27's business-logic core needed no
production-code fixes at all after hand-verification, and even those
still needed real tests added). `trading_live_arm` is safe for the user
to arm by hand whenever they choose (decision #43) -- RiskManager
enforces per-trade %, daily-loss halt, max concurrent positions, and
correlation limit on every real order, the live path fails loudly
instead of silently on a missing dependency or bad credentials, and the
Activity Log gives visibility into everything the autonomous discovery
loop does before any of it reaches a real account. A new campaign
against this driver needs a fresh grill session first.

- S29 -- #892 -- Trading pane "Tickers" sub-tab: per-ticker progress view.
  Grilled 2026-08-24 (decisions #48-#51) -- the first slice of the new
  campaign phase opened after S20-S28. Not landed via self_dev's own PR
  #893: the whole diff was 14 lines of dead CSS for a `.trd-tab` strip
  never referenced by any markup, JS, or backend -- zero of the eight
  acceptance criteria met (`tests_failed`, closed unmerged; see the PR's
  own diff for the full account). Landed by hand instead.

  Two real, disclosed scope adjustments the grill's acceptance criteria
  assumed away, found while building this for real (same Honesty-rule
  discipline as S10's "still honest gaps" list):
  1. **The pre-strategy "step tracker" (screened/judged/gauntlet-running/
     result) from decision #49 isn't buildable as written.**
     `discovery.py`'s `DiscoveryWatchlist` has no persisted per-attempt
     log -- S27's `process_idea` only ever records a "dispatched" activity
     entry, never the eventual gauntlet verdict, so a screened-but-
     strategy-less ticker cannot be told apart from one that was judged-
     and-rejected or dispatched-and-failed the gauntlet without inventing
     a status nothing actually stores. Built three honest stages instead
     (`cerebral/trading/ticker_view.py`): "screened" (in the watchlist, no
     strategy yet), "validated" (a strategy exists, zero fills so far),
     "charting" (a strategy exists with at least one fill -- the real
     chart). A watchlist symbol also never drops out of "screened" on its
     own, same reasoning -- `DiscoveryWatchlist` has no rejection/expiry
     flag either. Only a halted strategy with zero fills is dropped
     (decision #48's "nothing paper/live behind it"), since that IS real,
     queryable state (`StrategyLifecycle` status + `ForwardRecord` fills).
  2. **The buy-and-hold benchmark line needed a concrete definition the
     issue left implicit.** Reuses the gauntlet's own `vs_benchmark` gate
     (`gauntlet.py:277`: `last_close/first_close-1`) but expressed as a
     running series instead of one before/after scalar, scaled to the
     segment's own first fill's real qty and entry price -- so the
     benchmark lands in the same $ units as the strategy's own cumulative-
     PnL line (both are real dollar amounts) rather than mixing a $
     series with a fabricated "invested capital" percentage.

  Backend: `cerebral/trading/ticker_view.py`'s `build_ticker_view`/
  `build_ticker_benchmark` are pure and duck-typed (own data sources
  injected as plain callables/dicts, matching `live_tick.py`/
  `discovery.py`'s own convention) -- `cerebral/main.py`'s
  `_trading_tickers_data()` is a thin wrapper binding the real module-
  level singletons, plus a new `trading_tickers_poll` IPC handler
  broadcasting `trading_tickers_update`. Frontend: a `.trd-tabs` sub-tab
  strip on the Trading pane (Strategies/Tickers, matching Settings' own
  sub-tab pattern -- decision #51), and `tray/lib/trading-panel.js` grew
  `renderTickersUpdate`/`initTickersView` reusing the existing per-
  strategy canvas chart's drawing approach, extended to a second
  (benchmark) line, per-trade dot markers, and a shared hover tooltip
  showing the trade's strategy/side/price/PnL/timestamp -- paper and live
  segments render as separate canvases per decision #50, never one joined
  line. Hand-verified live in a real browser (not just jest, which has no
  canvas 2D context): sub-tab switching, all three stages' markup, both
  segments' dot counts, and the hover tooltip's actual content all
  confirmed against injected sample data before landing.

  15 new backend tests (`test_trading_ticker_view.py`), 5 new frontend
  tests (`trading-panel.test.js`). Full suite green: 5236 passed, 7
  skipped, 0 failed (`cerebral/tests/` + repo-root `tests/`); tray jest
  30 suites / 778 tests, 0 failed. Landed as commit 120bca6.

- S30 -- #894 -- Persisted per-attempt discovery log: gauntlet
  verdict/reasoning per candidate. Closes S29's own disclosed gap
  (decision #49): `DiscoveryWatchlist` had no per-attempt record, so a
  screened-but-strategy-less ticker couldn't be told apart from one the
  gauntlet actually rejected. Not landed via self_dev's own PR #895:
  the whole diff referenced three names (`record_attempt_fn`,
  `get_latest_attempt`, `_extract_attempt_reason`) that were never
  defined, imported, or threaded through any function signature -- a
  guaranteed `NameError` at runtime, no persisted log was added at all,
  only the pre-filter dispatch path was touched (the ticker-specific
  fast path wasn't), and the one CSS class it added was never referenced
  by any markup (`tests_failed`, same "dead CSS" failure shape as S29's
  own aborted PR #893). Landed by hand instead.

  New `cerebral/trading/discovery.py::DiscoveryAttempts` (same SQLite/
  db_path-injection convention as `VettedTickers` -- one row per symbol,
  replaced wholesale on each new attempt, only the latest matters).
  `process_idea`/`run_discovery_pass` gained an optional
  `record_attempt_fn`, called after `run_gauntlet_fn` returns on BOTH
  the ticker-specific fast path and the pre-filter loop (self_dev's
  attempt only wired the latter). `_attempt_outcome()` reads either the
  real production result shape (`{"ticker", "is_error", "result": <JSON
  string>}`) or the flat test-fake shape already used throughout
  `test_trading_discovery.py` (`{"ticker", "verdict"}`) so the existing
  fakes didn't need to change shape -- on UNVALIDATED, names the first
  failed gate and its own `details` string (e.g. "vs_benchmark:
  underperformed by 3.2%"), which required `plugins/scheduler.py`'s
  `_run_gauntlet` to actually include `details` in its gate JSON (it
  only had `name`/`passed` before, so no result reaching this module
  had a real reason string to read even if it tried). `SchedulerPlugin`
  gets a `_discovery_attempts` instance alongside `_discovery_watchlist`,
  same injection/isolation convention.

  `build_ticker_view` gained an optional `get_latest_attempt` param --
  a real "rejected" stage (with `reason`) for a ticker whose most recent
  attempt was UNVALIDATED and has no strategy yet; defaults to `None` so
  any caller not yet wired to `DiscoveryAttempts` keeps the old 3-stage
  behavior. A strategy's existence still always overrides "rejected",
  same as it did "screened" before. `cerebral/main.py`'s
  `_trading_tickers_data()` wires the real
  `_discovery_attempts.get_latest`. `tray/lib/trading-panel.js` shows a
  red "Rejected" badge + the reason text on the Tickers card in place of
  the generic "Screened -- no strategy yet." text. Deliberately did NOT
  add a live "gauntlet-running" status (#894's own scope note): a
  dispatch is one awaited call within one discovery pass tick, no
  meaningful in-progress window exists to poll for.

  18 new backend tests (`test_trading_discovery.py`,
  `test_trading_ticker_view.py`, `test_plugin_scheduler.py`), 1 new
  frontend test (`trading-panel.test.js`). Full suite green: 5258
  passed, 7 skipped (`cerebral/tests/` + repo-root `tests/`) -- one
  unrelated pre-existing failure on unmodified files, see
  `.learnings/ERRORS.md`'s 2026-08-25 entry (a `git checkout master --
  .` run to discard #895's diff accidentally reverted separate
  uncommitted WIP on `cerebral/mcp/orchestrator.py` and its test, not
  caused by this slice); tray jest 30 suites / 780 tests, 0 failed.
  Landed as commit 4e81904.

- S31 -- #896 -- Manual discovery start/stop with duration + market-hours
  gate for paper dispatch. Filed the same day as a separate bugfix (not
  itself a queued slice, landed as commit 8fe7628): the whole autonomous
  scheduler loop had been silently dead since it was first written --
  `events` table schema drift (the real production `openmind.db` predated
  the `last_run_iso` column `CREATE TABLE IF NOT EXISTS` never adds to an
  existing table) plus a tz-aware timestamp comparison crash in
  `list_due_events` (the discovery event's own `start_iso` is offset-
  suffixed, `now` deliberately isn't) -- both caught every 5 minutes by
  `_scheduler_loop`'s broad `except Exception`, logged as a warning, never
  surfaced. With that fixed, discovery would have become a fully-automatic
  always-on process with zero user control -- this slice is what the user
  actually asked for once they knew that: direct control over when
  discovery runs and for how long.

  Not landed via self_dev's own PR #897 -- worse than S29/S30's own
  aborted attempts, not just non-functional: `cerebral/main.py` came back
  with a genuine `SyntaxError` (an unterminated docstring left over from a
  bad find-replace, confirmed with `py_compile` -- the module didn't even
  import), the real `_dispatch_due_events` call was deleted entirely and
  replaced with a `pass` stub (would have silently disabled ALL paper
  trading even with the syntax fixed), `self._settings` was referenced
  everywhere but never assigned in `__init__` (guaranteed
  `AttributeError`), and `main.py`'s new code called `_parse_iso` without
  ever importing it (`NameError`). Landed by hand instead.

  `cerebral/settings.py` gained `discovery_enabled` (default **False** --
  discovery does not run on its own until explicitly started, a real
  behavior change from what the bugfix alone would have produced),
  `discovery_stop_at`, `discovery_queries`, `discovery_interval` -- same
  `SettingsStore` convention as `trading_live_arm`. `plugins/scheduler.py`
  gained a `settings` injection seam (same isolation convention as
  `discovery_watchlist`/`discovery_attempts` -- a `tmp_path`-scoped test
  plugin gets its own `felix-settings.json`, never the real one) and three
  new tools: `start_discovery` (optional `queries`/`interval`/
  `duration_hours` -- empty `queries`/`interval` leave the existing stored
  value alone rather than silently resetting to the built-in defaults,
  per this issue's own scope note), `stop_discovery`,
  `get_discovery_status`. `cerebral/main.py` binds the real `_settings`
  singleton onto `_scheduler_plugin` explicitly (two separate
  `SettingsStore` instances over the same JSON file would silently drift
  out of sync otherwise -- `_load()` only runs once at construction).
  `_scheduler_loop`'s discovery block now checks `discovery_enabled`
  first, auto-stops on an expired `discovery_stop_at` (logged as an
  activity entry, not silent), and passes settings-backed
  `queries`/`interval` into `run_discovery` instead of a hardcoded `{}`.

  New `cerebral/trading/market_hours.py::is_market_hours(now=None)` --
  pure, stdlib `zoneinfo` only (decision #22, free only), Mon-Fri
  09:30-16:00 America/New_York, `now=` injectable so tests use a fake
  clock instead of waiting for or faking the real one. Disclosed,
  deliberate gap: no market-holiday calendar (Thanksgiving/Christmas/etc.
  will incorrectly read as open) -- this gate's job is just "not literally
  24/7," which is what was actually asked for; a real NYSE calendar is a
  separate slice if it ever matters. Gates the real `_dispatch_due_events`
  call in `_scheduler_loop` -- kept intact, not touched otherwise.

  `tray/lib/trading-panel.js` gained a Discovery control (Start/Stop +
  optional duration-hours field) on the Trading pane, rendered on BOTH the
  populated-strategies and empty-positions branches -- discovery is
  independent of whether any strategy exists yet, so it must not disappear
  behind the "No active strategies" message. Found and fixed the same
  class of bug that would have caused: style injection previously only
  ran on the non-empty branch (harmless before, since that branch had no
  styled markup of its own -- but the discovery control now renders on
  both, so styles were hoisted to run unconditionally). Reuses the
  existing generic `call_tool` WS route, same as the create-strategy form
  and S17's edit box -- no bespoke IPC route, and a `trading_poll`
  re-send after each click refreshes the panel immediately rather than
  waiting for the next natural broadcast. `start_discovery`/
  `stop_discovery` being registered tools means chat/voice control
  ("search for day trading strategies for the next 2 hours") needed no
  separate wiring at all -- it already routes through the normal planner
  path every other tool does.

  24 new backend tests (`test_plugin_scheduler.py`,
  `test_trading_market_hours.py`, `test_settings.py`), 9 new frontend
  tests (`trading-panel.test.js`), plus a real regression fix in
  `test_settings.py` (an existing test hardcoded the full settings key
  set -- caught by running the FULL suite, not just this slice's own new
  tests, same discipline this campaign has followed throughout). Full
  suite green: 5284 passed, 7 skipped, one pre-existing unrelated failure
  (`test_plugins_time_notes.py`, tied to separately-lost WIP -- see
  `.learnings/ERRORS.md`'s 2026-08-25 entry); tray jest 30 suites / 788
  tests, 0 failed. Landed as commit 5df740c.

## What's next

The S13-S19 blueprint is code-complete and hand-verified, the same way
every slice in this campaign has been -- passing tests were never
treated as sufficient on their own, and that discipline found a real,
often severe bug in every single one of S17, S18, and S19 that a
passing (or, for S17/S18's `tests_failed` cases, a *reasonable-looking*
failing) test suite did not catch by itself. What is genuinely true
right now:

- A strategy's source can be edited (`edit_strategy`) with a real new
  version, a real full gauntlet re-run, and a dispatch pointer that only
  moves on VALIDATED.
- Multiple validated strategies can be mixed (`mix_strategies`) into a
  real composite that runs through the identical sandbox/gauntlet/
  dispatch path as any other strategy, with real, readable-back lineage
  naming every component.
- The Trading panel shows every tracked strategy, its real version and
  provenance, its source, and an edit box that round-trips through the
  real `edit_strategy` tool end to end.

What is still NOT true, same as every prior "landed" milestone in this
campaign -- do not read "the blueprint is complete" as "ready":

- **No real strategy has been submitted through any of this in
  production.** Every chain above is exercised by tests (now including
  real, unmocked ones for S17/S18/S19's own new surfaces) -- nothing in
  the live app has actually called `edit_strategy`/`mix_strategies` with
  real user intent yet.
- **The arm toggle has never been set to True.** Live capital is not
  and has never been at risk anywhere in this campaign. That posture is
  unchanged by S13-S19.
- **`render_provenance`'s `mixed` branch embeds a raw JSON string**,
  not a formatted one -- functionally satisfies "names every component"
  (confirmed by the S18 end-to-end test) but isn't pretty. Low priority,
  cosmetic only.
- **No `get_version(strategy_id, n)` reader exists** -- only
  `get_current_version`. S19's panel shows the CURRENT version's
  provenance/code for every tracked strategy, even one whose particular
  `StrategyState` entry (keyed by an older `"<id>@v<n>"`) represents a
  now-superseded version. Acceptable for what the acceptance criteria
  actually asked for; would need a real reader to show historical
  versions accurately.
- **Operational, not code, is the real remaining step**: run a real
  strategy through paper trading for real, over real time, watch an
  edit or a mix actually go through the panel with a human driving it --
  the standing next step this campaign's own notes have repeated since
  S12, still true.

## Thesis

The prompt-to-code step is commoditized; any model can turn "buy when RSI crosses 30"
into a strategy. The only part worth building is the **validation layer** that decides
whether a strategy is a real edge or an expensive illusion, plus the discipline that
keeps an unvalidated one away from real capital.

Felix is an **autonomous trading agent**: research, validation, and execution in one
pipeline. The deliverable is *evidence about a strategy* that Felix also acts on.

## Decisions (grill record)

| # | Item | Decision |
|---|---|---|
| 1 | Purpose ceiling | Full autonomous agent -- research + validation + execution, no human confirmation gate |
| 2 | Universe & horizon | All asset classes; **penny stocks first** |
| 3 | Idea source priority | Books, papers, and web content primary (user provides URLs, Felix crawls + follows links); user prose alongside; 82-book list is a wishlist (not yet acquired) |
| 4 | Data source | yfinance (free, permanent); survivorship bias on penny stocks is a known accepted limitation |
| 5 | Strategy representation | Python functions: `def strategy(data) -> signals` |
| 6 | Gate thresholds | Defaults, user-configurable (see gauntlet section) |
| 7 | Minimum trade count | 30 trades before a forward record is considered meaningful |
| 8 | Broker | Alpaca -- paper and live as separate API keys via keyring |
| 9 | Risk limits | 2% per trade, 6% daily loss, 10 max concurrent -- user-configurable |
| 10 | Failure behaviour | Conservative-continue across all modes (see section) |
| 11 | Anti-goals | See section |
| 12 | S1 split | S1a (data) and S1b (backtest engine) are separate slices |
| 13 | Cost model timing | S1b has no cost model; costs arrive in S2 |
| 14 | S4 shape | URL/web/book -> strategy spec (user provides URLs, Felix follows links within) |
| 15 | Paper auto-start | Automatic on gauntlet pass |
| 16 | Live graduation | Automatic with position-size ramp: 25% -> 50% -> 100% |
| 17 | Panel approach | Incremental -- each slice adds its view; no standalone panel slice |
| 18 | Broker slice | S5a -- dedicated Alpaca integration slice before paper trading |
| 19 | Risk/failure slice | S5b -- risk limits + failure behaviour tested on paper before live |
| 20 | Phasing | Sequential -- S4 stays after gauntlet |
| 21 | Settings | All thresholds, limits, and gate parameters are user-configurable |
| 22 | Free only | No paid data sources, no paid models, free commissions |
| 23 | Sandbox fix (2026-08-23) | AppContainer sandbox can't load Python at all (spiked, confirmed) -- user chose an icacls grant of AppContainer read access to the Python install dir, over bundling a separate Python or falling back to a weaker sandbox tier |
| 24 | Sandbox transport (2026-08-23) | Bars via CSV, signals via a JSON file in the workdir (not stdout -- truncates at 30k chars) |
| 25 | Sandbox cadence (2026-08-23) | Runs on every dispatch tick, not just once at validation -- caching a compiled callable would reintroduce in-process exec |
| 26 | LLM wiring (2026-08-23) | to_strategy reuses task_type="coding" via the existing router -- no new task_type, no paid dependency; added as its own prerequisite slice (S15) since every strategy generated so far was silently the stub |
| 27 | Edit + live history (2026-08-23) | An edit to a live strategy restarts clean (forward record scoped to strategy_id@version) -- user confirmed over carrying history across an edit, per the Honesty rule |
| 28 | Mix modes (2026-08-23) | unanimous + majority only for v1 -- no weighted/threshold mode until a real use case needs it (YAGNI) |
| 29 | Mix symbol constraint (2026-08-23) | All components of a mix must share the same symbol -- reject otherwise |
| 30 | Edit/mix UI surface (2026-08-23) | A plain textarea in the Trading panel, not the Documents library (ADR-0011) -- wrong medium (docx vs Python source), wrong editor |
| 31 | Build order (2026-08-23) | Sandbox (S13-S14) before edit/mix (S17-S18) -- contain the untrusted-code risk before more code flows through it, at the cost of edit/mix landing later |
| 32 | Autonomous discovery scope (2026-08-24) | **Full** autonomous idea sourcing (web search + navigate, already-built browser plugin tools) and universe screening -- reverses anti-goal above. User explicitly wants this, understands the risk. |
| 33 | Discovery pipeline shape (2026-08-24) | One shared convergence path (symbol+hypothesis+code -> run_gauntlet, unchanged) with multiple entry points/triggers (ticker-specific ideas skip screening; pattern-general ideas go through the cheap universe pre-filter first) -- not two separate pipelines |
| 34 | Idea discovery tools (2026-08-24) | Reuse `plugins/browser.py`'s existing `web_search` (Playwright/OpenClaw) + `navigate` -- no new crawling plugin |
| 35 | New stocks plugin (2026-08-24) | `plugins/stocks.py`: yfinance fundamentals (`.info`/`quarterly_financials`, zero new dependency) + SEC EDGAR (10-Q/10-K text, S-1/424B4 for IPO detection -- free, no key, 10 req/s limit) |
| 36 | Ticker universe (2026-08-24) | A growing watchlist seeded by real sourced signals, not a periodic full sweep of the ~15-20k US+OTC universe -- full-universe sweep stays technically available (~15-60 min with batching) but isn't the default cadence |
| 37 | IPO handling (2026-08-24) | Notification-only, via SEC EDGAR's daily filing index (S-1/424B4) -- user decides whether to trade manually with their own IPO-specific strategy; not auto-gauntlet-tested |
| 38 | Screening sandbox cost (2026-08-24) | One sandbox spawn evaluates many tickers per run (batched), not one spawn per ticker -- the latter would dominate cost (~1s/spawn x thousands); needs a batch-evaluate entry point in `sandboxed_eval.py` |
| 39 | Day trading / intraday data (2026-08-24) | **The higher-priority half of this expansion.** Data source hierarchy: Alpaca Market Data primary (same account/credentials already integrated -- avoids a backtest-vendor-vs-live-vendor mismatch), Alpha Vantage backup, Playwright/`navigate` scraping as last resort |
| 40 | Bar interval (2026-08-24) | Per-strategy declared, not one fixed interval -- 5-min/15-min recommended defaults; 1-min available but noisier/more latency-sensitive/most data-constrained (7-day history cap) |
| 41 | Trade-count scaling (2026-08-24) | The flat 30-trade honesty-rule minimum scales by trades-per-session/interval, so graduation still requires enough independent trading *days*, not just enough trades crammed into one fast session |
| 42 | Discovered-strategy provenance (2026-08-24) | New `origin='discovered'` value on `strategy_versions` (alongside generated/user_edited/mixed) -- an idea Felix found itself is a different fact from one a human supplied, per the Honesty rule |
| 43 | Live capital arming (2026-08-24) | **User will arm live trading.** Mechanism: user manually flips `trading_live_arm` (existing S11b toggle, already built) -- Felix does not decide to arm itself. Once armed, trades autonomously within the already-built rails (ramp, risk limits, retirement); no new code for the arm mechanism itself |
| 44 | Idea-quality pre-filter (2026-08-24) | An LLM-judge pass (same free routed model `to_strategy` uses) rejects vague/non-testable sourced claims before they reach the (expensive) gauntlet |
| 45 | Ticker fundamentals gate (2026-08-24) | For any never-before-traded ticker: pull its latest 10-Q/10-K via the stocks plugin, LLM-scan for red-flag language (going concern, restatement, investigation, delisting) as an additional gate at paper->live graduation specifically -- not at idea-sourcing time, not blocking paper trading. Previously-vetted tickers skip re-checking |
| 46 | Activity log (2026-08-24) | Felix-wide (not trading-only), reuses `conversation_turns` + `_record_turn` -- retrofits the scheduler loop and self_dev to actually log instead of console-only `logger.info`. New top-level "Log" nav tab for the full stream; a filtered Activity section inside the Trading tab for trading-scoped entries. Routine screening batches into summary entries ("screened 500, 3 candidates"); real decisions (validated, traded, sourced) log individually |
| 47 | **P0, found during this grill, not a new feature** (2026-08-24) | `RiskManager` (2%/trade, 6%/day, correlation limit -- S5b/S6, tested, believed wired) is never actually called from `cerebral/trading/live_tick.py`'s real dispatch path -- confirmed by direct grep, zero references. Every live order today would place with **no risk-limit enforcement at all**. Must be wired + verified with a real "an over-limit order gets blocked" test before `trading_live_arm` is ever set True -- prerequisite to decision #43, not part of the autonomous-discovery/day-trading blueprint itself |
| 48 | Ticker view scope (2026-08-24) | "Currently active" tickers only -- in watchlist/screening, mid-gauntlet, or backing a paper/live strategy -- drops off once halted/rejected with nothing paper/live behind it. Avoids an unbounded historical list |
| 49 | Ticker progress display (2026-08-24) | Pre-strategy stage (watchlist/screening/gauntlet) shows a step tracker (screened -> judged -> gauntlet running -> result), no chart -- nothing to plot yet. Post-strategy stage (paper or live) shows a cumulative equity-curve overlay: the strategy's own line vs. a buy-and-hold benchmark line (same definition as the gauntlet's existing `vs_benchmark` gate, `gauntlet.py:277`), each closed trade marked as a dot; hovering a dot shows that trade's strategy/details. One card per ticker, not per strategy -- if multiple strategies target the same ticker, all their trades plot on the same chart against the one benchmark line, disambiguated on hover |
| 50 | Paper/live chart isolation (2026-08-24) | Paper and live segments render as two separate, never-joined equity lines per strategy, each measured against its own buy-and-hold benchmark recomputed for that segment's own date range -- matches the existing `phase`-column isolation principle (TRADING.md's own "Live trades are isolated from paper/backtest trades" rule); a blended line would let simulated performance visually vouch for real-money performance |
| 51 | Trading pane sub-tabs (2026-08-24) | Trading gains a sub-tab strip matching Settings' existing pattern: **Strategies** (default -- today's create-form/list/activity content, unchanged) and **Tickers** (new, this slice). Future trading views become new sub-tabs here, never new top-level sidebar sections (CONTEXT.md's Main window entry updated to match: the sidebar is open-ended, not capped at four) |

## Strategy building: sandbox, edit, mix (2026-08-23 blueprint)

Full design produced by an Opus-model Plan agent, spiked and confirmed
before any slice was filed. Six slices, in dependency order:

- **S13 (#858)** -- `cerebral/trading/sandboxed_eval.py`: runs strategy
  code in a real out-of-process sandbox (`WindowsSandbox`, ADR-0010) via
  a child Python process, never `exec()` in Felix's own address space.
  Mechanism only, unwired.
- **S14 (#859)** -- retires `compile_strategy`'s in-process exec
  entirely; both production call sites (`live_tick.py`, `plugins/
  scheduler.py`) route through S13. Also fixes a warm-up-length crash
  and an event-loop-blocking bug found while planning this.
- **S15 (#860)** -- wires `to_strategy` to a real free model (task_type=
  "coding" via the existing router). Without this, "strategies from
  books/internet" all silently degrade to the same hardcoded stub
  regardless of source -- discovered while blueprinting this, not
  previously known.
- **S16 (#861)** -- strategy lineage: a `strategy_versions` table
  (structured provenance, origin, parent_version, components) alongside
  the existing `strategy_specs` dispatch pointer. Prerequisite for S17/S18.
- **S17 (#862)** -- edit a strategy's code via a new `edit_strategy` MCP
  tool: new version, full gauntlet re-run, dispatch pointer only moves
  on VALIDATED, forward record restarts clean (user-confirmed, #27).
- **S18 (#863)** -- mix strategies into a composite via `compose_
  strategies`/a `mix_strategies` tool: unanimous or majority voting,
  right-aligned signal sequences, a new strategy_id, full gauntlet
  required, provenance names every component at its pinned version.
- **S19 (#864)** -- Trading panel: multiple strategies (not just
  `positions[0]`), source view, provenance/version display, an edit
  textarea wired to S17.

**Real, load-bearing finding from the spike (2026-08-23), not assumed:**
`WindowsSandbox().spawn([sys.executable, "-c", "print(1)"], workdir,
timeout_s=15)` returns exit code `0xC0000135` (`STATUS_DLL_NOT_FOUND`)
-- the AppContainer cannot load the Python interpreter at all, before
even reaching pandas. Confirmed this is AppContainer-specific and not a
general sandbox failure: `WindowsSandbox().spawn(["cmd.exe", "/c",
"echo hi"], ...)` in the same sandbox succeeds (exit 0, "hi" on stdout).
S13 cannot be meaningfully verified until the icacls grant (decision
#23) is applied and this spike is re-run and passes.

## Autonomous discovery + day trading (2026-08-24 blueprint)

Full design produced by an Opus-model Plan agent, spiked and confirmed
against the real tree before any slice was filed, same discipline as
the blueprint above. Implements decisions #32-#47. Full text lives in
`.campaign-scratch/autonomous-discovery-blueprint.md` (a detailed
per-slice breakdown, five flagged open sub-decisions each paired with a
recommendation, and a reuse table) -- summary here, issues filed as
#873-#881:

- **S20 (#873)** -- Wire `RiskManager` into `run_strategy_tick`
  (`live_tick.py`), the one choke point every order routes through, not
  each caller. Also: actually apply the position-ramp percentage
  (currently computed and logged, never multiplied into qty), construct
  an `AlertDispatcher` in `main.py` (currently never constructed --
  every documented alert, including "risk limit prevented a trade", is
  silent in production), and add the three risk-limit settings keys
  `SettingsStore` is missing.
- **S21 (#874)** -- `alpaca-py` is not an installed dependency (see the
  spike finding below) -- add it, then make the live path fail loudly
  (a `preflight()` check) instead of silently error-looping. Also wires
  `check_correlation_limit`, the S6 correlation gate that has never run
  either.
- **S22 (#875)** -- Intraday bars. `fetch_ohlcv` is daily-only today, no
  `interval` param exists at all. New `AlpacaMarketDataClient` (same
  module as the execution client, avoids a backtest-vendor-vs-live-
  vendor mismatch), per-strategy `interval` column, and fixes two
  daily-bar assumptions (`DEFAULT_LOOKBACK_DAYS = 180`, hardcoded
  `sqrt(252)` Sharpe annualisation) that silently break at faster
  intervals.
- **S23 (#876)** -- A distinct-trading-days floor alongside the flat
  30-trade minimum, so a fast intraday strategy can't graduate off
  trades crammed into one session.
- **S24 (#877)** -- New `plugins/stocks.py`: yfinance fundamentals
  (already-free, zero new dependency) + SEC EDGAR filing text and
  IPO detection (S-1/424B4, notification-only per decision #37).
- **S25 (#878)** -- `origin='discovered'` lineage (needs a real
  migration -- SQLite can't ALTER a CHECK constraint and the existing
  `CREATE TABLE IF NOT EXISTS` DDL edit would be a no-op on real
  databases) and a decision that the cheap screening pre-filter should
  NOT use the sandbox at all (argv-only transport makes batching
  mechanically impossible under Windows' command-line length cap; the
  pre-filter is Felix's own trusted code, not generated source, so it
  doesn't need sandboxing in the first place).
- **S26 (#879)** -- Felix-wide Activity Log (decision #46) -- the trust
  prerequisite, lands before the discovery loop. A query layer over the
  already-real `conversation_turns` table, a retrofit of the two
  console-only background loops, and a new top-level nav tab.
- **S27 (#880)** -- The autonomous discovery loop itself: one shared
  convergence path (`run_gauntlet`, unchanged signature) with multiple
  entry points, an LLM-judge idea-quality pre-filter, a growing
  watchlist (not a full-universe sweep).
- **S28 (#881)** -- A ticker-fundamentals red-flag gate (recent 10-Q/
  10-K scanned for going-concern/restatement/investigation/delisting
  language) at paper-to-live graduation, for never-before-traded
  tickers only.

**Decision #43 (user arms live trading) is not a slice** -- the
mechanism already exists (S11b's toggle); it's gated on S20 and S21
both landing and being hand-verified, not on any new code of its own.

**Real, load-bearing finding from the spike (2026-08-24), not assumed:**
`alpaca-py` is not a declared or installed dependency of this repo --
absent from `pyproject.toml`, absent from `cerebral/requirements.txt`,
`pip show alpaca-py` reports not found. `AlpacaBrokerClient._connect()`
raises `RuntimeError` on the missing import; `_run_paper_strategy`'s
broad `except Exception` swallows it into `{"status": "error", ...}`.
So flipping `trading_live_arm` to True *today* -- even after S20 wires
the risk gate -- would produce a silent per-tick error loop, never an
actual order. The parallel to S13's `STATUS_DLL_NOT_FOUND` finding above
is exact: a mechanism believed present, never once exercised against
the real thing. S21 exists specifically to close this before S22-S28
build anything on top of it.

## What already exists (reuse, do not rebuild)

| Piece | Where | Use for |
|---|---|---|
| `market_price`, `market_quote` | `plugins/markets.py` | current quotes; NOT history (no OHLCV depth) |
| Book knowledge corpus (S1-S4 landed) | `plugins/book_ingest.py`, ADR-0025 | idea source: claims/assumptions/methods extracted per chapter |
| 82-book trading list | `AI_Trading_Book_Master_List.csv` | the corpus to ingest (books not yet acquired) |
| Claim vs fact vs inference citation | ADR-0025 S8 (#804) | "author claims X" never collapses to "X is true" -- same rule governs strategy provenance |
| `self_dev` | `plugins/self_dev.py` | Felix implements its own slices via clone -> test -> PR |
| `scheduler` | `plugins/scheduler.py` | periodic re-validation / paper-run ticks |
| Sandbox | ADR-0010, `plugins/shell.py` | run generated strategy code without trusting it |
| `browser`, `http_client` | `plugins/browser.py`, `plugins/http_client.py` | URL fetching for S4 |
| `rss_monitor` | `plugins/rss_monitor.py` | future: monitoring trading content feeds |
| Discord / notifications | existing notification paths | alerting on key trading events |

`plugins/finance.py` is receipts/OCR (personal accounting). Unrelated. Do not extend it.

## The pipeline

```
idea -> hypothesis -> data -> strategy spec -> backtest -> VALIDATION GAUNTLET
     -> paper forward record (auto) -> live autonomous execution (ramped)
```

Each arrow is a gate. A strategy that fails a gate does not proceed -- it goes back to
hypothesis, with the failure recorded.

1. **Idea.** Prose from the user, or a claim/method extracted from a URL/book/paper.
   Every strategy carries provenance: source URL, book/chapter/claim, or "user, verbatim".
   User provides URLs; Felix crawls the page and follows links within it.
2. **Hypothesis.** Stated as something falsifiable *before* seeing results: what
   inefficiency, why it should exist, why it should persist, what would disprove it.
   No hypothesis, no backtest.
3. **Data.** yfinance (free, permanent). Handles splits/dividends. Survivorship bias on
   penny stocks is a known, accepted limitation -- strategy cards carry the caveat.
4. **Strategy spec.** A Python function: `def strategy(data) -> signals`. Inspectable,
   executable without a compile step.
5. **Backtest.** Produces an equity curve and a trade log -- never a summary alone.
6. **Gauntlet.** Below.
7. **Paper.** Starts automatically on gauntlet pass. Forward record via Alpaca paper API.
   Numbers from the broker, never re-derived. Minimum 30 trades before the record is
   considered meaningful.
8. **Live.** Automatic graduation when paper CI excludes zero after 30+ trades.
   Position-size ramp: 25% for first 30 live trades, 50% for next 30, then 100%.

## The validation gauntlet

Mandatory pass/fail gates. All thresholds are user-configurable; defaults below.
A strategy that skips a gate is marked UNVALIDATED and cannot reach paper.

- **Out-of-sample.** 20-30% of the historical period, held out, never touched during
  development.
- **Walk-forward.** 5:1 ratio (e.g., fit on 5 years, test on 1 year, roll forward).
  Must be profitable on forward windows in aggregate.
- **Monte Carlo permutation.** p < 0.05. Shuffle returns/labels; less than 5% chance
  that noise produces this result.
- **Vs-random benchmark.** Strategy must beat the 95th percentile of random-entry
  returns (same holding period and position sizing).
- **Vs-benchmark.** Strategy must beat buy-and-hold of an appropriate index (SPY for
  equities, BTC for crypto, etc.). A strategy that beats random but loses to the
  benchmark isn't worth the complexity.
- **Noise / synthetic data.** Perturb prices +/- 1-2%. Sharpe must not drop by more
  than 50%.
- **Parameter sensitivity.** Vary each parameter +/- 20%. Performance must not flip
  from profitable to losing.
- **Costs.** For penny stocks: spread modelled at 1-3% of price, plus slippage.
  Report gross and net.
- **Capacity & liquidity.** Position size must be under 5-10% of average daily volume.

The output of the gauntlet is a **strategy card**: hypothesis, provenance, every gate's
result, the equity curve, and the honest verdict including confidence intervals.

## Risk limits

Enforced in code. A trade that would breach a limit does not execute.
All values are user-configurable; defaults below.

- **Per-trade risk:** 2% of account
- **Daily loss limit:** 6% of account -- halts trading until next session
- **Max concurrent positions:** 10

## Failure behaviour

Conservative-continue across all modes:

- **Stale data / broken feed:** halt new trades until the feed recovers; leave existing
  positions alone.
- **Partial fill:** keep the partial, cancel remainder, adjust position size math.
- **Halted symbol:** queue a close order for when trading resumes.
- **Felix crash mid-position:** on restart, reconcile with the broker (read open
  positions) and resume managing them.

## Strategy retirement

A live strategy is not permanent. Felix monitors its rolling forward performance:

- **CI re-enters zero.** If the rolling confidence interval on expectancy (over the
  last 30+ trades) starts containing zero again, the strategy is halted automatically.
  No new trades; existing positions managed to close.
- **Drawdown breach.** If a strategy's drawdown exceeds 2x the worst drawdown seen in
  the gauntlet backtest, it is halted.
- **Retirement is reversible.** A retired strategy can be re-validated and re-promoted
  if conditions change. Its full history is preserved.

## Alerting

Felix notifies on key events (via existing notification paths -- Discord, etc.):

- Strategy passed the gauntlet (with card summary)
- Strategy graduated from paper to live
- Daily loss limit hit -- trading halted
- Strategy retired (CI re-entered zero or drawdown breach)
- Crash recovery -- reconciled N positions on restart
- Risk limit prevented a trade

## Multi-strategy correlation

When running multiple strategies simultaneously, Felix checks pairwise correlation
of positions before entry. If a new trade would push portfolio exposure above the
concurrent position limit into correlated names (>0.7 correlation over trailing 60
days), it is blocked. This prevents 10 "independent" strategies from all betting on
the same penny stock sector.

## Phased slices (sequential, each gated on the previous having real data)

- S1a -- #831 -- OHLCV data module. Fetch and cache daily bars via yfinance, handle
  splits/dividends, return a DataFrame.
- S1b -- #832 -- Backtest engine. Takes a strategy function + DataFrame, produces equity
  curve + trade log. One reference strategy (MA cross). Panel: backtest results view.
- S2  -- #833 -- Cost/slippage model (penny stock spreads) + out-of-sample + walk-forward gates.
- S3  -- #834 -- Monte Carlo permutation, vs-random, vs-benchmark, noise, parameter
  sensitivity. Strategy card with confidence intervals. Panel: strategy card view.
- S4  -- #835 -- URL/web/book -> strategy spec. User provides URL, Felix crawls + follows
  links, extracts testable claims, generates `def strategy(data) -> signals` with provenance.
- S5a -- #836 -- Alpaca broker integration. Auth via keyring, read account/positions,
  place/cancel orders. Tested against stubs.
- S5b -- #837 -- Risk limits + failure behaviour. Tested on Alpaca paper. All limits
  user-configurable.
- S5c -- #838 -- Paper forward record. Auto-starts on gauntlet pass. Confidence intervals,
  30-trade minimum. Panel: forward record view.
- S6  -- #839 -- Autonomous live execution. Auto-graduation with position-size ramp
  (25% -> 50% -> 100%). Strategy retirement. Multi-strategy correlation check.
  Alerting on key events. Panel: open positions + alerts view.

## Anti-goals

**Will not build:**
- Manual chart-drawing / discretionary TA tools
- Strategy marketplace or sharing/selling features
- Social / copy-trading
- Portfolio optimization / rebalancing (different problem)
- Options pricing / Greeks engine (separate campaign if needed)
- ML model training for strategy generation (separate campaign if needed)
- Signal-space search / genetic algorithm (Build Alpha's lane, separate campaign)
- ~~Autonomous web crawling / source discovery (user provides URLs)~~ --
  **reversed 2026-08-23, decision #32** -- user wants full autonomous
  discovery. Kept struck through rather than deleted: the original
  reasoning (avoid open-ended scraping/ToS exposure) was real when
  written, and the reversal is a conscious, informed decision, not an
  oversight -- see decision #32 for why and what's still bounded (source
  tools reused, not built from scratch; a growing watchlist, not a full
  daily universe sweep; every autonomous action logged).

**Honesty rule (not negotiable):**
- Felix never presents a number without its uncertainty. Backtest, paper, and live
  results always carry confidence intervals.
- Every surfaced number is labelled backtest / paper / live. Mixing them is a bug.
- A forward record under 30 trades is labelled "insufficient sample" regardless of
  how good the returns look.
- Survivorship bias caveat on all yfinance penny stock backtests.

## SAFETY

- **No credentials in the repo.** Broker keys via keyring only (the #160 pattern).
  Paper keys and live keys are separate credentials, never the same account.
- **Tests never hit a real broker, a real market API, or real money.** Inject fixtures
  at the same seams `markets` / `book_ingest` already use.
- **Anything needing a real broker connection to verify** -> append to
  `docs/trading-live-verify.md`; do not perform it in a loop session.
- Seam rule (#153/#385): no `from plugins.<x> import ...` inside `cerebral/`.
- **`trading_ideas._compile_strategy` is test-only.** Production code no
  longer uses `exec()`. All strategy execution now routes through
  `cerebral.trading.sandboxed_eval.evaluate_signals` (S13/#858), which
  runs code in a real ADR-0010 AppContainer sandbox. Do not call
  `_compile_strategy` from production code.

## Future campaigns (explicitly out of scope for S1-S6)

- ML-generated strategies (train models per market/timeframe)
- Signal-space search / genetic strategy generation
- Multi-timeframe analysis (intraday + daily)
- Idea enhancement step (AI critiques hypothesis against book corpus before backtest)
- Autonomous source discovery (crawl trading subreddits, monitor RSS feeds)
- Delayed-fill simulation in the gauntlet
- PR #840 -- S1a (auto-merged by self_dev_campaign)
- PR #840 -- S1a (auto-merged by self_dev_campaign)
