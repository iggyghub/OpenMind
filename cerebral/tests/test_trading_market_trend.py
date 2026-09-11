import pandas as pd
import pytest

from cerebral.trading.market_trend import MarketTrendGate


def _fake_fetch(closes):
    def fetch(symbol, start, end, interval):
        return pd.DataFrame({"Close": closes})
    return fetch


def test_market_trend_up_on_positive_close_change():
    gate = MarketTrendGate(symbol="SPY")
    reading = gate.refresh(_fake_fetch([400.0, 404.0]))
    assert reading.label == "UP"
    assert reading.pct_change == pytest.approx(1.0)
    assert reading.symbol == "SPY"


def test_market_trend_down_on_negative_close_change():
    gate = MarketTrendGate()
    reading = gate.refresh(_fake_fetch([400.0, 396.0]))
    assert reading.label == "DOWN"
    assert reading.pct_change == pytest.approx(-1.0)


def test_market_trend_flat_within_band():
    gate = MarketTrendGate()
    reading = gate.refresh(_fake_fetch([400.0, 400.5]))
    assert reading.label == "FLAT"


def test_market_trend_caches_within_the_same_day():
    calls = []

    def counting_fetch(symbol, start, end, interval):
        calls.append(1)
        return pd.DataFrame({"Close": [400.0, 404.0]})

    gate = MarketTrendGate()
    gate.refresh(counting_fetch)
    gate.refresh(counting_fetch)
    assert len(calls) == 1


def test_market_trend_fails_open_on_fetch_error():
    def broken_fetch(symbol, start, end, interval):
        raise RuntimeError("network down")

    gate = MarketTrendGate()
    reading = gate.refresh(broken_fetch)
    assert reading.label == "FLAT"  # default reading, unchanged
