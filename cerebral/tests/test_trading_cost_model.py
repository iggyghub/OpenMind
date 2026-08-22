import pytest
from typing import List, Tuple
from cerebral.trading.cost_model import SpreadCostModel, apply_costs_to_returns, compute_backtest_result, Trade

# Fixtures
def make_trade(index, value, price=1.0, direction="long"):
    return Trade(index=index, direction=direction, price=price, value=value)

def test_cost_model_deducts_from_returns():
    gross = [0.01, -0.005, 0.02]
    trades = [make_trade(0, 1000), make_trade(1, 1000)]
    config = {"min_spread_pct": 0.01, "max_spread_pct": 0.03}
    net = apply_costs_to_returns(gross, trades, config)
    
    # Cost per trade: 1000 * 0.02 / 10000 = 0.002
    assert net[0] == pytest.approx(0.01 - 0.002, abs=1e-6)
    assert net[1] == pytest.approx(-0.005 - 0.002, abs=1e-6)
    assert net[2] == pytest.approx(0.02, abs=1e-6)

def test_penny_stock_spread_default():
    model = SpreadCostModel()
    cost = model.compute_trade_cost_pct(1.50, "long")
    assert cost == pytest.approx(0.02, abs=1e-6)

def test_cost_config_override():
    model = SpreadCostModel(min_spread_pct=0.02, max_spread_pct=0.05)
    cost = model.compute_trade_cost_pct(2.00, "short")
    assert cost == pytest.approx(0.035, abs=1e-6)

def test_apply_costs_no_trades():
    gross = [0.01, 0.02]
    trades = []
    config = {"min_spread_pct": 0.01, "max_spread_pct": 0.03}
    net = apply_costs_to_returns(gross, trades, config)
    assert net == gross

def test_backtest_result_reports_gross_and_net():
    gross = [0.01, -0.01]
    trades = [make_trade(0, 2000)]
    config = {"min_spread_pct": 0.01, "max_spread_pct": 0.03}
    result = compute_backtest_result(gross, trades, config)
    
    assert result.cumulative_gross_return == pytest.approx(0.0, abs=1e-6)
    # Net should be lower due to cost deduction
    assert result.cumulative_net_return < result.cumulative_gross_return
    assert isinstance(result.net_returns, list)
