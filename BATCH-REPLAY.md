# BATCH-REPLAY.md -- Batch Historical Replay campaign driver

Runs the existing Historical Replay engine (REPLAY.md, `plugins/trading_replay.py`)
over the full available history in 1-month batches, as a standing, resumable,
user-controllable job -- instead of the one-off `simulate_period` calls the
original campaign shipped. Two goals: (1) build up a real multi-period track
record per strategy instead of a single 4-week snapshot, and (2) actually use
that accumulated evidence -- feed it into `StrategyLifecycle.check_retirement`'s
currently-hardcoded-to-0.0 drawdown circuit breaker, and add a new refusal gate
to `check_graduation` so a strategy with a bad accumulated replay history can't
graduate to live money on a lucky 30-trade paper streak alone.

Scoped 2026-09-14 in conversation with the user (not from a GitHub issue --
context: live trading review found paper dispatch inactive since a Sep 4 reset;
led into "how would these strategies have performed historically," which led
into "let's watch this systematically over time instead of one-off," which led
into "and make the accumulated data actually feed decisions, since nobody's
manually reviewing this"). Explicitly run via Felix's own `self_dev_campaign`
(ADR-0015), not a Claude Code campaign-scaffold loop, per direct instruction.

**Read before running:** self_dev_campaign auto-merges every slice regardless
of guardrail/test status (2026-08-21 full-auto-merge amendment) -- unlike the
scheduler-split campaign run earlier today (Claude Code loop, tests gated
every merge), nothing here blocks a bad PR from landing on its own. Hand-review
every landed PR's actual diff before trusting it, same discipline REPLAY.md's
own trading-domain slices needed almost every time.

## Status: done

## Next slice -- start here

- **Active:** none -- queue complete. check_retirement's unit-mismatch
  follow-up (see SAFETY) and the risk-cap sizing investigation (separate
  from this campaign) are the only open threads.
- **Model:** sonnet

## Queue

- [x] S1 -- #1224 -- backend: start/stop/status tools, 1-month cadence, persisted resumable cursor
- [x] S2 -- #1225 -- Trading pane sub-tab: Start/Stop button + status
- [x] S3 -- #1226 -- timeline visual: progress across the full replay range
- [x] S4 -- #1227 -- feed accumulated drawdown into check_retirement + a new check_graduation refusal gate

S1 must land before S2 (UI calls S1's tools). S2 before S3 (S3 renders inside
S2's sub-tab). S4 depends only on S1 (the batch loop it hooks into), not S2/S3
-- could in principle land out of order, but keep it last anyway since it's
the one slice touching real trading-decision logic and benefits from S1 having
already accumulated a few real batches to test against.

## Landed PRs

- PR #1228 -- S1: batch replay backend (merged 2026-09-14, hand-fixed after
  self_dev's own attempt correctly self-blocked on tests_failed rather than
  force-merging red -- real bugs found: cursor advanced by 1 day instead of
  1 month, missing module-level task-state declarations, new settings keys
  never registered in cerebral/settings.py's allowlist, batch_replay_start
  clobbered on every boot-resume, and both new tests touched real production
  settings via a dead CEREBRAL_DATA_DIR env var plus called asyncio.run()
  inside a sync test body in an asyncio_mode=auto suite)
- PR #1230 -- S2: Trading pane sub-tab (merged 2026-09-14, hand-fixed after
  self_dev's own attempt correctly self-blocked on tests_failed -- but the
  block's own reason was pytest dots output, meaning verification only ran
  the Python suite and never actually executed this slice's own Jest tests
  at all. Real bug: three functions written as bare `function name(){}`
  declarations directly inside the module's `return {...}` object literal
  -- not valid object-literal syntax, the whole module failed to even
  `require()`. Also a genuine resource leak in the new interval test (a
  real uncleared 2000ms setInterval, "Jest did not exit"))
- PR #1231 -- S3: timeline bar (merged 2026-09-14, hand-implemented --
  self_dev_campaign's edit step produced an empty diff twice in a row for
  this slice, no PR ever opened either time, not retried a third time.
  Also fixed a real S2 bug found while implementing this: the status
  text read status.current_month/status.total_months, but
  get_batch_replay_status's actual field names are
  cursor_date/months_total -- "Processing: —" has never shown real data
  since S2 landed. Visual hand-verification in the running tray (light +
  dark) still outstanding -- not something a passing test confirms.
- PR #1232 -- S4: accumulated drawdown into lifecycle gates (merged
  2026-09-14, hand-implemented after S3's self_dev misses. `check_graduation`
  gets a real refusal gate off `StrategyStore.worst_drawdown` (rolled up
  from every accumulated replay run) vs. `batch_replay_graduation_dd_cap`
  (default 0.30). `check_retirement`'s `worst_backtest_dd=0.0` hardcode was
  deliberately left alone -- see SAFETY below, unit mismatch is a real open
  design question, not a bug to hand-fix in passing. Full suite green
  (5804 passed, 2 known pre-existing pollution failures unrelated to this
  change). Hand-restart + IPC verify still to do.

## SAFETY

- **No slice may place an order, real or paper.** This reuses REPLAY.md's
  exact engine and its exact constraint: `run_replay`/`simulate_period` read
  history and write a results row, nothing else. `run_gauntlet` must never be
  called from this path (same reasoning as REPLAY.md's own D1: `auto_promote`
  would re-register the whole portfolio).
- **S4 is the one slice that changes real trading-decision logic.** Everything
  else (S1-S3) is pure accumulation and display, reversible with no behavior
  change if skipped. S4 must not grow beyond the two named gates
  (`check_retirement`'s drawdown input, `check_graduation`'s new refusal
  check) into any broader auto-retire/auto-tune surface.
- **Conservative-refuse convention**: missing/`None` accumulated replay data
  must never be treated as a refusal signal anywhere in S4 -- mirrors the
  existing fundamentals-accession-not-found handling in `check_graduation`
  ("inventing a refusal here would be a fabricated signal").
- **A full ~10-year sweep is untested at that scale.** RP9's real 4-week/286-strategy
  run took ~8 minutes and ran slower than the campaign's own original estimate
  because of real per-strategy news-API calls. Expect S1's full sweep to take
  a long time; that's exactly why it's a resumable background job with a stop
  button, not a single blocking call.
- **Hand-restart Felix and hand-verify via the IPC bridge after S1 and S4**
  land, same as every other trading-domain slice in this repo's history --
  a green test run is necessary, not sufficient (ADR-0028 R6).
- **`check_retirement`'s unit mismatch is unresolved, on purpose (post-S4).**
  `current_live_dd` (the live circuit breaker) is a raw dollar amount --
  cumulative PnL peak minus current. Replay's `worst_drawdown` is a
  fraction of returns (e.g. -0.24). Feeding one into the other needs
  either (a) tracking `current_live_dd` as a fraction of the strategy's
  own peak equity too -- cleaner, but a ramping position size (25%->50%->
  100%) distorts what "peak equity" even means -- or (b) converting
  replay's fraction to dollars via `qty` x a reference price -- more
  faithful to the existing dollar design, but needs a real price fetch in
  the live dispatch hot path and "reference price" (at registration? at
  replay time? current?) is ambiguous. User has not chosen an approach --
  ask before implementing either one.
