import pandas as pd
import pytest

from cerebral.trading.trend_basket_strategy import TREND_BASKET_STRATEGY_CODE


def _run_strategy(df):
    ns = {"data": df}
    exec(TREND_BASKET_STRATEGY_CODE, {}, ns)
    return ns["position"]


def _make_df(closes, highs=None, lows=None, opens=None):
    if opens is None:
        opens = closes
    if highs is None:
        highs = [max(c, h) for c, h in zip(closes, opens)]
    if lows is None:
        lows = [min(c, l) for c, l in zip(closes, opens)]
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes}
    )


class TestTrendBasketStrategy:
    def test_stop_on_12_pullback(self):
        # Peak builds to 100, then close drops to 87 (< 88)
        closes = [1, 2, 3, 4, 5, 10, 20, 50, 100, 87, 88]
        opens = closes
        lows = [0.9, 1.8, 2.7, 3.6, 4.5, 9, 18, 45, 90, 86, 87]
        highs = closes
        df = _make_df(closes, highs, lows, opens)
        assert _run_strategy(df) == 0

    def test_hold_under_12_pullback(self):
        # Peak 100, pullback to 92 (> 88)
        closes = [1, 2, 3, 4, 5, 10, 20, 50, 100, 92, 93]
        opens = closes
        lows = [c - 0.1 for c in closes]
        highs = closes
        df = _make_df(closes, highs, lows, opens)
        assert _run_strategy(df) == 1

    def test_hard_exit_at_bar_20(self):
        # Flat price, no stop triggered, reaches index 20
        closes = [10] * 22
        opens = closes
        lows = [9] * 22
        highs = closes
        df = _make_df(closes, highs, lows, opens)
        assert _run_strategy(df) == 0

    def test_regression_same_bar_peak_inflation(self):
        # Bar i has huge High, but Close triggers stop.
        # Prev peak = 10. Stop threshold = 8.8.
        # Close = 8.0 (< 8.8) -> stop fires.
        # If peak updated first, new peak = 100, stop threshold = 88, no stop.
        closes = [10] * 5 + [8, 8, 8]
        opens = closes
        lows = [9.5] * 8
        highs = [10] * 5 + [100, 10, 10]  # Spike on bar 5
        df = _make_df(closes, highs, lows, opens)
        assert _run_strategy(df) == 0
