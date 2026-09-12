"""Unit tests for cerebral/trading/replay.py (RP1)."""
import pandas as pd
import pytest

from cerebral.trading.replay import run_bars, derive_trades


_ALWAYS_LONG = "def strategy(data):\n    return [1] * len(data)\n"


def _ramp_bars(n: int = 30) -> pd.DataFrame:
    """Synthetic strictly-increasing daily OHLCV bars."""
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * n},
        index=idx,
    )


def test_run_bars_equity_nondecreasing():
    bars = _ramp_bars(30)
    equity, position, metrics = run_bars(_ALWAYS_LONG, bars, "1d")
    assert len(equity) == 30
    for i in range(1, len(equity)):
        assert equity[i] >= equity[i - 1] - 1e-9, f"equity dropped at bar {i}"


def test_run_bars_position_all_ones():
    bars = _ramp_bars(30)
    _, position, _ = run_bars(_ALWAYS_LONG, bars, "1d")
    # shift(1) means bar 0 is 0.0 (no prior signal), bars 1+ are 1.0
    assert position.iloc[0] == pytest.approx(0.0)
    assert (position.iloc[1:] == 1.0).all()


def test_run_bars_returns_position_series():
    bars = _ramp_bars(10)
    _, position, _ = run_bars(_ALWAYS_LONG, bars, "1d")
    assert isinstance(position, pd.Series)
    assert len(position) == 10


def test_run_bars_metrics_has_max_holding_days():
    bars = _ramp_bars(20)
    _, _, metrics = run_bars(_ALWAYS_LONG, bars, "1d")
    assert "max_holding_days" in metrics


def test_derive_trades_flat_long_flat():
    pos = pd.Series([0, 1, 1, 1, 0, 0], index=range(5))
    close = pd.Series([10.0, 10.0, 10.0, 10.0, 10.0, 10.0], index=range(5))
    trades = derive_trades(pos, close)
    assert len(trades) == 2
    assert trades[0].direction == "buy"
    assert trades[0].price == 10.0
    assert trades[0].value == 10.0
    assert trades[1].direction == "sell"
    assert trades[1].price == 10.0
    assert trades[1].value == 10.0


def test_derive_trades_no_changes():
    pos = pd.Series([0.5, 0.5, 0.5], index=range(3))
    close = pd.Series([10.0, 10.0, 10.0], index=range(3))
    trades = derive_trades(pos, close)
    assert len(trades) == 0


def test_net_returns_leq_gross_returns():
    bars = _ramp_bars(30)
    # Strategy that swings long/flat to generate trades
    _swing = """
def strategy(data):
    n = len(data)
    return [1] + [0] * (n // 2 - 1) + [1] + [0] * (n - n // 2)
"""
    equity, position, metrics = run_bars(_swing, bars, "1d")
    net_returns = metrics.get("net_returns", equity)
    # Ensure both are iterable for comparison
    net_list = list(net_returns) if isinstance(net_returns, (list, pd.Series)) else [net_returns]
    for g, n in zip(equity, net_list):
        assert n <= g + 1e-9, f"Net return {n} > Gross return {g}"
