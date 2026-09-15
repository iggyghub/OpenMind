"""Unit tests for cerebral/trading/cross_stock_replay.py (CROSS-STOCK-VALIDATION S2).

Replaces self_dev's own first attempt at this file (PR #1240): that attempt
never wrote an implementation module at all (only this test file), and its
mocks assumed a `bar_cache.BarCache` class that doesn't exist anywhere in
this codebase -- bar_cache.py is a plain module-level get_bars function
(see cerebral/trading/bar_cache.py), same as run_replay already uses.
"""
import asyncio
import logging

import pandas as pd
import pytest

import plugins.trading_replay as tr
from cerebral.trading.cross_stock_replay import build_pairs, rollup_consistency, run_pair
from cerebral.trading.cross_stock_store import CrossStockStore
from cerebral.trading.strategy_store import StrategySpec, StrategyStore

_ALWAYS_LONG = "def strategy(data):\n    return [1] * len(data)\n"


class FakeBarCache:
    """Same shape as test_replay.py's own FakeBarCache -- get_bars(symbol,
    start, end, interval), ignores the args, returns synthetic bars wide
    enough to cover warm-up plus the requested window."""
    def get_bars(self, symbol, start, end, interval):
        idx = pd.date_range(start="2021-01-01", periods=1400, freq="B")
        close = [100.0 + i * 0.05 for i in range(1400)]
        return pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * 1400},
            index=idx,
        )


class FlatBarCache:
    """Constant close price -- buy-and-hold return should be ~0."""
    def get_bars(self, symbol, start, end, interval):
        idx = pd.date_range(start="2021-01-01", periods=1400, freq="B")
        close = [100.0] * 1400
        return pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": [1e6] * 1400},
            index=idx,
        )


class FailingBarCache:
    def get_bars(self, symbol, start, end, interval):
        raise ValueError("Missing columns in Alpaca response for " + symbol)


def test_run_pair_against_a_stock_other_than_the_strategys_own_symbol():
    """The whole point of this campaign: symbol is independent of the
    strategy's own registered spec.symbol."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="NVDA",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FakeBarCache(),
    )
    assert result["flat_reason"] is None
    assert result["net_return"] is not None
    assert result["n_trades"] >= 0


def test_run_pair_reports_flat_reason_on_bar_fetch_failure():
    """A missing-bars failure (the exact class of error BATCH-REPLAY
    already tolerates for UBER/LYFT/etc.) must not raise -- it's recorded
    as a real flat_reason, with no fabricated numeric result."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="XYZ",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FailingBarCache(),
    )
    assert result["net_return"] is None
    assert result["max_drawdown"] is None
    assert result["flat_reason"] is not None


def test_run_pair_reports_flat_reason_on_broken_strategy_code():
    result = run_pair(
        strategy_id="s1", code="def strategy(data):\n    raise RuntimeError('kaboom')\n",
        symbol="AAPL", start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FakeBarCache(),
    )
    assert result["flat_reason"] is not None
    assert "kaboom" in result["flat_reason"]


def test_build_pairs_excludes_stock_specific_and_unclassified_strategies():
    """Only cross_test_eligible=True specs enter the sweep -- False (stock-
    specific) and None (never classified) are both excluded, same
    conservative-refuse convention as check_graduation's own gates."""
    generic = StrategySpec("generic", "AAPL", _ALWAYS_LONG, cross_test_eligible=True)
    specific = StrategySpec("specific", "MSFT", _ALWAYS_LONG, cross_test_eligible=False)
    unclassified = StrategySpec("unclassified", "NVDA", _ALWAYS_LONG)  # cross_test_eligible=None

    pairs = build_pairs([generic, specific, unclassified], basket=["AAPL", "MSFT"])

    assert pairs == [(generic, "AAPL"), (generic, "MSFT")]


def test_build_pairs_is_cross_product_of_eligible_specs_and_basket():
    a = StrategySpec("a", "AAPL", _ALWAYS_LONG, cross_test_eligible=True)
    b = StrategySpec("b", "MSFT", _ALWAYS_LONG, cross_test_eligible=True)

    pairs = build_pairs([a, b], basket=["X", "Y", "Z"])

    assert len(pairs) == 6
    assert (a, "X") in pairs and (b, "Z") in pairs


def test_consistency_rollup_8_of_10_positive(tmp_path):
    """8/10 tested stocks showing positive expectancy rolls up to 0.8."""
    s_store = StrategyStore(db_path=tmp_path / "strategy_specs.db")
    c_store = CrossStockStore(db_path=str(tmp_path / "cross_stock_results.db"))

    s_store.save(StrategySpec("strat1", "AAPL", _ALWAYS_LONG, cross_test_eligible=True))

    # Record 10 results: 8 positive, 2 negative
    for i in range(10):
        net_ret = 0.05 if i < 8 else -0.05
        c_store.record_result("run1", "strat1", f"SYM{i}", net_ret, 0.0, 10, None)

    rollup_consistency(s_store, c_store)

    spec = s_store.get("strat1")
    assert spec.cross_stock_consistency == pytest.approx(0.8)


def test_consistency_rollup_zero_stocks(tmp_path):
    """Strategy with zero tested stocks stays None (missing data != confirmed inconsistent)."""
    s_store = StrategyStore(db_path=tmp_path / "strategy_specs.db")
    c_store = CrossStockStore(db_path=str(tmp_path / "cross_stock_results.db"))

    s_store.save(StrategySpec("strat2", "MSFT", _ALWAYS_LONG, cross_test_eligible=True))

    # Record 0 results for strat2

    rollup_consistency(s_store, c_store)

    spec = s_store.get("strat2")
    assert spec.cross_stock_consistency is None


def test_consistency_rollup_excludes_failed_pairs_from_both_numerator_and_denominator(tmp_path):
    """A pair that couldn't run (flat_reason set, net_return None -- missing
    bars, sandbox error) must not count as a negative result. Caught before
    merging: the original SQL's CASE WHEN net_return > 0 treats NULL as
    "not positive" and rolls it into the AVG anyway, silently conflating
    "couldn't test this stock" with "tested it and lost.\""""
    s_store = StrategyStore(db_path=tmp_path / "strategy_specs.db")
    c_store = CrossStockStore(db_path=str(tmp_path / "cross_stock_results.db"))

    s_store.save(StrategySpec("strat3", "AAPL", _ALWAYS_LONG, cross_test_eligible=True))

    # 2 real results (both positive) + 3 failed pairs (no bars available).
    c_store.record_result("run1", "strat3", "AAPL", 0.10, -0.05, 10, None)
    c_store.record_result("run1", "strat3", "MSFT", 0.05, -0.02, 8, None)
    for symbol in ("UBER", "LYFT", "SNAP"):
        c_store.record_result("run1", "strat3", symbol, None, None, 0, "Missing columns in Alpaca response")

    rollup_consistency(s_store, c_store)

    spec = s_store.get("strat3")
    # 2/2 real results positive -- not 2/5, which is what the failed
    # pairs being counted as negative would have produced.
    assert spec.cross_stock_consistency == pytest.approx(1.0)


def test_consistency_rollup_all_failed_pairs_stays_none(tmp_path):
    """Every attempt failing is still "missing data," not "confirmed
    inconsistent" -- must not roll up to 0.0."""
    s_store = StrategyStore(db_path=tmp_path / "strategy_specs.db")
    c_store = CrossStockStore(db_path=str(tmp_path / "cross_stock_results.db"))

    s_store.save(StrategySpec("strat4", "AAPL", _ALWAYS_LONG, cross_test_eligible=True))
    c_store.record_result("run1", "strat4", "AAPL", None, None, 0, "Missing columns in Alpaca response")

    rollup_consistency(s_store, c_store)

    spec = s_store.get("strat4")
    assert spec.cross_stock_consistency is None


# F2 (#1247): stop-path bounds tests -- asyncio_mode=auto handles these as async tests automatically.

async def test_stop_cross_stock_replay_cancels_wedged_task(monkeypatch):
    """A task that never completes must be cancelled within the timeout, not awaited forever."""
    wedged = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(tr, "_cross_stock_task", wedged)
    monkeypatch.setattr(tr, "_cross_stock_stop_flag", False)
    monkeypatch.setattr(tr, "_CROSS_STOCK_STOP_TIMEOUT_S", 0.05)

    result = await tr.stop_cross_stock_replay()

    assert result == "Cross-stock replay stopped."
    assert wedged.cancelled()


async def test_stop_cross_stock_replay_normal_path_no_warning(monkeypatch, caplog):
    """A task that respects the stop flag and exits normally triggers no warning."""
    async def _respects_stop():
        while not tr._cross_stock_stop_flag:
            await asyncio.sleep(0)

    task = asyncio.create_task(_respects_stop())
    monkeypatch.setattr(tr, "_cross_stock_task", task)
    monkeypatch.setattr(tr, "_cross_stock_stop_flag", False)

    with caplog.at_level(logging.WARNING, logger="plugins.trading_replay"):
        result = await tr.stop_cross_stock_replay()

    assert result == "Cross-stock replay stopped."
    assert not any("cancelled" in r.message for r in caplog.records)


# F3 (#1248): benchmark_return tests

def test_benchmark_return_is_populated_and_positive_for_rising_series():
    """Rising close series -> positive buy-and-hold benchmark_return."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="AAPL",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FakeBarCache(),
    )
    assert result["flat_reason"] is None
    assert result["benchmark_return"] is not None
    assert result["benchmark_return"] > 0


def test_benchmark_return_is_near_zero_for_flat_series():
    """Constant close -> buy-and-hold return is 0 (all pct_changes are 0)."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="AAPL",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FlatBarCache(),
    )
    assert result["flat_reason"] is None
    assert result["benchmark_return"] == pytest.approx(0.0, abs=1e-9)


def test_benchmark_return_is_null_when_bars_missing():
    """Missing bars -> benchmark_return is None, not 0.0."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="XYZ",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FailingBarCache(),
    )
    assert result["benchmark_return"] is None


def test_benchmark_return_is_null_when_strategy_run_failed():
    """Broken strategy code -> benchmark_return is None, not 0.0."""
    result = run_pair(
        strategy_id="s1", code="def strategy(data):\n    raise RuntimeError('kaboom')\n",
        symbol="AAPL", start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FakeBarCache(),
    )
    assert result["benchmark_return"] is None


def test_always_long_net_return_matches_benchmark_return():
    """Always-long strategy tracks buy-and-hold closely (same in-window slice).
    Not exactly equal: run_bars_verbose applies simulated transaction costs,
    so net_return is slightly below the raw close-pct-change compound."""
    result = run_pair(
        strategy_id="s1", code=_ALWAYS_LONG, symbol="AAPL",
        start="2021-09-15", end="2026-09-15", interval="1d",
        bar_cache=FakeBarCache(),
    )
    assert result["flat_reason"] is None
    assert result["net_return"] == pytest.approx(result["benchmark_return"], rel=0.05)


def test_cross_stock_consistency_unchanged_by_f3(tmp_path):
    """Regression guard: benchmark_return being recorded must not alter the
    cross_stock_consistency rollup -- it stays a sign test on net_return only."""
    s_store = StrategyStore(db_path=tmp_path / "strategy_specs.db")
    c_store = CrossStockStore(db_path=str(tmp_path / "cross_stock_results.db"))

    s_store.save(StrategySpec("guard", "AAPL", _ALWAYS_LONG, cross_test_eligible=True))
    # 3 positive net_returns, 1 negative -- consistency should be 0.75 regardless of benchmark.
    c_store.record_result("run1", "guard", "SYM0", 0.10, -0.05, 5, None, benchmark_return=0.08)
    c_store.record_result("run1", "guard", "SYM1", 0.05, -0.02, 4, None, benchmark_return=0.06)
    c_store.record_result("run1", "guard", "SYM2", 0.03, -0.01, 3, None, benchmark_return=0.09)
    c_store.record_result("run1", "guard", "SYM3", -0.04, -0.10, 6, None, benchmark_return=0.07)

    rollup_consistency(s_store, c_store)

    spec = s_store.get("guard")
    assert spec.cross_stock_consistency == pytest.approx(0.75)
