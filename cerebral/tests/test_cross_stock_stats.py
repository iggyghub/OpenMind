"""Benchmark-relative cross-stock statistics (#1250 / #1251 axis 3)."""
import pytest

from cerebral.trading.cross_stock_stats import (
    CAVEAT, bh_adjust, binom_upper_tail, summarize_vs_benchmark,
)


def test_binom_upper_tail_known_values():
    assert binom_upper_tail(0, 10) == 1.0
    assert binom_upper_tail(11, 10) == 0.0
    assert binom_upper_tail(10, 10) == pytest.approx(0.5 ** 10)
    assert binom_upper_tail(5, 10) == pytest.approx(0.623046875)  # 638/1024


def test_bh_adjust_is_monotone_capped_and_order_preserving():
    p = [0.01, 0.04, 0.03, 0.5]
    q = bh_adjust(p)
    assert q == pytest.approx([0.04, 0.0533333, 0.0533333, 0.5], rel=1e-4)
    assert all(0 <= x <= 1 for x in q)
    assert bh_adjust([]) == []


def _pairs(wins, n, excess=0.1):
    return [(1.0 + excess, 1.0)] * wins + [(1.0 - excess, 1.0)] * (n - wins)


def test_a_clone_of_buy_and_hold_is_not_significant_against_a_coin_flip():
    rows = summarize_vs_benchmark({"clone": _pairs(50, 100, 0.001)})
    assert rows[0]["beat_share"] == 0.5
    assert rows[0]["significant"] is False


def test_a_real_edge_is_significant_and_a_loser_is_not():
    rows = summarize_vs_benchmark({"edge": _pairs(90, 100), "loser": _pairs(20, 100)})
    by = {r["strategy_id"]: r for r in rows}
    assert by["edge"]["significant"] is True and by["edge"]["q_value"] < 0.001
    assert by["loser"]["significant"] is False
    assert rows[0]["strategy_id"] == "edge"  # ranked by beat share


def test_fewer_than_min_stocks_are_not_ranked():
    assert summarize_vs_benchmark({"few": _pairs(19, 19)}) == []


def test_ties_in_beat_share_break_on_median_excess():
    rows = summarize_vs_benchmark({"small": _pairs(60, 100, 0.01), "big": _pairs(60, 100, 0.5)})
    assert [r["strategy_id"] for r in rows] == ["big", "small"]


def test_multiple_comparisons_are_deflated_across_all_ranked_strategies():
    # one mildly lucky strategy among many noise strategies must not stay 'significant'
    field = {f"noise{i}": _pairs(50, 100) for i in range(60)}
    field["lucky"] = _pairs(62, 100)  # naive p ~ 0.01
    lucky = next(r for r in summarize_vs_benchmark(field) if r["strategy_id"] == "lucky")
    assert lucky["p_value"] < 0.05 and lucky["significant"] is False


def test_caveat_is_a_short_nonempty_string():
    assert isinstance(CAVEAT, str) and "Informational" in CAVEAT
