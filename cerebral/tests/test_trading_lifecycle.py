"""Unit tests for StrategyLifecycle (S6 graduation, retirement, sizing)."""
import pytest
from unittest.mock import MagicMock

from cerebral.trading.lifecycle import StrategyLifecycle, StrategyState
from cerebral.trading.alerts import AlertDispatcher, StructuredAlert


@pytest.fixture
def dispatcher():
    return AlertDispatcher()


@pytest.fixture
def lifecycle(dispatcher):
    return StrategyLifecycle(alert_dispatcher=dispatcher)


def test_graduation_requires_30_trades_and_positive_ci(lifecycle):
    mock_record = MagicMock()
    mock_record.compute_expectancy_ci.return_value = (0.5, 0.1, 0.9, True)
    state = lifecycle.get_state("test_strat")
    assert state.status == "paper"
    
    graduated = lifecycle.check_graduation("test_strat", mock_record)
    assert graduated is True
    assert state.status == "live"
    assert state.position_size_pct == 0.25
    assert state.promoted_at is not None


def test_position_size_ramp(lifecycle):
    state = lifecycle.get_state("ramp_test")
    state.status = "live"
    
    state.live_trade_count = 15
    assert lifecycle.apply_position_ramp("ramp_test") == 0.25
    
    state.live_trade_count = 45
    assert lifecycle.apply_position_ramp("ramp_test") == 0.50
    
    state.live_trade_count = 75
    assert lifecycle.apply_position_ramp("ramp_test") == 1.0


def test_retirement_on_drawdown_breach(lifecycle):
    state = lifecycle.get_state("dd_fail")
    state.status = "live"
    state.live_equity_curve = [1000.0, 940.0]
    state.peak_live_equity = 1000.0
    
    retired = lifecycle.check_retirement("dd_fail", worst_backtest_dd=20.0)
    # DD = 60. 2x20 = 40. 60 > 40 -> halts
    assert state.status == "halted"


def test_halted_strategies_ignore_fills(lifecycle):
    state = lifecycle.get_state("ignore_test")
    state.status = "halted"
    lifecycle.update_live_fill("ignore_test", pnl=5.0)
    
    assert len(state.live_equity_curve) == 0
    assert state.live_trade_count == 0


def test_alerts_emitted_on_graduation(lifecycle):
    mock_record = MagicMock()
    mock_record.compute_expectancy_ci.return_value = (0.2, 0.05, 0.35, True)
    lifecycle.check_graduation("alert_test", mock_record)
    
    alerts = lifecycle.get_alert_history()
    assert len(alerts) == 1
    assert alerts[0].event_type == "paper_to_live_graduation"
    assert alerts[0].severity == "info"
