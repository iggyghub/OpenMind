"""Random-timing null (#1251 axis 2)."""
import numpy as np
import pytest

from cerebral.trading.permutation_null import MIN_BARS, NullResult, circular_shift_null


def _series(n=1000, seed=1):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.01, n)


def _hold_blocks(n, block, gap):
    pos = np.zeros(n)
    i = 0
    while i < n:
        pos[i:i + block] = 1.0
        i += block + gap
    return pos


def test_a_prescient_strategy_has_a_tiny_p_value():
    ret = _series()
    pos = (ret > 0).astype(float)  # holds only on up bars: a perfect (leaky) timer
    res = circular_shift_null(pos, ret, n_sims=300)
    assert res.p_value < 0.02 and res.observed > res.null_median


def test_random_timing_is_not_significant():
    ret = _series(seed=2)
    pos = _hold_blocks(len(ret), block=20, gap=30)  # arbitrary timing, unrelated to returns
    res = circular_shift_null(pos, ret, n_sims=300)
    assert res.p_value > 0.05


def test_always_long_is_the_null_itself():
    ret = _series(seed=3)
    res = circular_shift_null(np.ones(len(ret)), ret, cost=0.0, n_sims=200)
    # every circular shift of a constant position is identical, so it can never beat the null
    assert res.observed == pytest.approx(res.null_median)
    assert res.p_value == 1.0


def test_p_value_is_bounded_and_uses_the_plus_one_correction():
    ret = _series(seed=4)
    pos = (ret > 0).astype(float)
    res = circular_shift_null(pos, ret, n_sims=99)
    assert res.p_value >= 1 / (res.n_sims + 1)
    assert 0 < res.p_value <= 1


def test_cost_lowers_the_observed_return():
    ret = _series(seed=5)
    pos = _hold_blocks(len(ret), block=3, gap=3)
    cheap = circular_shift_null(pos, ret, cost=0.0, n_sims=50)
    dear = circular_shift_null(pos, ret, cost=0.01, n_sims=50)
    assert dear.observed < cheap.observed


def test_deterministic_for_a_seed():
    ret = _series(seed=6)
    pos = _hold_blocks(len(ret), 10, 10)
    assert circular_shift_null(pos, ret, seed=7, n_sims=50) == circular_shift_null(pos, ret, seed=7, n_sims=50)


def test_nothing_to_test_returns_none():
    ret = _series()
    assert circular_shift_null(np.zeros(len(ret)), ret) is None            # never holds
    assert circular_shift_null(np.ones(MIN_BARS - 1), _series(MIN_BARS - 1)) is None  # too short
    assert circular_shift_null(np.ones(10), _series(11)) is None             # length mismatch


def test_returns_a_frozen_result():
    res = circular_shift_null(_hold_blocks(500, 10, 10), _series(500), n_sims=20)
    assert isinstance(res, NullResult)
    with pytest.raises(Exception):
        res.p_value = 0.0
