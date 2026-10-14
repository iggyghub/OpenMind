"""Intraday rules: contract, flat-by-close, no look-ahead, one behavioural check, idempotent registration."""
import numpy as np
import pandas as pd
import pytest

from cerebral.trading.intraday_rules import INTERVAL, RULES, register_intraday_rules
from cerebral.trading.strategy_store import StrategyStore


def _bars(days=8, seed=0):
    rng = np.random.default_rng(seed)
    parts = []
    price = 100.0
    for d in pd.bdate_range("2024-03-04", periods=days):
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=78, freq="5min")
        price *= 1 + rng.normal(0, 0.008)                     # overnight gap
        close = price * np.cumprod(1 + rng.normal(0, 0.0015, len(idx)))
        parts.append(pd.DataFrame({
            "Open": np.r_[price, close[:-1]], "High": close * 1.001, "Low": close * 0.999,
            "Close": close, "Volume": rng.integers(1_000, 5_000, len(idx)).astype(float),
        }, index=idx))
        price = close[-1]
    return pd.concat(parts)


def _run(code, bars):
    ns = {"pd": pd, "np": np}
    exec(code, ns)
    return pd.Series(ns["strategy"](bars), index=bars.index)


@pytest.mark.parametrize("claim", list(RULES))
def test_rule_contract_flat_by_close_and_causal(claim):
    bars = _bars()
    sig = _run(RULES[claim], bars)
    assert len(sig) == len(bars) and set(sig.unique()) <= {-1, 0, 1}
    minutes = bars.index.hour * 60 + bars.index.minute
    assert (sig[minutes >= 950] == 0).all()                    # never carried into the last bar of the day
    for cut in (130, 190, 333, 500):                           # a truncated history must give the same past
        assert (_run(RULES[claim], bars.iloc[:cut]).values == sig.values[:cut]).all()


def test_opening_range_breakout_goes_long_on_an_upside_break_and_holds_to_the_close():
    idx = pd.date_range("2024-03-04 09:30", periods=78, freq="5min")
    close = np.full(78, 100.0)
    close[24:] = 101.0                                         # breaks the 30-minute range at 11:30
    bars = pd.DataFrame({"Open": close, "High": close + 0.05, "Low": close - 0.05, "Close": close,
                         "Volume": np.full(78, 1000.0)}, index=idx)
    sig = _run(RULES[next(k for k in RULES if "opening-range" in k)], bars)
    assert (sig.iloc[:24] == 0).all() and (sig.iloc[24:75] == 1).all() and (sig.iloc[75:] == 0).all()


def test_register_saves_five_minute_rules_once_and_keeps_them_out_of_the_daily_sweep(tmp_path):
    store = StrategyStore(db_path=tmp_path / "s.db")
    assert register_intraday_rules(store) == len(RULES)
    assert register_intraday_rules(store) == 0
    specs = store.list_all()
    assert {s.interval for s in specs} == {INTERVAL}
    assert not any(s.cross_test_eligible for s in specs)
