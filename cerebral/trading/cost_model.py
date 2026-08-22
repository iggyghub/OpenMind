from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class Trade:
    index: int
    direction: str
    price: float
    value: float
    cost_pct: float = 0.0

@dataclass
class BacktestResult:
    gross_returns: List[float]
    net_returns: List[float]
    trades: List[Trade]
    cost_config: Dict[str, Any]
    cost_per_trade_pct: Optional[float] = None
    
    @property
    def cumulative_gross_return(self) -> float:
        ret = 1.0
        for r in self.gross_returns:
            ret *= (1 + r)
        return ret - 1.0

    @property
    def cumulative_net_return(self) -> float:
        ret = 1.0
        for r in self.net_returns:
            ret *= (1 + r)
        return ret - 1.0

class SpreadCostModel:
    """Cost/slippage model for penny stocks."""
    def __init__(self, min_spread_pct: float = 0.01, max_spread_pct: float = 0.03):
        self.min_spread_pct = min_spread_pct
        self.max_spread_pct = max_spread_pct

    def compute_trade_cost_pct(self, price: float, direction: str) -> float:
        """Returns cost as a fraction of trade value."""
        return (self.min_spread_pct + self.max_spread_pct) / 2.0

def apply_costs_to_returns(
    gross_returns: List[float],
    trades: List[Trade],
    cost_config: Dict[str, Any],
    cost_per_trade_pct: Optional[float] = None,
    initial_capital: float = 10000.0
) -> List[float]:
    """Deducts trading costs from gross returns to produce net returns."""
    if cost_per_trade_pct is not None:
        cost_frac = cost_per_trade_pct
    else:
        min_spread = cost_config.get("min_spread_pct", 0.01)
        max_spread = cost_config.get("max_spread_pct", 0.03)
        cost_frac = (min_spread + max_spread) / 2.0
        
    costs_by_index = {}
    for trade in trades:
        idx = trade.index
        cost_val = trade.value * cost_frac
        costs_by_index[idx] = costs_by_index.get(idx, 0.0) + cost_val
        
    net_returns = list(gross_returns)
    for i in range(len(net_returns)):
        if i in costs_by_index:
            net_returns[i] -= costs_by_index[i] / initial_capital
            
    return net_returns

def compute_backtest_result(
    gross_returns: List[float],
    trades: List[Trade],
    cost_config: Dict[str, Any],
    cost_per_trade_pct: Optional[float] = None,
    initial_capital: float = 10000.0
) -> BacktestResult:
    """Computes backtest results with both gross and net returns."""
    net_returns = apply_costs_to_returns(
        gross_returns, trades, cost_config, cost_per_trade_pct, initial_capital
    )
    return BacktestResult(
        gross_returns=gross_returns,
        net_returns=net_returns,
        trades=trades,
        cost_config=cost_config,
        cost_per_trade_pct=cost_per_trade_pct
    )
