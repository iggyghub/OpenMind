"""Unit tests for the forward_record module.

Tests persistence, CI computation, 30-trade threshold, and equity curve.
All broker interactions use StubBrokerClient where applicable.
"""
import os
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.forward_record import ForwardRecord


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect the module's DB path to a per-test tmp dir for isolation.

    Must stay a Path, not str(...) -- __post_init__ calls _DB_PATH.parent,
    a pathlib-only method (forward_record.py uses pathlib throughout,
    unlike trading_data.py's os.path-based _CACHE_DIR, which does accept
    a plain string).
    """
    monkeypatch.setattr("cerebral.trading.forward_record._DB_PATH", tmp_path / "test_fills.db")


def test_add_fill_persists_data():
    """add_fill correctly writes to SQLite."""
    record = ForwardRecord()
    record.add_fill("AAPL", "buy", 10.0, 150.0, fees=1.5, pnl=0.0)
    record.add_fill("AAPL", "sell", 10.0, 160.0, fees=1.5, pnl=95.0)
    
    assert record.trade_count() == 2
    fills = record.get_fills()
    assert len(fills) == 2
    assert fills[0]["symbol"] == "AAPL"
    record.close()


def test_compute_expectancy_ci():
    """CI correctly computes mean, bounds, and sufficient flag."""
    record = ForwardRecord()
    # Add 10 trades
    for i in range(10):
        record.add_fill("TSLA", "buy", 1.0, 100.0 + i, pnl=i * 5.0)
    
    # floor=1: isolate the trade-count check this test targets -- all fills
    # land "now" (one real distinct day), so the default floor of 30 would
    # fail this on the distinct-days axis instead of the one being tested.
    mean, lower, upper, sufficient, n, distinct_days = record.compute_expectancy_ci(floor=1)
    assert not sufficient
    assert mean == 22.5  # avg of 0,5,10,...,45
    assert n == 10

    # Add more to reach threshold
    for i in range(20):
        record.add_fill("NVDA", "buy", 1.0, 500.0 + i, pnl=i * 2.0)

    mean2, lower2, upper2, sufficient2, n2, distinct_days2 = record.compute_expectancy_ci(floor=1)
    assert sufficient2
    assert n2 == 30
    assert distinct_days2 == 1  # all fills recorded "now" -- one real trading day
    assert mean2 > 0
    record.close()


def test_equity_curve():
    """get_equity_curve returns chronological cumulative PnL."""
    record = ForwardRecord()
    record.add_fill("MSFT", "buy", 1.0, 100.0, pnl=10.0)
    record.add_fill("MSFT", "buy", 1.0, 105.0, pnl=-5.0)
    record.add_fill("MSFT", "sell", 1.0, 110.0, pnl=5.0)
    
    curve = record.get_equity_curve()
    assert len(curve) == 3
    assert abs(curve[0] - 10.0) < 0.001
    assert abs(curve[1] - 5.0) < 0.001
    assert abs(curve[2] - 10.0) < 0.001
    record.close()


def test_stub_broker_integration():
    """Tests that paper trading flow uses StubBrokerClient correctly."""
    stub = StubBrokerClient()
    record = ForwardRecord()
    
    # Simulate broker placing an order and returning a fill
    order = stub.place_order("GOOGL", 5, "buy", "market")
    # In real flow, broker would update position/pnl, here we simulate fill recording
    # We manually call add_fill to mimic broker callback on fill
    record.add_fill("GOOGL", "buy", 5.0, 2800.0, fees=0.0, pnl=0.0)
    
    assert record.trade_count() == 1
    assert order.status == "FILLED"
    record.close()


def test_zero_trades_ci():
    """CI returns zeros and insufficient flag when no trades exist."""
    record = ForwardRecord()
    mean, lower, upper, sufficient, n, distinct_days = record.compute_expectancy_ci()
    assert mean == 0.0
    assert lower == 0.0
    assert upper == 0.0
    assert not sufficient
    assert n == 0
    assert distinct_days == 0
    record.close()


def _add_fill_on_day(record, day_iso, symbol, side, qty, price, pnl, strategy_id="global"):
    """Insert a fill with a controlled timestamp -- add_fill() always stamps
    "now", with no way to inject a date through the public API. Bypasses it
    with a direct insert instead of monkeypatching the datetime CLASS
    forward_record.py imports (`from datetime import datetime` -- patching
    "cerebral.trading.forward_record.datetime" replaces that class with
    something else entirely, breaking every other datetime.now() call in
    the module, not just the one under test)."""
    record._con.execute(
        "INSERT INTO forward_fills (timestamp, phase, symbol, side, qty, price, fees, pnl, strategy_id) "
        "VALUES (?, 'paper', ?, ?, ?, ?, 0.0, ?, ?)",
        (f"{day_iso}T12:00:00+00:00", symbol, side, qty, price, pnl, strategy_id),
    )
    record._con.commit()


def test_intraday_cluster_fails_distinct_days_floor():
    """S23: 30+ trades on the same day must fail graduation due to the
    distinct-days floor. Old trade-count-only logic would have wrongly
    passed this -- every fill lands on the same calendar date."""
    record = ForwardRecord()
    for i in range(35):
        _add_fill_on_day(record, "2026-07-19", "AAPL", "buy", 1.0, 100.0 + i, pnl=i * 2.0)

    mean, lower, upper, is_sufficient, n, distinct_days = record.compute_expectancy_ci()
    assert n == 35
    assert distinct_days == 1
    assert not is_sufficient  # fails because distinct_days(1) < floor(30)
    record.close()


def test_enough_trades_across_enough_distinct_days_is_sufficient():
    """Unchanged from today's behavior for a real daily-bar strategy: 30
    trades spread across 30 distinct days passes, same as before S23."""
    record = ForwardRecord()
    for i in range(30):
        _add_fill_on_day(record, f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "AAPL", "buy", 1.0,
                          100.0 + i, pnl=i - 15.0)

    mean, lower, upper, is_sufficient, n, distinct_days = record.compute_expectancy_ci(floor=30)
    assert n == 30
    assert distinct_days == 30
    assert is_sufficient
    record.close()


def test_get_total_pnl_grand_total():
    """get_total_pnl returns the sum of PnL across all strategies, all-time."""
    record = ForwardRecord()
    record.add_fill("AAPL", "buy", 1.0, 100.0, pnl=10.0, strategy_id="strat_a")
    record.add_fill("TSLA", "sell", 1.0, 200.0, pnl=20.0, strategy_id="strat_b")
    record.add_fill("MSFT", "buy", 1.0, 150.0, pnl=-5.0, strategy_id="strat_a")
    
    total = record.get_total_pnl()
    assert total == pytest.approx(25.0)  # 10.0 + 20.0 + -5.0
    record.close()


def test_get_all_fills_cross_strategy():
    """get_all_fills returns all fills across strategies, oldest first, and respects limit."""
    record = ForwardRecord()
    _add_fill_on_day(record, "2026-01-01", "AAPL", "buy", 1.0, 100.0, pnl=1.0, strategy_id="strat_a")
    _add_fill_on_day(record, "2026-01-03", "GOOGL", "buy", 1.0, 2000.0, pnl=2.0, strategy_id="strat_b")
    _add_fill_on_day(record, "2026-01-02", "AAPL", "sell", 1.0, 110.0, pnl=5.0, strategy_id="strat_a")
    
    all_fills = record.get_all_fills()
    assert len(all_fills) == 3
    # Oldest first check
    assert all_fills[0]["timestamp"] < all_fills[1]["timestamp"] < all_fills[2]["timestamp"]
    # Verify all strategies and symbols are present
    assert all_fills[0]["strategy_id"] == "strat_a"
    assert all_fills[1]["strategy_id"] == "strat_a"
    assert all_fills[2]["strategy_id"] == "strat_b"
    
    # Test limit
    limited_fills = record.get_all_fills(limit=2)
    assert len(limited_fills) == 2
    assert limited_fills[1]["timestamp"] == all_fills[1]["timestamp"]
    
    record.close()


def test_reset_paper_clears_only_phase_paper():
    """reset_paper() deletes only 'paper' phase fills, leaves 'live' alone, returns deleted count."""
    record = ForwardRecord()
    record.add_fill("AAPL", "buy", 10.0, 150.0, pnl=0.0, phase="paper")
    record.add_fill("GOOGL", "buy", 5.0, 2800.0, pnl=0.0, phase="paper")
    record.add_fill("TSLA", "sell", 1.0, 200.0, pnl=10.0, phase="live")
    
    deleted_count = record.reset_paper()
    assert deleted_count == 2
    
    remaining = record.get_all_fills()
    assert len(remaining) == 1
    assert remaining[0]["symbol"] == "TSLA"
    assert remaining[0]["phase"] == "live"
    record.close()
