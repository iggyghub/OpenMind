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


# ── S3: full gauntlet (Monte Carlo, vs-random, vs-benchmark, noise,
# parameter sensitivity, capacity) + strategy card ─────────────────────────

import numpy as np
import pandas as pd

from cerebral.trading.gauntlet import run_gauntlet, StrategyCard


def make_prices(n=200, seed=42):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    return pd.DataFrame({
        "Open": close * (1 + rng.uniform(-0.005, 0.005, n)),
        "High": close * (1 + rng.uniform(0.005, 0.01, n)),
        "Low": close * (1 - rng.uniform(0.005, 0.01, n)),
        "Close": close,
        "Volume": np.random.randint(1000, 5000, n),
    })


def make_benchmark_prices(n=200, seed=43):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, n))
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": np.ones(n) * 2000,
    })


def make_positions(n=200, size=50.0):
    return pd.Series(np.full(n, size))


def make_params():
    return {"fast_window": 10, "slow_window": 30}


def make_backtest(prices, params):
    # int(): parameter sensitivity perturbs window sizes by a fraction, which
    # yields a float (e.g. 10 * 1.2 = 12.0) -- .rolling() requires an integer.
    fast = prices["Close"].rolling(int(params["fast_window"])).mean()
    slow = prices["Close"].rolling(int(params["slow_window"])).mean()
    signal = np.select([prices["Close"] > fast, prices["Close"] < slow], [1.0, -1.0], 0.0)
    daily_ret = signal * prices["Close"].pct_change()
    eq = 100 * np.cumprod(1 + daily_ret.fillna(0))
    metrics = {
        "sharpe": daily_ret.mean() / daily_ret.std() * np.sqrt(252),
        "total_return": (eq.iloc[-1] / eq.iloc[0]) - 1,
    }
    return list(eq), metrics


def make_failing_backtest(prices, params):
    eq = [100 * (1 - 0.02 * i) for i in range(len(prices))]
    metrics = {"sharpe": -0.5, "total_return": -0.5}
    return eq, metrics


class TestPassesAllGates:
    def test_all_gates_pass(self):
        prices = make_prices()
        params = make_params()
        card = run_gauntlet(
            make_backtest,
            prices,
            params,
            make_benchmark_prices(),
            make_positions(),
            hypothesis="Trend following test",
            provenance="internal",
            seed=42,
        )
        assert card.verdict == "VALIDATED"
        assert all(g.passed for g in card.gates)
        assert len(card.gates) == 6


class TestFailsEachGate:
    def test_fails_monte_carlo(self):
        def const_backtest(prices, params):
            return [100.0] * len(prices), {"sharpe": 0.0, "total_return": 0.0}

        card = run_gauntlet(
            const_backtest, make_prices(), make_params(), make_benchmark_prices(), make_positions(), seed=42
        )
        assert card.gates[0].passed is False
        assert card.verdict == "UNVALIDATED"

    def test_fails_vs_random(self):
        card = run_gauntlet(
            make_failing_backtest, make_prices(), make_params(), make_benchmark_prices(), make_positions(), seed=42
        )
        assert card.gates[1].passed is False
        assert card.verdict == "UNVALIDATED"

    def test_fails_vs_benchmark(self):
        card = run_gauntlet(
            make_failing_backtest, make_prices(), make_params(), make_benchmark_prices(), make_positions(), seed=42
        )
        assert card.gates[2].passed is False
        assert card.verdict == "UNVALIDATED"

    def test_fails_noise(self):
        call_count = [0]

        def noise_fail_backtest(prices, params):
            call_count[0] += 1
            if call_count[0] == 1:
                return [100 * (1 + 0.001 * i) for i in range(len(prices))], {"sharpe": 1.0, "total_return": 0.2}
            return [100 * (1 - 0.001 * i) for i in range(len(prices))], {"sharpe": 0.0, "total_return": -0.05}

        card = run_gauntlet(
            noise_fail_backtest, make_prices(), make_params(), make_benchmark_prices(), make_positions(), seed=42
        )
        assert card.gates[3].passed is False
        assert card.verdict == "UNVALIDATED"

    def test_fails_param_sensitivity(self):
        def flip_backtest(prices, params):
            # fast_window defaults to 10; a -20% perturbation (-> 8) crosses this
            # threshold and flips profitability, which is what the gate should catch.
            if params.get("fast_window", 10) < 9:
                return [100 * (1 - 0.001 * i) for i in range(len(prices))], {"sharpe": -0.1, "total_return": -0.05}
            return [100 * (1 + 0.001 * i) for i in range(len(prices))], {"sharpe": 0.8, "total_return": 0.2}

        card = run_gauntlet(
            flip_backtest, make_prices(), make_params(), make_benchmark_prices(), make_positions(), seed=42
        )
        assert card.gates[4].passed is False
        assert card.verdict == "UNVALIDATED"

    def test_fails_capacity(self):
        # ADV ~3000. Threshold 7.5% -> 225 shares. Use 300 shares.
        large_pos = make_positions(size=300.0)
        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(), large_pos, seed=42
        )
        assert card.gates[5].passed is False
        assert card.verdict == "UNVALIDATED"


class TestConfigurableThresholds:
    def test_all_thresholds_configurable(self):
        # With loose threshold, constant returns should pass MC
        card = run_gauntlet(
            lambda p, pr: ([100.0] * len(p), {"sharpe": 0, "total_return": 0}),
            make_prices(),
            make_params(),
            make_benchmark_prices(),
            make_positions(),
            p_value_threshold=1.0,
            adv_threshold_pct=0.2,
            seed=42,
        )
        assert card.gates[0].passed is True
        assert card.verdict == "VALIDATED"


class TestAutoPromote:
    """S5c: a VALIDATED verdict schedules paper trading via scheduler's own
    public API, not by writing to scheduler._con directly (seam rule), and
    never fabricates a fill to seed the forward record (would silently
    count toward the 30-trade minimum without being a real trade)."""

    def test_validated_calls_scheduler_public_api(self):
        calls = []

        class FakeScheduler:
            def _run_paper_strategy(self, args):
                calls.append(args)

        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), hypothesis="MA cross test", provenance="internal",
            scheduler=FakeScheduler(), seed=42,
        )
        assert card.verdict == "VALIDATED"
        assert len(calls) == 1
        assert calls[0]["strategy_name"] == "MA cross test"
        assert calls[0]["interval"] == "5m"

    def test_unvalidated_does_not_schedule(self):
        calls = []

        class FakeScheduler:
            def _run_paper_strategy(self, args):
                calls.append(args)

        card = run_gauntlet(
            lambda p, pr: ([100.0] * len(p), {"sharpe": 0.0, "total_return": 0.0}),
            make_prices(), make_params(), make_benchmark_prices(), make_positions(),
            scheduler=FakeScheduler(), seed=42,
        )
        assert card.verdict == "UNVALIDATED"
        assert calls == []

    def test_no_scheduler_does_not_raise(self):
        # scheduler=None (the default) must be a safe no-op, not an
        # AttributeError on a None scheduler.
        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), seed=42,
        )
        assert card.verdict == "VALIDATED"
