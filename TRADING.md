# TRADING.md -- Stock trading campaign driver

Design: ADR-0026 (not written yet).
Scaffolded 2026-08-21, grill closed 2026-08-22.

## Status: ready -- the icacls grant (decision #23) is applied and the
spike re-run confirms it fixed the real problem: `WindowsSandbox().
spawn([sys.executable, "-c", "print(1)"], ...)` and a follow-up `import
pandas` both now exit 0 with real stdout, where they previously failed
with `0xC0000135` (STATUS_DLL_NOT_FOUND). S13 is unblocked.

## Next slice -- start here

- **Active:** S13 -- #858 -- sandboxed strategy evaluation, mechanism
  only (unwired). See issue #858. Six more slices queued behind it
  (S14-S19, #859-#864) -- real sandboxing, wiring `to_strategy` to an
  actual free model (currently every generated strategy silently falls
  through to a hardcoded stub regardless of source -- #860), strategy
  lineage/versioning, editing, mixing, and a UI to use all of it. Full
  plan and the open questions it resolved are in "Strategy building:
  sandbox, edit, mix (2026-08-23 blueprint)" below.
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

- [ ] S13 -- #858 -- Sandboxed strategy evaluation, mechanism only
  (unwired). Blocked on the icacls grant -- see Status.
- [ ] S14 -- #859 -- Retire in-process compile_strategy exec; route
  both call sites through S13's sandbox.
- [ ] S15 -- #860 -- Wire to_strategy to a real free model (currently
  every generated strategy is the hardcoded stub, regardless of source).
- [ ] S16 -- #861 -- Strategy lineage: versions, structured provenance,
  component tracking.
- [ ] S17 -- #862 -- Edit a strategy's code (new version, full
  re-validation, restarts clean on a live strategy -- user-confirmed).
- [ ] S18 -- #863 -- Mix strategies into a composite (unanimous/majority
  voting).
- [ ] S19 -- #864 -- Trading panel: strategy list, source view, edit
  box, lineage display.

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
- Autonomous web crawling / source discovery (user provides URLs)

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
- **`trading_ideas.compile_strategy` is not real sandboxing.** It exec()s
  code ultimately derived from scraped web content, hardened with a
  forbidden-pattern scan + a minimal builtins allowlist (2026-08-22, see
  PR #843 in Landed PRs) -- but that's a partial mitigation, not the
  ADR-0010 sandbox self_dev uses for untrusted code. Route strategy
  execution through that sandbox for real before S5+ runs generated
  strategy code against a live broker connection.

## Future campaigns (explicitly out of scope for S1-S6)

- ML-generated strategies (train models per market/timeframe)
- Signal-space search / genetic strategy generation
- Multi-timeframe analysis (intraday + daily)
- Idea enhancement step (AI critiques hypothesis against book corpus before backtest)
- Autonomous source discovery (crawl trading subreddits, monitor RSS feeds)
- Delayed-fill simulation in the gauntlet
- PR #840 -- S1a (auto-merged by self_dev_campaign)
- PR #840 -- S1a (auto-merged by self_dev_campaign)
