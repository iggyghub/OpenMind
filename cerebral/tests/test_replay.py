"""Unit tests for cerebral/trading/replay.py (RP1)."""
import pandas as pd
import pytest

from cerebral.trading.replay import run_bars


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
