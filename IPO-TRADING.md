# IPO-TRADING.md -- self_dev campaign driver for the IPO pop-then-fade strategy

Source: a grill-with-docs design session, 2026-09-02 (same Claude Code session that closed out
TRADING-AUDIT-FIXES.md). User wants a dedicated strategy for IPO plays: buy at the open on a
stock's first trading day, ride it with a trailing stop that tightens once it's up 20%+. Full
design reasoning (including two real backtests against real 2026 IPO data that shaped the exact
thresholds) is in that session's transcript; CONTEXT.md's new "IPO play" glossary entry has the
condensed architectural rationale for the one deliberate exception this makes to the Gauntlet's
"every idea source converges through it" rule.

**Read before running this campaign:** every trading-system slice landed via self_dev in this
project's whole history has needed hand-review, and the large majority shipped a real bug
self_dev's own tests didn't catch. Assume the same is true here. **Hand-verify every PR's real
diff before merging, even on a green sandbox test run** -- a `tests_failed`/timeout sandbox
verdict has repeatedly been confirmed environmental (cut off mid-pytest-collection), not a real
signal; the only reliable check is reading the real diff and running the real tests locally.

**None of these fixes require touching `tray/`.** Standard practice for this whole project:
never send `tray/` to self_dev.

**Ordering matters.** IPO4/IPO5 (the new strategy code + calendar fetcher) are independent of
everything else and can run in any order. IPO1 -> IPO2 -> IPO3 are a strict chain (each adds a
piece the next one calls) -- don't skip ahead if an earlier one in that chain fails. IPO6 depends
on all five of the others already being on master (it imports from `ipo_strategy.py`/
`ipo_calendar.py` and calls the `StrategySpec.risk_override_pct` field IPO1-3 add).

## Status: ready

## Next slice -- start here

- **Active:** IPO1 -- #1038
- **Model:** sonnet

Fresh campaign, nothing landed yet.

## Queue

Ordered by dependency chain, not severity (unlike TRADING-AUDIT-FIXES.md's queue) -- see
"Ordering matters" above.

- [ ] IPO1 -- #1038 -- StrategySpec gains a nullable risk_override_pct column (strategy_store.py)
- [ ] IPO2 -- #1039 -- RiskManager.check_order honors a per-call risk-pct override (risk_limits.py)
- [ ] IPO3 -- #1040 -- live_tick.py threads spec.risk_override_pct into check_order
- [ ] IPO4 -- #1041 -- hand-written IPO pop-then-fade strategy code (new ipo_strategy.py)
- [ ] IPO5 -- #1042 -- free IPO-calendar fetcher, no API key (new ipo_calendar.py)
- [ ] IPO6 -- #1043 -- wire it all together: weekly calendar refresh + per-tick dispatch (scheduler.py + main.py)

## Design summary (for hand-review context, not part of any single issue)

**The rule** (confirmed against real 2026 IPO data before filing -- see the grill session):
buy at the open on IPO day. Track the running peak price since entry. While the peak has never
reached +20% above entry, exit on a 3% pullback from the peak (this also covers the "hard stop"
case, since peak == entry until the position first moves up -- 3% off peak and 3% off entry are
the same thing at that point). Once the peak has reached +20% above entry at any point, tighten
to a 1% trailing stop for the rest of the hold. No separate flat take-profit or stop-loss price.

**Position sizing:** up to 25% of account equity per IPO trade (vs. the global 10%
`max_per_trade_risk_pct` every other strategy uses) -- IPO1-3 build the per-strategy override
this needs without loosening risk for the ~180 other already-registered strategies.

**Validation is the one deliberate exception in this whole design:** a brand-new IPO ticker has
zero price history before its first trading day, so it's structurally impossible to backtest it
through the normal Gauntlet (`_run_gauntlet`) before dispatching. IPO6's `_dispatch_due_ipos`
registers the strategy directly via `StrategyStore.save()`, bypassing the Gauntlet's per-symbol
backtest gate for this one case. The strategy *code itself* (IPO4) still needs to be validated
once against real historical IPO data before this campaign runs -- do this by hand after IPO4
lands (call `_run_gauntlet` directly with the code against a real past IPO ticker, e.g. one of
APMD/BSP/SCTX/JMKE/REF/LIME/BRVE/ATTO from the grill session's own backtest, confirm VALIDATED)
-- not a self_dev slice, an operational step.

**Data granularity:** `interval="5m"` for the whole hold, not a day-0-only intraday window that
switches to daily later -- `StrategySpec` only has one interval field, and building dynamic
mid-hold interval switching is out of scope for this campaign. 5-minute bars are strictly more
precise than daily for tracking the tight 1-3% trailing stops correctly; the tradeoff is more
data volume fetched per tick on a multi-week hold, which is a performance question, not a
correctness one, if it ever becomes a real problem.

**Notification:** both new tools (`check_ipo_calendar`, `dispatch_due_ipos`) post to the
Felix-wide Activity Log via the already-wired `self._record_activity_fn` -- same mechanism the
rest of the Trading panel's activity already uses, not a new alert path.

## Landed PRs

(none yet)
