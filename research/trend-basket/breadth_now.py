import sys
sys.path.insert(0, r"C:\OpenMind")
from datetime import datetime, timedelta, timezone
import pandas as pd
from cerebral.trading.broker import AlpacaBrokerClient
from cerebral.trading.discovery import build_dynamic_universe
from cerebral.trading.trend_basket_selection import compute_breadth
from cerebral.trading_data import fetch_ohlcv

broker = AlpacaBrokerClient(env="paper")


def fetch_sel(symbol, n, freq):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(n * 2.5) + 5)
    df = fetch_ohlcv(symbol, start.isoformat(), end.isoformat(), interval="1d")
    if df is None or df.empty or "Close" not in df.columns:
        return pd.DataFrame(columns=["close"])
    return df.tail(n).rename(columns={"Close": "close"})[["close"]]


u = build_dynamic_universe(broker, fetch_ohlcv, movers_top=50, actives_top=100, min_price=2.0, min_dollar_volume=10_000_000.0)
print("pool size:", len(u), u[:10])
r = compute_breadth(u, fetch_sel)
print("breadth:", r.breadth, "above:", len(r.above_ma), "below:", len(r.below_ma))
