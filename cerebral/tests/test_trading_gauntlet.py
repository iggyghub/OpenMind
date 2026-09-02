import json
import pytest
from typing import List, Tuple, Sequence, Any
from cerebral.trading.cost_model import Trade
from cerebral.trading.gauntlet import oos_test, walk_forward, GateResult, _bars_per_year

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

from cerebral.trading.gauntlet import run_gauntlet, StrategyCard, compute_max_holding_days


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
            def _create_event(self, args):
                calls.append(args)

        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), hypothesis="MA cross test", provenance="internal",
            scheduler=FakeScheduler(), paper_broker=object(), seed=42,
        )
        assert card.verdict == "VALIDATED"
        assert len(calls) == 1
        assert calls[0]["title"] == "MA cross test"
        assert calls[0]["recurrence"] == "5m"
        assert calls[0]["start_iso"]

    def test_unvalidated_does_not_schedule(self):
        calls = []

        class FakeScheduler:
            def _create_event(self, args):
                calls.append(args)

        card = run_gauntlet(
            lambda p, pr: ([100.0] * len(p), {"sharpe": 0.0, "total_return": 0.0}),
            make_prices(), make_params(), make_benchmark_prices(), make_positions(),
            scheduler=FakeScheduler(), paper_broker=object(), seed=42,
        )
        assert card.verdict == "UNVALIDATED"
        assert calls == []

    def test_no_paper_broker_does_not_schedule(self):
        # scheduler present but paper_broker=None (the default) must also be
        # a safe no-op -- paper_broker gates whether auto-promotion is
        # wanted at all, even though the scheduler event itself no longer
        # carries the broker (the dispatcher supplies its own).
        calls = []

        class FakeScheduler:
            def _create_event(self, args):
                calls.append(args)

        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), scheduler=FakeScheduler(), seed=42,
        )
        assert card.verdict == "VALIDATED"
        assert calls == []

    def test_validated_registers_the_spec_the_dispatcher_needs(self, tmp_path):
        """The event carries only a title. Without a matching spec (symbol +
        strategy source) the dispatcher has nothing to evaluate and no ticker
        to trade -- it used to fall back to a literal "SYMBOL" placeholder."""
        from cerebral.trading.strategy_store import StrategyStore

        calls = []

        class FakeScheduler:
            def _create_event(self, args):
                calls.append(args)

        store = StrategyStore(db_path=tmp_path / "specs.db")
        code = "def strategy(data):\n    return [1] * len(data)"
        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), hypothesis="MA cross test", provenance="internal",
            scheduler=FakeScheduler(), paper_broker=object(), seed=42,
            symbol="AAPL", strategy_code=code, strategy_store=store, position_qty=3.0,
        )

        assert card.verdict == "VALIDATED"
        spec = store.get("MA cross test")
        assert spec is not None
        assert (spec.symbol, spec.code, spec.qty) == ("AAPL", code, 3.0)
        assert calls[0]["title"] == "MA cross test"  # still scheduled

        # S16/#861: a VALIDATED pass must also record real lineage, not
        # just the dispatch pointer -- provenance and hypothesis intact,
        # not silently dropped or flattened.
        version = store.get_current_version("MA cross test")
        assert version is not None
        assert version["origin"] == "generated"
        assert version["hypothesis"] == "MA cross test"
        assert json.loads(version["provenance_json"]) == {"source": "internal"}
        assert "internal" in store.render_provenance(version)

    def test_unvalidated_registers_no_spec(self, tmp_path):
        from cerebral.trading.strategy_store import StrategyStore

        class FakeScheduler:
            def _create_event(self, args):
                pass

        store = StrategyStore(db_path=tmp_path / "specs.db")
        run_gauntlet(
            lambda p, pr: ([100.0] * len(p), {"sharpe": 0.0, "total_return": 0.0}),
            make_prices(), make_params(), make_benchmark_prices(), make_positions(),
            hypothesis="dud", scheduler=FakeScheduler(), paper_broker=object(), seed=42,
            symbol="AAPL", strategy_code="def strategy(data):\n    return [1]",
            strategy_store=store,
        )
        assert store.list_all() == []

    def test_no_scheduler_does_not_raise(self):
        # scheduler=None (the default) must be a safe no-op, not an
        # AttributeError on a None scheduler.
        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), seed=42,
        )
        assert card.verdict == "VALIDATED"


class TestBarsPerYear:
    """S22 (#875): each interval must get its own annualisation factor --
    bucketing several intervals onto one shared formula silently applies the
    wrong factor to every interval but the one it was written for."""

    def test_daily(self):
        assert _bars_per_year("1d") == 252.0

    def test_1h_and_4h_are_distinct(self):
        h1, h4 = _bars_per_year("1h"), _bars_per_year("4h")
        assert h1 == pytest.approx(252 * 6.5)
        assert h4 == pytest.approx(252 * 6.5 / 4)
        assert h1 != h4

    def test_5m_15m_30m_are_all_distinct(self):
        m5, m15, m30 = _bars_per_year("5m"), _bars_per_year("15m"), _bars_per_year("30m")
        assert m5 == pytest.approx(252 * 6.5 * 60 / 5)
        assert m15 == pytest.approx(252 * 6.5 * 60 / 15)
        assert m30 == pytest.approx(252 * 6.5 * 60 / 30)
        assert len({m5, m15, m30}) == 3

    def test_unknown_interval_falls_back_to_daily(self):
        assert _bars_per_year("bogus") == 252.0


class TestMaxHoldingDays:
    """"Most trades daily, nothing held past a month" -- a user policy
    decision, not a data-derived threshold. See TRADING.md's #961
    follow-up entry."""

    def test_flat_series_holds_zero_days(self):
        assert compute_max_holding_days([0, 0, 0, 0], "1d") == 0.0

    def test_one_continuous_run_on_daily_bars(self):
        # 10 consecutive held bars on daily data == 10 days (1 bar/day).
        position = [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0]
        assert compute_max_holding_days(position, "1d") == pytest.approx(10.0)

    def test_direction_flip_without_flat_is_one_continuous_hold(self):
        # Long then short with no flat bar between them is still one
        # unbroken holding run at the broker -- the position is never
        # actually closed in between.
        position = [1, 1, 1, -1, -1, -1]
        assert compute_max_holding_days(position, "1d") == pytest.approx(6.0)

    def test_takes_the_longest_run_not_the_total(self):
        position = [1, 1, 0, 1, 1, 1, 1, 1, 0, 1]
        assert compute_max_holding_days(position, "1d") == pytest.approx(5.0)

    def test_intraday_bars_compress_to_fewer_days(self):
        # 78 consecutive 5-minute bars is one full 6.5h trading day, not
        # 78 days -- must scale by the interval's own bars-per-day.
        position = [1] * 78
        assert compute_max_holding_days(position, "5m") == pytest.approx(1.0, rel=0.05)


class TestMaxHoldingPeriodGate:
    def test_skipped_when_backtest_func_reports_no_holding_metric(self):
        # make_backtest (this file's default fixture) never sets
        # metrics["max_holding_days"] -- the gate must not appear at all,
        # not silently pass. Matches TestPassesAllGates' existing
        # len(card.gates) == 6 assertion.
        card = run_gauntlet(
            make_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), seed=42,
        )
        assert "max_holding_period" not in [g.name for g in card.gates]

    def test_fails_when_longest_trade_exceeds_the_limit(self):
        def long_hold_backtest(prices, params):
            eq, metrics = make_backtest(prices, params)
            metrics["max_holding_days"] = 45.0
            return eq, metrics

        card = run_gauntlet(
            long_hold_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), seed=42, max_holding_days=30.0,
        )
        gate = next(g for g in card.gates if g.name == "max_holding_period")
        assert gate.passed is False
        assert card.verdict == "UNVALIDATED"

    def test_passes_when_longest_trade_is_within_the_limit(self):
        def short_hold_backtest(prices, params):
            eq, metrics = make_backtest(prices, params)
            metrics["max_holding_days"] = 5.0
            return eq, metrics

        card = run_gauntlet(
            short_hold_backtest, make_prices(), make_params(), make_benchmark_prices(),
            make_positions(), seed=42, max_holding_days=30.0,
        )
        gate = next(g for g in card.gates if g.name == "max_holding_period")
        assert gate.passed is True


def test_random_entry_returns_nonzero_on_uptrend():
    """vs_random gate must compute non-zero random-entry returns instead of
    degenerating to 0.0 (off-by-one bug where start/end were the same bar)."""
    n = 200
    close = np.arange(1, n + 1, dtype=float)
    prices = pd.DataFrame({
        "Open": close, "High": close + 0.1, "Low": close - 0.1,
        "Close": close, "Volume": np.ones(n) * 1000,
    })

    card = run_gauntlet(
        lambda p, pr: ([100.0] * len(p), {"sharpe": 0.0, "total_return": 0.0}),
        prices,
        make_params(),
        benchmark_prices=None,
        seed=42,
    )
    vs_rand_gate = card.gates[1]
    assert vs_rand_gate.threshold > 0.0, "p95_random should be > 0.0 on an uptrend"
