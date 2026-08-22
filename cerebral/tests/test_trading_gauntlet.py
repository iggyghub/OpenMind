"""Unit tests for the validation gauntlet.

Covers: strategy passing all gates, strategy failing each individual gate,
and user-configurable thresholds.
"""

import numpy as np
import pandas as pd
import pytest

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
    fast = prices["Close"].rolling(params["fast_window"]).mean()
    slow = prices["Close"].rolling(params["slow_window"]).mean()
    signal = np.select([prices["Close"] > fast, prices["Close"] < slow], [1.0, -1.0], 0.0)
    daily_ret = signal * prices["Close"].pct_change()
    eq = 100 * np.cumprod(1 + daily_ret.fillna(0))
    metrics = {
        "sharpe": daily_ret.mean() / daily_ret.std() * np.sqrt(252),
        "total_return": (eq[-1] / eq[0]) - 1,
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
            k = list(params.keys())[0]
            if -5 < params[k] < 15:
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
