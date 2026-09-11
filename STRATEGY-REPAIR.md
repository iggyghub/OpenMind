# STRATEGY-REPAIR.md -- Strategy Repair campaign driver

Root cause, found live in `cerebral.err.log` (2026-09-11 session): `to_strategy()`
(`cerebral/trading_ideas.py`) generates each strategy's pandas code in one LLM shot with
no self-correction loop. Hallucinated method calls (`Series.sign`, `Series.maximum`,
`Series.combine_max`, `Index.between`, `Rolling.any`, etc. -- 14+ distinct
AttributeError/TypeError signatures, 100+ occurrences, almost all from book-ingested
strategies) go straight to `cerebral/trading/sandboxed_eval.py`, which silently degrades
to an all-flat signal on any failure. The strategy then just fails the gauntlet's normal
gates for a reason nobody can see -- no crash, no signal to the operator that it was a
code bug rather than a bad hypothesis.

Filing one issue per hallucinated method name would be whack-a-mole: each strategy's
code is uniquely generated, so fixing today's `Series.sign` typo does nothing for
tomorrow's `Series.combine_max` one. This campaign instead adds one bounded
repair-retry mechanism at the generation layer: when the sandbox reports a real
code-level failure, feed that error back into a second `to_strategy` call before giving
up.

**Read before running this campaign:** every slice landed against `cerebral/trading/`
across this project's history (TRADING.md, 48+ slices; TRADING-AUDIT-FIXES.md, 21 more)
has needed hand-review, and the large majority shipped a real bug self_dev's own tests
didn't catch. Assume the same here. **Hand-verify every PR's actual diff before
merging, even on a green sandbox test run.**

**None of these fixes require touching `tray/`** except SR4's one-field addition to an
existing broadcast dict -- no new tray rendering in this campaign.

## Status: ready

## Next slice -- start here

- **Active:** SR3 -- #1178
- **Model:** sonnet

## Queue

- [x] SR1 -- #1176 -- sandboxed_eval.py: expose the real failure reason without changing evaluate_signals' contract
- [x] SR2 -- #1177 -- trading_ideas.py: give to_strategy an optional repair prompt
- [ ] SR3 -- #1178 -- plugins/scheduler.py: wire one bounded repair retry into _run_gauntlet
- [ ] SR4 -- #1179 -- observability: track how often the repair retry actually helps

## Landed PRs

- SR1 -- #1182 -- landed by hand (the autonomous loop's own outer process didn't
  survive a Claude Code session restart mid-attempt; the partial working-tree diff
  it left was inspected, verified complete/correct against the issue spec, tested,
  and finished/merged directly rather than re-run from scratch)
- SR2 -- #1183 -- to_strategy repair prompt params

## SAFETY

- The repair retry is bounded to exactly ONE extra `to_strategy` call per strategy,
  ever. No loop, no re-retry on the repaired code's own failure. This is a cost/latency
  ceiling (one extra LLM call per originally-broken strategy), not just a safety one --
  do not let a later slice turn this into an unbounded retry loop.
- This campaign touches only the generation/backtest-time path (`_run_gauntlet`,
  `to_strategy`, `evaluate_signals`). It must NOT touch `live_tick.py`'s live dispatch
  path, `risk_limits.py`, or any broker/order code -- this is about generating better
  candidate strategies before they're registered, not about live trading behavior.
- `evaluate_signals`'s existing return contract (`List[int]`, degrade-to-flat on any
  failure) must stay byte-for-byte unchanged for every caller that isn't
  `_run_gauntlet` -- `evaluate_signals_verbose` is additive, not a replacement.
