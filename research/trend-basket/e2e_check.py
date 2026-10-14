"""Real sandbox + real run_strategy_tick on real bars with the entry-dated trend-basket code."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, r"C:\OpenMind")
from cerebral.trading.historical_bars import get_daily_bars
from cerebral.trading.sandboxed_eval import evaluate_signals_verbose
from cerebral.trading.trend_basket_strategy import trend_basket_code
from cerebral.trading.live_tick import run_strategy_tick
from cerebral.trading.strategy_store import StrategySpec
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.forward_record import ForwardRecord
from cerebral.trading.risk_limits import RiskManager, RiskConfig

import pandas as pd, datetime as dt
bars = get_daily_bars("NVDA", "2026-03-04", "2026-09-01")
bars.index = bars.index + (pd.Timestamp(dt.date.today()) - bars.index[-1])  # end today: pass the stale-data guard
entry = str(bars.index[-3].date())
code = trend_basket_code(entry)
sig, err = evaluate_signals_verbose(code, bars)
print("sandbox:", len(sig), "bars, last signal", sig[-1], "ones", sum(sig), err or "")

fetch = lambda s, a, b, interval="1d": bars
last = float(bars["Close"].iloc[-1])
broker = StubBrokerClient({"starting_cash": 100.0})
spec = StrategySpec("Trend basket: x @NVDA", "NVDA", code, qty=10.0 / (last * 0.99))  # sized at a 1%-lower prior close
with tempfile.TemporaryDirectory() as d:
    import cerebral.trading.forward_record as frm
    frm._DB_PATH = Path(d) / "fr.db"
    rec = ForwardRecord()
    res = run_strategy_tick(spec.strategy_id, spec, broker, rec, fetch=fetch,
                            risk=RiskManager(RiskConfig(max_per_trade_risk_pct=10.0)))
    print("tick:", res)
    print("orders:", [(o.symbol, round(o.qty * last, 2)) for o in broker._orders.values()])
