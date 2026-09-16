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

## Status: ready

## Next slice -- start here

- **Active:** S2 -- #1267
- **Model:** sonnet

## Queue

- [x] S1 -- #1266 -- `Idea.interval` field + `infer_interval()` keyword
  heuristic over claim prose (pure functions, no wiring into the live
  ingestion path yet)
- [ ] S2 -- #1267 -- wire `Idea.interval` into `book_library.py`'s
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
