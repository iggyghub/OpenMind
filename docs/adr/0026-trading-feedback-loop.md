# 26. Trading learning loop: shared retrieval, dual nudge, symbol-qualified expansion, same-symbol-only composites

Date: 2026-08-29
Status: accepted (grill session, issues #TBD)

## Context

The trading campaign (S1-S37, all landed) validates strategies through one gauntlet
pipeline but never closes the loop: a validated `StrategySpec` is locked to the one
symbol it happened to validate against, `mix_strategies` requires hand-naming
same-symbol components, and real paper/live performance (`ForwardRecord`,
`StrategyLifecycle`) never influences future `judge_idea`/`to_strategy` calls. This
ADR was reserved as "not written yet" at the top of `TRADING.md` since 2026-08-21;
this is that design.

Grounding facts from the live database, not assumptions:
- 121 validated `strategy_specs` rows across 12 symbols; **zero** share a
  `strategy_id` across two symbols. `strategy_id` is the literal claim/hypothesis
  text and is the table's `PRIMARY KEY`; `StrategyStore.save()` does
  `INSERT OR REPLACE`.
- All 130 `StrategyLifecycle` states are still `status='paper'` — nothing has ever
  graduated to `live`. One archived paper run exists (788 fills, net -$41.42).
- `compose_strategies` generates one `strategy(data)` function per component and
  votes their signals element-wise — every component must see the same symbol's
  bars. Cross-symbol composition is a different code shape, not a relaxed check.
- The discovery pipeline runs constantly (continuous `bonsai` traffic observed in
  `cerebral.err.log`); an unrelated live incident this session (book ingestion
  tasks orphaned after a Cerebral restart, then stalled again after resuming)
  showed the same event loop is already contended.

## Decision

1. **Feedback signal = phase-weighted paper + live fills, not live-only.** With
   zero live-graduated strategies today, a live-only signal would be silent for
   months. `paper` fills count, weighted lower than `live` fills, growing as
   strategies graduate.

2. **Reward metric reuses `ForwardRecord.compute_expectancy_ci` /
   `compute_live_expectancy_ci` as-is** — no new metric. Its existing
   `is_sufficient` significance flag (≥30 trades, ≥30 distinct days) is used as a
   **continuous confidence weight input, not a hard gate** — a low-trade strategy
   still nudges, just weakly. A hard gate would leave the loop inert for a long
   stretch given how few strategies currently clear that bar.

3. **Strategy identity: symbol-qualified `strategy_id` for expansions only, no PK
   migration.** Making a validated strategy work on a second ticker cannot reuse
   the bare claim text as `strategy_id` — `INSERT OR REPLACE` would silently
   destroy the original symbol's row. Rejected a composite `(strategy_id, symbol)`
   key migration (correct long-term shape, but touches live data and every join
   site: `forward_fills`, `StrategyLifecycle`, `list_validated_strategies`'
   provenance-prefix match) in favor of appending `@SYMBOL` to the `strategy_id`
   only for newly-expanded rows. The 121 existing rows are untouched. Cost:
   Similar-claim retrieval strips a trailing `@SYMBOL` before treating `strategy_id`
   as embeddable claim text.

4. **Both nudge mechanisms, one shared retrieval step.** Every new claim triggers
   Similar-claim retrieval (top-5 nearest neighbors by embedding distance, new
   Chroma collection, default embedding function — same pattern as
   `cerebral/memory/manager.py`, no new dependency) before `judge_idea`/
   `to_strategy` run. The retrieved set's Tally (simple win/loss count, never
   elaborated into per-component reasoning) feeds **both**: a sentence appended to
   the judging prompt, and a ±1 bias on `discovery_candidate_limit` for that
   dispatch. Considered scoring-only (smaller, fully testable) and prompt-only
   (richer signal) as separate first slices; the user's call was to build both from
   the one retrieval step since the marginal cost over either alone is small and
   an inert nudge on one path is caught by the other.

5. **Ticker expansion is on-demand, not automatic.** A strategy becomes eligible
   once its confidence weight is positive; the candidate ticker pool is
   `_KNOWN_TICKERS` minus the strategy's current symbol, ranked by
   `rank_for_day_trading` and capped at `discovery_candidate_limit` (3) — same
   ranking and cap discovery already uses, no new logic. Expansion is a callable
   tool (`expand_strategy_ticker` or similar), not a second background loop:
   discovery already dispatches constantly, and a second automatic loop generating
   more gauntlet/LLM work would compound the exact resource contention observed
   live this session when book ingestion stalled.

6. **Combining is scoped to same-symbol only; cross-symbol composites are
   explicitly deferred.** `compose_strategies`' one-`data`-argument, element-wise-
   vote shape only makes sense when every component shares a symbol — a
   cross-symbol (pairs/relative-value) composite needs a genuinely different
   `compose_strategies` mode and a different data-feeding shape in the sandboxed
   eval harness. That is independent, real, future work, not a natural extension
   of this design. In scope now: auto-discover the top-3 confidence-weighted
   strategies per symbol, fire the *existing* `mix_strategies` path for both
   `unanimous` and `majority`, keep whichever backtests better. On-demand, same
   reasoning as (5).

## Consequences

- No schema migration against live data; the identity fix (3) is purely additive.
  Cost is paid once, quietly, inside retrieval's suffix-stripping — worth revisiting
  as a real composite-key migration only if `@SYMBOL`-suffixed rows start
  outnumbering bare ones, at which point the string convention starts looking like
  a workaround rather than a shortcut.
- The feedback loop (1, 2, 4) produces a real nudge even before any strategy
  graduates to `live`, at the cost of trusting noisier paper-phase data early on —
  accepted deliberately in (1) rather than shipping a feedback loop that does
  nothing for months.
- Cross-symbol composites (6) remain an open gap after this ADR lands — tracked as
  future work, not silently dropped. Anyone revisiting `mix_strategies`' same-symbol
  restriction should read this ADR before "fixing" it as an oversight.
- Both new on-demand tools (Expansion, Same-symbol composite) deliberately avoid
  adding load to the existing always-on discovery loop; if usage later shows the
  on-demand friction isn't worth it, promoting either to a scheduled job is a small
  follow-up, not a redesign.

## Amendment — 2026-09-08 (dynamic Candidate pool, replaces the static ticker source in decision 5)

Decision 5 fixed the *ranking* (`rank_for_day_trading`) and the *cap* (`discovery_candidate_limit`)
as shared between Discovery and Expansion, but left the underlying ticker source as
`_KNOWN_TICKERS` — a 36-symbol frozenset hand-edited directly in `discovery.py` source, most
recently 2026-09-01. That was a real gap: which companies count as liquid/interesting drifts over
time, and a hardcoded list needs a human to keep re-editing source to track that drift. It was also
the wrong shape for what the user actually wants — `_KNOWN_TICKERS`' 2026-09-01 revision leaned
mega-cap-heavy, and mega-caps are deliberately low-volatility; day/swing trading wants tickers that
actually move.

**Decision:** the ticker source behind the shared Candidate pool becomes dynamic — built fresh each
use from Alpaca's real market-movers/most-actives screeners (both gainers and losers: a loser is a
bounce/reversal candidate, not just something to avoid) plus a random sample of the broader
tradable universe for breadth, still narrowed by the existing `rank_for_day_trading` liquidity/ATR
filter unchanged from decision 5. `_KNOWN_TICKERS` is kept, not deleted, as the fallback when the
live Alpaca calls fail for any reason (network, auth, rate limit) — Discovery/Expansion must never
go silent just because one live call errored.

Two alternatives were considered and rejected during the grill: a market-cap/S&P-500-constituents
anchor list (rejected — same "barely moves" problem as the mega-cap-heavy `_KNOWN_TICKERS` it would
replace, just refreshed instead of static) and per-strategy-type candidate pools (rejected — no
strategy anywhere declares any ticker-fit criteria today; inventing that tagging concept purely to
split the pool would be solving a problem that doesn't exist yet, not shaped by the actual gap
found). See `TICKER-DISCOVERY.md` (self_dev campaign driver, issues #1157-#1161) for the concrete
slices, and the new **Candidate pool** glossary term in `CONTEXT.md`.

Also added in the same campaign, independent of the pool mechanism above: named finance-news query
sourcing (Motley Fool, Benzinga, MarketWatch, Zacks, CNBC, via the existing `web_search` idea-
sourcing path — no new scraper) and a per-source-domain validated/unvalidated rollup
(`DiscoveryAttempts.get_source_performance`, derived from the `idea_url` already recorded per
attempt — no schema change), so which sites' "top picks" actually pan out becomes visible over
time instead of every source being trusted equally forever.
