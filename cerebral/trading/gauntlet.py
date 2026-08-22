from typing import Any, Dict, List, Callable, Tuple, Optional, Sequence
from dataclasses import dataclass

from .cost_model import apply_costs_to_returns, Trade

@dataclass
class GateResult:
    passed: bool
    metrics: Dict[str, Any]
    details: str = ""

def oos_test(
    strategy_fn: Callable[[Sequence], Tuple[List[float], List[Trade]]],
    data: Sequence,
    holdout_pct: float = 0.25,
) -> GateResult:
    """
    Out-of-sample validation gate.
    Splits data, fits on first (1-holdout_pct), tests on held-out holdout_pct.
    Returns pass/fail and metrics.
    """
    cost_config = {"min_spread_pct": 0.01, "max_spread_pct": 0.03}
        
    split_idx = int(len(data) * (1 - holdout_pct))
    train_data = data[:split_idx]
    oos_data = data[split_idx:]
    
    # Fit strategy on training window
    fitted_strategy = strategy_fn(train_data)
    
    # Evaluate on OOS window
    if callable(fitted_strategy):
        gross_oos, trades_oos = fitted_strategy(oos_data)
    else:
        gross_oos, trades_oos = strategy_fn(oos_data)
        
    net_oos = apply_costs_to_returns(gross_oos, trades_oos, cost_config)
    
    cum_net = 1.0
    for r in net_oos:
        cum_net *= (1 + r)
    cum_net -= 1.0
    
    passed = cum_net > 0.0
    
    return GateResult(
        passed=passed,
        metrics={
            "oos_cumulative_net_return": cum_net,
            "oos_trades_count": len(trades_oos),
            "holdout_pct": holdout_pct,
        },
        details=f"OOS cumulative net return: {cum_net:.4f}, trades: {len(trades_oos)}"
    )

def walk_forward(
    strategy_fn: Callable[[Sequence], Any],
    data: Sequence,
    fit_ratio: int = 5,
) -> GateResult:
    """
    Walk-forward validation gate.
    Rolling fit/test windows based on fit_ratio.
    Returns pass/fail and aggregate forward-window performance.
    """
    cost_config = {"min_spread_pct": 0.01, "max_spread_pct": 0.03}
        
    if fit_ratio < 1:
        raise ValueError("fit_ratio must be >= 1")
        
    window_size = fit_ratio + 1
    n = len(data)
    num_windows = max(0, n - window_size)
    
    all_net_returns = []
    total_trades = 0
    
    for i in range(num_windows):
        train_window = data[i : i + fit_ratio]
        test_window = data[i + fit_ratio : i + window_size]
        
        fitted_strategy = strategy_fn(train_window)
        if callable(fitted_strategy):
            gross, trades = fitted_strategy(test_window)
        else:
            gross, trades = strategy_fn(test_window)
            
        net = apply_costs_to_returns(gross, trades, cost_config)
        all_net_returns.extend(net)
        total_trades += len(trades)
        
    cum_net = 1.0
    for r in all_net_returns:
        cum_net *= (1 + r)
    cum_net -= 1.0
        
    passed = cum_net > 0.0
    
    return GateResult(
        passed=passed,
        metrics={
            "wf_cumulative_net_return": cum_net,
            "wf_forward_windows": num_windows,
            "wf_total_trades": total_trades,
            "fit_ratio": fit_ratio,
        },
        details=f"Walk-forward cumulative net return: {cum_net:.4f}"
    )
