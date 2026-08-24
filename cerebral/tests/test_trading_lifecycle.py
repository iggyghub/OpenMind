"""Unit tests for StrategyLifecycle (S6 graduation, retirement, sizing)."""
import pytest
from unittest.mock import MagicMock

from cerebral.trading.lifecycle import StrategyLifecycle, StrategyState
from cerebral.trading.alerts import AlertDispatcher, StructuredAlert


@pytest.fixture
def dispatcher():
    return AlertDispatcher()


@pytest.fixture
def lifecycle(dispatcher, tmp_path):
    return StrategyLifecycle(alert_dispatcher=dispatcher, db_path=tmp_path / "lifecycle.sqlite")


def test_graduation_requires_30_trades_and_positive_ci(lifecycle):
    mock_record = MagicMock()
    mock_record.compute_expectancy_ci.return_value = (0.5, 0.1, 0.9, True, 30, 30)
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


def test_retirement_rolling_ci_uses_per_trade_pnl(lifecycle):
    """Regression: rolling-CI check must diff consecutive equity points, not
    (last_equity - equity[i-1]), which collapses variance and can mask a
    losing strategy (false negative -> keeps trading live with real money)."""
    import numpy as np
    state = lifecycle.get_state("ci_check")
    state.status = "live"

    np.random.seed(3)
    pnls = np.random.normal(0.3, 1.0, 35).tolist()
    for p in pnls:
        lifecycle.update_live_fill("ci_check", pnl=p)

    recent = pnls[-30:]
    mean = sum(recent) / len(recent)
    se = (sum((x - mean) ** 2 for x in recent) / (len(recent) - 1)) ** 0.5 / len(recent) ** 0.5
    expected_halt = (mean - 1.96 * se) <= 0
    assert expected_halt, "fixture should exercise the halt branch"

    retired = lifecycle.check_retirement("ci_check", worst_backtest_dd=0.0)
    assert retired is True
    assert state.status == "halted"


def test_halted_strategies_ignore_fills(lifecycle):
    state = lifecycle.get_state("ignore_test")
    state.status = "halted"
    lifecycle.update_live_fill("ignore_test", pnl=5.0)
    
    assert len(state.live_equity_curve) == 0
    assert state.live_trade_count == 0


def test_alerts_emitted_on_graduation(lifecycle):
    mock_record = MagicMock()
    mock_record.compute_expectancy_ci.return_value = (0.2, 0.05, 0.35, True, 30, 30)
    lifecycle.check_graduation("alert_test", mock_record)

    alerts = lifecycle.get_alert_history()
    assert len(alerts) == 1
    assert alerts[0].event_type == "paper_to_live_graduation"
    assert alerts[0].severity == "info"


# ── S12 Part A: status must survive a restart (a fresh process = a fresh
# StrategyLifecycle instance against the same db_path) ──────────────────────

def test_graduation_survives_a_fresh_instance(tmp_path):
    """The actual bug: a Felix restart used to silently drop every
    strategy back to "paper" because StrategyLifecycle kept state in
    memory only. Simulates a restart by throwing away the instance and
    constructing a brand new one against the same path."""
    db_path = tmp_path / "lifecycle.sqlite"
    lifecycle = StrategyLifecycle(db_path=db_path)
    mock_record = MagicMock()
    mock_record.compute_expectancy_ci.return_value = (0.2, 0.05, 0.35, True, 30, 30)
    lifecycle.check_graduation("restart_test", mock_record)
    assert lifecycle.get_state("restart_test").status == "live"

    fresh = StrategyLifecycle(db_path=db_path)  # a new process, same db

    state = fresh.get_state("restart_test")
    assert state.status == "live"
    assert state.position_size_pct == 0.25
    assert state.promoted_at is not None


def test_ramp_and_live_equity_curve_survive_a_fresh_instance(tmp_path):
    db_path = tmp_path / "lifecycle.sqlite"
    lifecycle = StrategyLifecycle(db_path=db_path)
    lifecycle.get_state("ramp_test").status = "live"
    lifecycle._save_state(lifecycle.get_state("ramp_test"))
    for _ in range(35):
        lifecycle.update_live_fill("ramp_test", pnl=1.0)
    lifecycle.apply_position_ramp("ramp_test")

    fresh = StrategyLifecycle(db_path=db_path)

    state = fresh.get_state("ramp_test")
    assert state.live_trade_count == 35
    assert state.position_size_pct == 0.50  # ramped past 30 trades
    assert state.live_equity_curve[-1] == 35.0
    assert state.peak_live_equity == 35.0


def test_halt_survives_a_fresh_instance(tmp_path):
    db_path = tmp_path / "lifecycle.sqlite"
    lifecycle = StrategyLifecycle(db_path=db_path)
    lifecycle.get_state("halt_test").status = "live"
    lifecycle._save_state(lifecycle.get_state("halt_test"))
    lifecycle._halt_strategy("halt_test", "test halt")

    fresh = StrategyLifecycle(db_path=db_path)

    assert fresh.get_state("halt_test").status == "halted"


def test_two_instances_against_different_paths_do_not_share_state(tmp_path):
    """The regression this whole fix exists to prevent: before db_path
    injection existed, every StrategyLifecycle() in the test suite shared
    ONE real file, so a strategy graduated in one test leaked "live"
    status into an unrelated test using the same strategy name."""
    a = StrategyLifecycle(db_path=tmp_path / "a.sqlite")
    b = StrategyLifecycle(db_path=tmp_path / "b.sqlite")
    a.get_state("s1").status = "live"
    a._save_state(a.get_state("s1"))

    assert b.get_state("s1").status == "paper"
