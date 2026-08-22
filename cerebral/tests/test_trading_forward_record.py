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
    """Redirect the module's DB path to a per-test tmp dir for isolation."""
    monkeypatch.setattr("cerebral.trading.forward_record._DB_PATH", str(tmp_path / "test_fills.db"))


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
    
    mean, lower, upper, sufficient = record.compute_expectancy_ci()
    assert not sufficient
    assert mean == 22.5  # avg of 0,5,10,...,45
    
    # Add more to reach threshold
    for i in range(20):
        record.add_fill("NVDA", "buy", 1.0, 500.0 + i, pnl=i * 2.0)
        
    mean2, lower2, upper2, sufficient2 = record.compute_expectancy_ci()
    assert sufficient2
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
    mean, lower, upper, sufficient = record.compute_expectancy_ci()
    assert mean == 0.0
    assert lower == 0.0
    assert upper == 0.0
    assert not sufficient
    record.close()
