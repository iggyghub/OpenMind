"""Regime stress windows + the risk view."""
import datetime

import numpy as np
import pytest

from cerebral.trading.stress_windows import (
    DEFENSIVE_DD_GAIN, RESEARCH_COST, evaluate_window, summarize_stress, windows,
)


def _rets(n=300, seed=0, drift=0.0004):
    return np.random.default_rng(seed).normal(drift, 0.01, n)


def test_always_long_matches_buy_and_hold_apart_from_one_entry_cost():
    r = _rets()
    res = evaluate_window(np.ones(len(r)), r, cost=0.001)
    assert res["gross_return"] == pytest.approx(res["benchmark_return"])
    assert res["n_trades"] == 1
    assert res["net_return"] < res["gross_return"]
    assert res["max_drawdown"] == pytest.approx(res["benchmark_max_drawdown"], abs=1e-3)


def test_cost_is_a_fraction_of_traded_notional_independent_of_anything_else():
    r = _rets(seed=1)
    pos = np.tile([1.0, 0.0], len(r) // 2)          # trades every bar
    free = evaluate_window(pos, r, cost=0.0)
    dear = evaluate_window(pos, r, cost=0.005)
    assert free["net_return"] == pytest.approx(free["gross_return"])
    assert dear["net_return"] < free["net_return"]
    assert dear["n_trades"] == len(r)


def test_sitting_out_a_crash_cuts_drawdown_and_reports_a_positive_gain():
    r = np.concatenate([np.full(100, 0.002), np.full(50, -0.02), np.full(100, 0.002)])
    pos = np.concatenate([np.ones(100), np.zeros(50), np.ones(100)])
    res = evaluate_window(pos, r, cost=0.0)
    assert res["max_drawdown"] > res["benchmark_max_drawdown"]      # less negative
    assert res["net_return"] > res["benchmark_return"]


def test_too_little_data_or_a_length_mismatch_is_none():
    assert evaluate_window(np.ones(10), _rets(10)) is None
    assert evaluate_window(np.ones(40), _rets(41)) is None


def test_windows_include_the_2008_crash_and_a_rolling_main_window():
    w = windows(datetime.date(2026, 9, 19))
    assert set(w) == {"gfc", "mid", "bear22", "main"}
    assert w["gfc"][0] <= "2008-09-15" < w["gfc"][1]
    assert w["main"][1] == "2026-09-19" and w["main"][0] < "2021-09-30"


def _row(excess, dd_gain):
    return {"net_return": 0.5 + excess, "benchmark_return": 0.5, "max_drawdown": -0.2 + dd_gain,
            "benchmark_max_drawdown": -0.2}


def _stocks(excess, dd_gain, n=12):
    return [_row(excess, dd_gain) for _ in range(n)]


def test_robust_needs_to_beat_buy_and_hold_in_every_window():
    data = {
        "always": {"gfc": _stocks(0.2, 0.0), "main": _stocks(0.2, 0.0)},
        "bull_only": {"gfc": _stocks(-0.3, 0.0), "main": _stocks(0.3, 0.0)},
    }
    out = summarize_stress(data)
    flags = {r["strategy_id"]: r["robust"] for r in out["strategies"]}
    assert flags == {"always": True, "bull_only": False}
    assert out["robust"] == 1 and out["strategies"][0]["strategy_id"] == "always"


def test_a_defensive_strategy_is_flagged_even_though_it_never_beats_buy_and_hold():
    data = {"shield": {"gfc": _stocks(-0.05, 0.25), "bear22": _stocks(-0.02, 0.2)}}
    row = summarize_stress(data)["strategies"][0]
    assert row["defensive"] is True and row["robust"] is False


def test_giving_up_too_much_return_is_not_defensive():
    data = {"costly": {"gfc": _stocks(-0.4, 0.3), "bear22": _stocks(-0.4, 0.3)}}
    assert summarize_stress(data)["strategies"][0]["defensive"] is False
    assert DEFENSIVE_DD_GAIN == 0.10


def test_windows_with_too_few_stocks_are_not_scored_and_block_the_flags():
    data = {"thin": {"gfc": _stocks(0.5, 0.3, n=12), "main": _stocks(0.5, 0.3, n=4)}}
    out = summarize_stress(data)
    assert out["strategies"][0]["robust"] is False
    assert "main" not in out["strategies"][0]["windows"]


def test_per_window_multiple_comparison_adjustment():
    field = {f"noise{i}": {"gfc": [_row(0.1 if j % 2 else -0.1, 0.0) for j in range(20)]} for i in range(40)}
    out = summarize_stress(field)
    assert out["windows"]["gfc"] == {"ranked": 40, "significant": 0}


def test_research_cost_is_two_basis_points():
    assert RESEARCH_COST == pytest.approx(0.0002)
