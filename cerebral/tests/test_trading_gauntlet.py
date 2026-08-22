import pytest
from typing import List, Tuple, Sequence, Any
from cerebral.trading.cost_model import Trade
from cerebral.trading.gauntlet import oos_test, walk_forward, GateResult

class MockStrategy:
    """Mock strategy that returns predefined returns and trades."""
    def __init__(self, returns: List[float], trades: List[Trade]):
        self.returns = returns
        self.trades = trades
        self.is_fitted = False
        
    def __call__(self, data: Sequence):
        if not self.is_fitted:
            self.is_fitted = True
            return self
        return self.evaluate(data)
        
    def evaluate(self, data: Sequence) -> Tuple[List[float], List[Trade]]:
        return self.returns, self.trades

def make_trade(index, value=1000, price=1.0, direction="long"):
    return Trade(index=index, direction=direction, price=price, value=value)

def test_oos_gate_passes():
    returns = [0.05] * 10
    trades = [make_trade(i, 1000) for i in range(10)]
    strat = MockStrategy(returns, trades)
    
    data = list(range(20))
    res = oos_test(strat, data, holdout_pct=0.5)
    
    assert res.passed is True
    assert res.metrics["oos_cumulative_net_return"] > 0
    assert res.metrics["holdout_pct"] == 0.5

def test_oos_gate_fails():
    # Strategy barely profitable gross, but high value trades make it net negative
    returns = [0.001] * 10
    trades = [make_trade(i, 10000) for i in range(10)]
    strat = MockStrategy(returns, trades)
    
    data = list(range(20))
    res = oos_test(strat, data, holdout_pct=0.5)
    
    assert res.passed is False
    assert res.metrics["oos_cumulative_net_return"] < 0

def test_walk_forward_passes():
    returns = [0.02] * 5
    trades = [make_trade(i, 1000) for i in range(5)]
    strat = MockStrategy(returns, trades)
    
    data = list(range(20))
    res = walk_forward(strat, data, fit_ratio=4)
    
    assert res.passed is True
    assert res.metrics["fit_ratio"] == 4

def test_walk_forward_fails():
    returns = [0.0001] * 5
    trades = [make_trade(i, 20000) for i in range(5)]
    strat = MockStrategy(returns, trades)
    
    data = list(range(20))
    res = walk_forward(strat, data, fit_ratio=4)
    
    assert res.passed is False
    assert res.metrics["wf_cumulative_net_return"] < 0

def test_gate_result_structure():
    returns = [0.01] * 5
    trades = [make_trade(i, 1000) for i in range(5)]
    strat = MockStrategy(returns, trades)
    
    data = list(range(15))
    res = oos_test(strat, data)
    
    assert hasattr(res, 'passed')
    assert hasattr(res, 'metrics')
    assert hasattr(res, 'details')
    assert isinstance(res.metrics, dict)
    assert "oos_cumulative_net_return" in res.metrics
