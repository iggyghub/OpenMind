"""(1) What signal does live_tick get from TREND_BASKET_STRATEGY_CODE on its real 180-day window?
(2) Real run_gauntlet pass rate on historical basket picks, which gates fail, and whether
passers outperform failers over the following 20-day trade."""
import sys, collections, statistics
sys.path.insert(0, r"C:\OpenMind")
sys.path.insert(0, r"C:\Users\iggy\AppData\Local\Temp\claude\C--OpenMind\5cb6d305-c85c-4ead-ac51-0bd71200d9ec\scratchpad")
import pandas as pd
from datetime import timedelta
from cerebral.trading.historical_bars import get_daily_bars
from cerebral.trading.trend_basket_strategy import TREND_BASKET_STRATEGY_CODE as CODE
from cerebral.trading.replay import run_bars
from cerebral.trading.gauntlet import run_gauntlet
from cerebral.trading.sandboxed_eval import evaluate_signals_verbose
from trend_basket_harness import Universe, simulate_trade

# (1) live signal
for sym in ("AAPL", "NVDA", "SOFI"):
    d = get_daily_bars(sym, "2026-03-04", "2026-09-01")
    sig, err = evaluate_signals_verbose(CODE, d)
    print(f"live window {sym}: {len(d)} bars, last signal = {sig[-1] if sig else None}, ones = {sum(1 for s in sig if s == 1)} {err or ''}")

# (2) gauntlet on historical picks
u = Universe()
vol20 = u.returns.rolling(20).std()
score = u.mom20 * vol20
events = [e for e in u.entry_events() if e.year >= 2008]
events = events[:: max(1, len(events) // 25)][:25]  # ~25 dates spread across history
verdicts, fails, fwd = collections.Counter(), collections.Counter(), {"VALIDATED": [], "UNVALIDATED": []}
for day in events:
    idx = u.price.index.get_loc(day)
    picks = list(score.iloc[idx - 1].dropna().sort_values(ascending=False).index[:10])
    passed = 0
    for sym in picks:
        bars = get_daily_bars(sym, (day - timedelta(days=365)).strftime("%Y-%m-%d"), day.strftime("%Y-%m-%d"))
        if len(bars) < 60:
            continue
        last = float(bars["Close"].iloc[-1])
        qty = 100 * 0.10 / last
        card = run_gauntlet(
            lambda b, p: (lambda r: (r[0], r[2]))(run_bars(CODE, b, "1d")), bars, {}, bars.copy(),
            position_sizes=pd.Series([qty] * len(bars), index=bars.index),
            n_permutations=300, auto_promote=False, interval="1d",
        )
        verdicts[card.verdict] += 1
        passed += card.verdict == "VALIDATED"
        for g in card.gates:
            if not g.passed:
                fails[g.name] += 1
        t = simulate_trade(u, sym, idx)
        if t:
            fwd[card.verdict].append(t[0])
    print(f"{day.date()}: {passed}/10 passed")

n = sum(verdicts.values())
print(f"\npass rate {verdicts['VALIDATED']}/{n} = {verdicts['VALIDATED']/n:.0%}")
print("gate failures:", dict(fails.most_common()))
for k, v in fwd.items():
    if v:
        print(f"{k}: n={len(v)} median 20d trade {statistics.median(v):+.2%} win {sum(x > 0 for x in v)/len(v):.0%}")
