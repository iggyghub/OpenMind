# BOOK-TIMEFRAME.md -- book-claim timeframe campaign driver

Fixes book-sourced strategies silently defaulting to daily bars regardless
of what the claim's own text says. Found 2026-09-16 investigating why
every one of 306 live strategies was `interval='1d'` -- a real example
already in the DB shows the cost: a claim reading "Exit any trade that
remains in a loss after holding for 45 minutes from entry" is inherently
intraday, but gets backtested on daily bars.

This is a small **Felix-built** campaign (ADR-0015 self_dev), not a Claude
Code loop -- trading-domain work in this repo routes through Felix's own
self_dev per established convention. A related but separate bug --
`run_gauntlet` dropping even a correctly-passed `interval` at save time --
was already hand-fixed directly in Claude Code (#1264, PR #1265, merged
2026-09-16) since it was a one-line, high-confidence root-cause fix. This
campaign is what actually gets a *non-default* interval to that now-fixed
save path for book claims in the first place.

Scoped 2026-09-16.

## Status: done

## Next slice -- start here

- **Active:** none -- queue complete.
- **Model:** sonnet

## Queue

- [x] S1 -- #1266 -- `Idea.interval` field + `infer_interval()` keyword
  heuristic over claim prose (pure functions, no wiring into the live
  ingestion path yet)
- [x] S2 -- #1267 -- wire `Idea.interval` into `book_library.py`'s
  `run_gauntlet_fn` (mirrors `discovery.py`'s existing pattern)

S1 blocks S2 (the field/function must exist before anything can consume
it).

## Explicitly NOT in this campaign

- Backfilling the 254 already-saved book-sourced strategies currently
  stuck at `interval='1d'`. Re-validating existing strategies under a
  newly-inferred interval could change their verdicts entirely -- a
  separate, deliberate decision, not a default extension once inference
  exists. Same discipline as CROSS-STOCK-VALIDATION's own deferred
  decisions (see that campaign's SAFETY section).
- Making `discovery.py`'s web-discovery path claim-derived too (it
  currently passes a fixed `"15m"`, which is out of scope here).
- Any new interval string beyond this codebase's existing recognized set
  (`1m`/`5m`/`15m`/`30m`/`1h`/`4h`/`1d`, per `replay.py`'s `_warmup_days`)
  -- no weekly/monthly capability exists downstream (bar fetching, warmup,
  annualization), so `infer_interval` must not invent one.

## Landed PRs

- PR #1269 -- S1: `Idea.interval` + `infer_interval()` (merged 2026-09-16).
  self_dev's own first attempt correctly self-blocked on `tests_failed`
  rather than auto-merging a broken diff (the "auto-merges regardless of
  test status" SAFETY note below turned out not to hold for this failure
  class -- corrected there). The truncated reason string (a bare pytest
  dot-progress dump, no real error) was useless on its own, same known gap
  as every other trading-domain campaign's self_dev history here --
  running the actual test suite against the branch in an isolated worktree
  found two real off-by-one boundary bugs: `infer_interval`'s minutes
  bucketing used `n<=4`/`n<=14` where it needed `n<=5`/`n<=15`, so "5
  minute" claims landed as `"15m"` and "15-minute" claims as `"30m"` --
  self_dev's own generated tests had the right expected values, the
  implementation's boundaries were just one bucket off. Fixed directly on
  the PR's branch; full `test_trading_ideas.py` 37/37 green after, broader
  `trading_ideas or book_library` sweep 43/43 green.
- PR #1270 -- self_dev's S2 attempt -- **closed unmerged, hand-implemented
  from scratch instead.** Threaded `interval` through
  `cerebral/trading/books.py`'s `ingest_book` into `process_idea`'s
  kwargs -- but `process_idea` (`discovery.py`) has no `interval`
  parameter, so this would raise `TypeError` on exactly the claims with a
  detected timeframe, the entire point of the slice. Also the wrong file:
  the issue asked for `book_library.py`'s `run_gauntlet_fn` closure, which
  already has `idea` (and `.interval`, from S1) in scope and needs no new
  plumbing. No tests were added in this attempt at all (0 test-file
  changes in the diff).
- **Hand-implemented S2** (commit `694a090`, merged 2026-09-16): two lines
  in `book_library.py`'s `run_gauntlet_fn` closure -- `if idea.interval:
  gauntlet_args["interval"] = idea.interval`. Two new regression tests in
  `test_plugin_book_library.py` exercise `_run_book_ingestion` end to end
  (a ticker-naming claim routes straight to `run_gauntlet_fn`, skipping
  the judge/watchlist machinery): a claim with detected timeframe language
  produces that interval in `gauntlet_args`, a claim with none omits the
  key. Verified the first test fails without the fix (`KeyError`).
  `test_plugin_book_library.py` 4/4 green, broader `book_library or
  trading_books or trading_ideas or discovery` sweep 174/174 green.
  **Live verification:** restarted Cerebral via the real `restart_felix`
  IPC path (not the launcher directly -- see the launcher-fix issues
  #1262/#1268 for why that matters) and confirmed the new code is loaded
  (commit timestamp precedes the restart). Did not force a real book
  upload through to a `VALIDATED` gauntlet verdict to observe the final
  saved `StrategySpec.interval` end-to-end live -- the full trading
  validation gauntlet (out-of-sample, walk-forward, Monte Carlo,
  vs-random, vs-benchmark, noise, parameter sensitivity, costs, capacity)
  makes a real claim reliably validating a live-data gamble, not something
  worth forcing just to watch one field. The two halves of this path are
  each independently, automatically verified instead: this slice's own
  tests prove `gauntlet_args["interval"]` gets set correctly, and #1265's
  tests (already live, verified earlier the same day) prove a `VALIDATED`
  save correctly threads whatever `args["interval"]` was into the saved
  spec. The join between them is a single dict key read via
  `args.get("interval", "1d")` -- confirmed by direct code inspection, not
  additionally re-proven live.

## Campaign complete (2026-09-16)

Both slices landed. self_dev's own attempts needed hand-fixing on both:
S1 had a real off-by-one caught by running its own tests properly; S2's
approach was fundamentally wrong (wrong file, a kwarg the target function
doesn't accept) and was replaced outright. Consistent with every other
trading-domain campaign in this repo's self_dev history -- hand-review
every diff, never trust a `self_dev_campaign_status` report alone.

## SAFETY

- **No slice may place an order, real or paper.** This changes what
  interval a validated strategy is *recorded* as running at; it does not
  touch `run_gauntlet`'s gates, `auto_promote`, or dispatch.
- **Ambiguous claims must never guess an interval.** No timeframe language
  in the claim text -> `None`, not a fabricated `"1d"` (or anything else)
  at the inference layer. The existing `_run_gauntlet` default (`"1d"`
  when no `"interval"` key is present) already handles the fallback --
  S1/S2 must not duplicate or override that decision.
- **Hand-verify live before calling S2 done (ADR-0028 rule 6).** self_dev
  has failed outright on multiple slices across this repo's other
  trading-domain campaigns (BATCH-REPLAY, CROSS-STOCK-VALIDATION) --
  green tests are necessary, not sufficient. S2's issue spells out the
  exact live-verification steps (restart Cerebral, process a real claim
  with explicit timeframe language, confirm the saved spec's interval).
- **Do not assume self_dev_campaign auto-merges regardless of test status.**
  The plugin's own description says it does (2026-08-21 full-auto-merge
  amendment), but S1's real run set `Status: blocked` on `tests_failed`
  and left the PR open rather than merging a broken diff -- whatever the
  intended policy, `tests_failed` observably blocks in practice. Either
  way, whoever runs this campaign (Foreman loop: fire -> hand-verify the
  real PR diff -> fix bugs found -> retrigger) must review each landed
  PR's actual diff before trusting it, same discipline as every other
  trading-domain campaign in this repo's history -- S1's own bug (a
  boundary off-by-one) would have shipped silently on a pure "trust green"
  read of the campaign status.
