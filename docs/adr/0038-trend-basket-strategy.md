# 38. Trend basket play: a second hand-written strategy, IPO play's DNA with a different exit

Status: accepted (grill session, 2026-09-22)

## Context

FELIX-CAUSALITY.md closed out an exhaustive validation of 283 book-mined, LLM-judged strategies
with a null result across four independent tests (causality, cross-stock permutation, regime
stress, true intraday rules) — no continuous-signal technical rule survived. The IPO play
(CONTEXT.md) is this repo's one prior success: a hand-written, fixed-rule Strategy validated once
against real data, not sourced from books or discovery. This ADR designs a second one, explicitly
**not** book-sourced and **not** routed through `claim_store`/discovery — same origin as the IPO
play, a from-scratch grill session, not a variant of the null-result book pipeline.

The starting idea (from the grill) was literally "reuse the IPO play's mechanism on regular
stocks": when the market is broadly trending up, split capital across several trending stocks and
apply the same entry-then-trailing-stop shape. Real historical backtests run during the session
(scratchpad scripts, not committed code — see the session transcript) tested this piece by piece
against 2005–2026 daily bars and 2020–2026 5-minute bars for a liquid subset of large/mid caps.

## Decision

**Entry:** market breadth — the share of the Candidate pool trading above its own 50-day moving
average, computed from the prior day's close (no look-ahead) — must exceed 60%. On that condition
turning on, open 10 equal-weighted (10% each) positions in the Candidate pool's top 10 names by
**momentum × 20-day realized volatility** (20-day return multiplied by 20-day daily-return stdev,
ranked descending — see "Full-universe correction" below for why this replaced pure momentum
ranking).

**Exit:** a flat 12% trailing stop from each position's own peak since entry, independent per
symbol, hard-capped at 20 trading days. No tightening ratchet.

**Gauntlet:** every selected symbol goes through the normal `_run_gauntlet` per-symbol backtest,
same as any other idea source. No bypass.

### Why the exit isn't the IPO play's exit

The original idea was to reuse the IPO play's exact strategy code (`ipo_strategy.py`: 3% trail,
tightening to 1% once the position is up 20%+) on ordinary stocks. Tested directly — that shape
loses to simply holding the same picks with no stop at all, on both daily bars (round 1–2: median
−7.7% vs. buy-and-hold on the same picks) and on 5-minute bars at the IPO play's own native
granularity, multi-day hold (round 5: beats flat-hold on only 29/104 events). The failure isn't a
sampling-resolution artifact — the tight tighten-at-20% trail was calibrated for a single IPO
day's volatility profile (pop, then fade, within hours). Any multi-day swing hold on an ordinary
stock accumulates enough real drawdown along the way, at any bar resolution, to keep tripping a
stop that tight before the trend plays out.

A flat, non-tightening, wider trailing stop (swept 5–25%) does not have this problem: it
consistently matched or beat a no-stop hold while still capping single-name downside (round 4,
round 6). **Verdict: the IPO play's peak-tracking *concept* transfers; its specific tightening
*parameters* do not, and reusing them unmodified would quietly re-break this strategy.**

### Why no Gauntlet bypass

The IPO play's bypass is justified by one fact: a brand-new IPO ticker has zero price history
before its first trading day, so a per-symbol backtest is structurally impossible before dispatch.
That fact doesn't hold here. This strategy's own entry criteria (a 50-day moving average, 20-day
momentum) already require ~50+ days of price history before a ticker is even eligible to be
selected — so every candidate this strategy could ever pick already has enough history for a
normal Gauntlet backtest. There is no case where bypass would even apply; adding one would be
unjustified complexity, not a shortcut.

### Why "under a month," not IPO's open-ended hold, and not same-day

Two shapes were tried and rejected during the grill: a same-day (flat by close) version showed no
real signal at all — gated and ungated days were statistically indistinguishable (round 8: ~52%
win rate either way, matching FELIX-CAUSALITY.md's existing null result on 8 hand-authored
day-trading rules). A longer fixed-duration hold (45–90 trading days, round 6–7) showed a larger
edge (+3.7–4.4% median excess vs. the universe average) but doesn't match "ride it until it turns
down" — the user's stated intent was a pure trailing exit, not a fixed calendar window. The
20-trading-day cap (round 10) keeps the realized average hold at 11–18 days (comfortably under a
month) while still clearing the universe-average benchmark on 60–77% of trades across every trail
width tested (5–15%) — a smaller edge than the longer fixed hold, but the one that actually matches
the mechanism as specified, not the mechanism that back-tested best in isolation.

## Considered and rejected

- **Trend-ranked vs. random vs. "laggard" (weakest-momentum-within-uptrend) selection.** Trend-
  ranked won decisively once measured against the whole-universe average return rather than the
  10 picks' own noisy buy-and-hold (round 3: gated+trend +11.26% median 60-day forward return,
  67% beat rate vs. universe average; ungated+trend only +3.70%/56%). Random and laggard showed
  no edge either gated or ungated. The earlier appearance that laggard was "best" (round 1–2) was
  an artifact of comparing against each combo's own small 10-stock buy-and-hold, which the tight
  IPO-style stop was actively punishing trend picks the hardest on (they're the most volatile).
- **SPY > its own 200-day MA as the gate**, instead of Candidate-pool breadth. Rejected: it's on
  77% of trading days historically (barely a filter — closer to "not in a declared recession" than
  a real regime signal) and produced far fewer regime-change events (31 vs. breadth's 104) to
  learn from.
- **10 vs. fewer/more basket slots.** Swept 5–25 (round 9): fewer slots concentrate in the
  strongest momentum names (more edge, more single-name variance — 5 slots: 52% stdev of basket
  return); more slots dilute toward the universe average. 10 slots at 10% each has the highest
  win rate of anything tested (75%) with real excess (+3.7%) and materially lower variance than
  5–8 slots — confirms rather than overrides the starting "10% each" idea.

## Full-universe correction (2026-09-22, same session)

Everything above (rounds 1–12) was tested against a hand-picked 33-ticker subset, chosen for
reproducibility. The books campaign's own stress-window cache (`bars_hist.db`) already had a much
larger, already-local, already-free universe sitting unused: **58 symbols**, most back to 2005,
built by the FELIX-CAUSALITY.md regime-stress campaign. Re-running the locked mechanism (momentum
selection, breadth gate, 12% trail/20-day cap) against that full universe was a materially
different, more honest result than the 33-ticker numbers reported above:

```
                          beat-vs-universe   perm-null beat-own-null   min BH q
33-ticker universe             71–77%               67%                0.247
58-symbol universe (momentum)   54%                  55%                0.686
```

The 33-ticker set happened to be tilted toward the high-volatility names (RIOT, ITUB, BBD, NIO,
SNAP, SOFI) the permutation-null check had already flagged as where the edge concentrates. Adding
26 more, mostly lower-volatility blue chips (AXP, BAC, C, COST, GS, HD, JPM, MCD, WMT, V, MA, and
others) diluted the basket toward names the mechanism has no real edge in, and the result dropped
to near-coin-flip. **The 71–77% numbers earlier in this ADR were partly a universe-selection
artifact, not proof of a broad edge — treat them as a step in the session's reasoning, not the
final record.**

Tilting selection toward volatility (rather than pure momentum) partially recovers this at full
scale: ranking by momentum × 20-day realized volatility (not momentum alone) on the same 58-symbol
universe:

```
momentum only:            54% beat-vs-universe, 55% beat-own-null, min q=0.686
momentum x volatility:    58% beat-vs-universe, 60% beat-own-null, min q=0.347
```

Real improvement, not a full recovery of the narrow-universe numbers — this is now the locked
selection method (see Decision above), and it should be read as a modest, plausible, **not
statistically proven** edge, not the stronger-looking 67–77% figures the narrower test produced.

**What stays true regardless of universe size or selection variant: absolute participation.**
Median return is positive (+0.96% to +1.22% per ~20-trading-day trade) and win rate is 56–61%
across every variant tested, on the full 58-symbol universe, specifically during gate-on (breadth
>60%, i.e. market-trending-up) conditions. That's the property this strategy was actually built to
have — the basket goes up when the market's own breadth says it's trending up — and it holds
independent of which stock-picking refinement sits on top. The relative "beats a passive basket"
bar is the harder, still-unproven one; the basic "moves with the market's own uptrend" bar is met.

## Permutation-null significance check (initial 33-ticker version — superseded by the full-universe
## numbers above; kept for the session's reasoning trail)

Everything above compares against a buy-and-hold or universe-average benchmark. FELIX-CAUSALITY.md's
rigorous version of that question for the 283 book strategies was a circular-shift permutation
null (`cerebral/trading/permutation_null.py`): does the real *entry timing* beat the same exposure
(same trade count, same holding periods) at randomized dates? First run on the 33-ticker set, full
walk-forward per-symbol position series (one position at a time per symbol), 500 circular-shift
draws per symbol, real 2015–2026 data, real cost (2 bps notional/side):

```
Nominal p<0.05: 5/33 symbols
BH-adjusted q<0.05: 0/33 (min q = 0.247)
Median observed return: +41.95%   |   median random-timing null: +6.65%
Observed beats its own random-timing null on 22/33 symbols (67%)
```

As the "Full-universe correction" section above found, this 33-symbol result was partly an
artifact of that set's tilt toward high-volatility names — the 58-symbol re-run (min q=0.686 for
momentum-only, 0.347 for momentum×volatility) is the number that should actually inform any
go/no-go decision, not this one. Kept here because it's what pointed at the volatility-concentration
finding in the first place: the edge is not evenly spread across the universe — it's concentrated
in high-volatility/high-beta names (RIOT, ITUB, BBD, NIO, SNAP, SOFI all beat their null by a wide
margin on the 33-ticker set), while blue-chip mega-caps mostly don't (TSLA, AAPL, GOOGL, CRM, PYPL
all sit at or below their own null) — the observation that led directly to the momentum×volatility
selection change.

## Consequences

- **Regime coverage is asymmetric by construction, but re-validated clean on the full universe.**
  Re-run on the full 58-symbol universe with the locked momentum×volatility selection: every
  window shows positive median excess and a beat-rate above 50% — gfc +0.49% (53%, n=19), mid
  +0.45% (58%, n=113), bear22 +3.76% (67%, n=6), main +0.82% (60%, n=50). Modest everywhere,
  consistent with the full-universe correction's overall finding, but unlike the 33-ticker version
  this is not a universe-selection artifact — it held up on the bigger, more representative set.
  bear22 is still a thin sample (n=6) and the breadth gate still rarely fires during a crash by
  construction (it mostly sits out bad regimes rather than trading through them), so treat that one
  window as directionally supportive, not proof — but no window failed outright, which is more than
  any of FELIX-CAUSALITY.md's 165 book strategies achieved (0 robust across all windows at once).
- **Causality/look-ahead check run 2026-09-22, causal**: `check_causality` against real AAPL
  2015–2026 daily bars — `causal=True, mismatches=0, tested=60`. No look-ahead in TREND1's code.
- **Backtests here used `bars_hist.db`'s 58-symbol research cache** (built by the FELIX-CAUSALITY
  regime-stress campaign), not the Candidate pool's live dynamic universe (movers/most-actives +
  liquidity filter, refreshed daily) — the closest free, already-cached proxy for "a broad,
  representative universe" available during this design session, not a claim that the real
  Candidate pool looks exactly like these 58 names. The live implementation dispatches against the
  real Candidate pool, which should be re-validated against once the strategy has accumulated real
  dispatch history — the same way Confidence weight already re-weights every other Strategy as
  more Fills come in.
- **The permutation-null p-values are optimistic, the same way `permutation_null.py`'s own
  docstring already warns**: the symbols in any one universe share one market regime, so their
  p-values are correlated, not independent — treat "0/58 significant after BH correction" as the
  honest no-spin headline and "58–60% beat their own null with the volatility tilt" as a real but
  modest, unproven lead, not treat either number as more precise than it is.
- **Position sizing does not actually reach 10% per trade yet, contrary to this ADR's own
  "no risk_override_pct plumbing needed" reasoning above.** `_run_gauntlet` sizes positions off
  the global `max_per_trade_risk_pct` setting, and `cerebral/settings.py`'s real default for that
  key is **2.0%**, not 10% — this ADR's claim that the global default already matched 10% was
  written without checking `cerebral/settings.py` and was wrong (found during TREND3's build,
  2026-09-22; see TREND-BASKET.md's Landed PRs entry for PR #1347). As shipped, 10 dispatched
  slots total 20% invested, not the intended 100%. Safe (under-deploys rather than over-risks) but
  not what the backtests in this ADR were validated against. Needs a follow-up slice mirroring the
  IPO play's own `risk_override_pct` plumbing (IPO1–3) before this strategy trades at its designed
  sizing. Filed as #1349.
- **No reentrancy guard on `trend_basket_dispatch`** — found during live verification, 2026-09-22
  (see TREND-BASKET.md's PR #1348 entry). Filed as #1350.

## Amendment (2026-09-24) -- register picks without the per-symbol Gauntlet; anchor entry to a date

**Context** -- Before the first paper run, the real `run_gauntlet` (the same call
`_run_gauntlet` makes) was backtested on the basket's actual top-10 picks at 25 historical
rising-edge dates (58-symbol `bars_hist.db` universe, 2008-2025). It passed **5 of 248 picks
(2%)**, and 23 of the 25 dates passed 0 of 10. Most failures were `monte_carlo_permutation` (232),
then `vs_benchmark` (158) and `vs_random` (134). The rejected picks were not bad trades (median
20-day trade +1.6%, 52% winners). The Gauntlet backtests one symbol over the past year, and for
this code that meant one 20-day hold starting a year ago, then 11 months flat: mostly zero returns
that can't reach significance. The same session found that the strategy code could never open a
live position. It treated the data's first bar as the entry and went flat after bar 20, while
`live_tick` passes a 180-day window and acts on the last signal, so the signal was always 0
(confirmed on real AAPL/NVDA/SOFI bars). The original "Why no Gauntlet bypass" reasoning was only
about data availability ("every candidate has enough history"), not about whether the Gauntlet's
test fits this strategy. It doesn't.

**Decision** -- Chosen by the user 2026-09-24 (option A). Trend-basket picks are registered
directly, the way the IPO play is: a `StrategySpec` plus one recurring scheduler event per
symbol, with no per-symbol Gauntlet run. The strategy's validation is the portfolio-level research
in this ADR (breadth timing plus cross-sectional selection), which a one-symbol backtest can't see.
Also corrected in the same change (commit c7613dd):
- Strategy code is generated per position with a real `ENTRY` date (`trend_basket_code`). It
  holds from that date with the same 12% trail and 20-bar cap, so the live last-bar signal is
  correct.
- `rank_by_momentum` now scores 20-day return x 20-day daily-return stdev, the locked selection in
  "Full-universe correction" above. The shipped code had ranked by pure momentum.
- "Already held" expires 30 calendar days after entry (covering the 20-trading-day cap), so a later
  rising edge can re-enter a symbol. A re-entry re-saves the spec under the same id (new version,
  fresh forward record) and does not create a second event.

**Considered and rejected**
- *Keep the Gauntlet as-is.* At a 2% pass rate the basket would almost never trade, and the filter
  rejected ordinary picks for a structural reason, not a quality one.
- *A trend-basket-specific Gauntlet backtest* (replaying the rule at this symbol's past signal
  dates). Keeps a per-stock filter, but it's real work with no evidence it would filter anything
  meaningful. The 5 historical passers (+10.5% median) are too few to show that a per-stock
  filter adds edge. Revisit only if live results suggest bad single names are the problem.

**Consequences**
- Order-time risk checks are now the only per-trade guard: the per-trade cap (with the 2026-09-23
  trim-to-cap for small overshoots), concurrent-position and correlation limits, the sentiment
  gates, the daily-loss halt, and live_tick's global 5% stop-loss / 30% take-profit backstop. The
  backstop is tighter than this ADR's 12% trail and exits first on a 5% drawdown from entry. That
  is an existing user policy, deliberately left unchanged here and flagged for the user.
- The sizing gap in Consequences above (#1349) is closed without override plumbing: live
  `max_per_trade_risk_pct` is 10 and `trading_paper_starting_capital` is 100, matching the $100
  Alpaca paper account.
- ADR-0028 rule 4 is unaffected: the Gauntlet is a strategy-quality filter, not the ADR-0005
  permission gate, and no permission path changes. The 16-class capability vocabulary is unchanged.

## Amendment (2026-09-24, second) -- trigger 55% / sustain 50%: refill slots while the uptrend holds

Extends the first 2026-09-24 amendment above; it does not change that one's Gauntlet decision.

**Context** -- Under the original Decision, a basket buys only on the day breadth crosses above
60%. Each slot then sits in cash once its position exits (trail or 20-day cap) until the next
crossing, so the strategy averaged about 35% invested. The user asked whether the number should be
55% and whether trigger and sustain should be separate. A capital-constrained portfolio backtest
(same 58-symbol universe, 10 slots x 10%, 12% trail, 20-day cap, close fills, signals and picks from
the prior close) compared 8 trigger/sustain combinations, split 2005-15 / 2016-26:

```
                               2005-15 CAGR/Sharpe   2016-26 CAGR/Sharpe   21-yr maxDD   invested
cross 60%, buy that day only      +7.0%  0.50          +20.4%  0.98           -32%        35%
cross 55%, buy that day only     +10.8%  0.72          +13.2%  0.74           -42%        40%
cross 60%, refill while >55%     +16.2%  0.85          +31.4%  1.03           -46%        53%
cross 55%, refill while >50%     +23.8%  1.09          +29.3%  0.96           -40%        63%
no gate, always refill           +20.6%  0.75          +34.6%  0.99           -62%        99%
```

**Decision** -- Chosen by the user 2026-09-24. The regime turns **active** when breadth crosses
above **55%** and stays active while breadth is above **50%**. On every scheduler tick while
active, empty slots (up to 10) are refilled with the top momentum x volatility names not already
held. An occupied slot is one whose position's own strategy code still signals hold on the latest
bars, or one entered today. Commit c4bfe78.

**Considered and rejected**
- *55% as a one-day trigger only.* Inconsistent: better in 2005-15, worse in 2016-26.
- *Hysteresis / multi-day confirmation on the one-day trigger* (tested 2026-09-23, recorded in
  TREND-BASKET.md). Worse in both halves.
- *No gate, always refill.* Similar return per unit of risk, but a -62% worst drop versus -40%.
  The breadth gate's value is drawdown protection.

**Consequences**
- More time invested means bigger swings than the original design: the worst backtested drop goes
  from -32% to -40%.
- The thresholds were picked as the best of 8 on the same data, so they are probably flattering.
  The refill improvement itself held in every refill variant and in both halves, so that part is
  the robust finding. The universe is 2026's survivors, so absolute returns are inflated.
- The 16-class capability vocabulary and the ADR-0005 permission gate are unchanged.
