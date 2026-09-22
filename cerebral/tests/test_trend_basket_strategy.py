import pandas as pd

from cerebral.trading.trend_basket_strategy import TREND_BASKET_STRATEGY_CODE


def _run_strategy(df: pd.DataFrame) -> list:
    """Execute the stored strategy string and return signals for the given DataFrame."""
    ns = {}
    exec(TREND_BASKET_STRATEGY_CODE, ns)
    return ns["strategy"](df)


def test_stop_on_12pct_pullback():
    """A >12% pullback from peak (Low breaches peak * 0.88) exits and stays flat."""
    df = pd.DataFrame({
        "Open": [100.0] * 5,
        "High": [100.0] * 5,
        "Low":  [100.0, 100.0, 87.0, 100.0, 100.0],
        "Close": [100.0] * 5,
    })
    sigs = _run_strategy(df)
    # Stop = 100.0 * 0.88 = 88.0. Low 87.0 <= 88.0 triggers exit on bar 2.
    assert sigs == [1, 1, 0, 0, 0]


def test_hold_under_12pct_pullback():
    """A pullback that stays within 12% of peak never triggers the stop."""
    df = pd.DataFrame({
        "Open": [100.0] * 3,
        "High": [100.0] * 3,
        "Low":  [100.0, 100.0, 92.0],
        "Close": [100.0] * 3,
    })
    sigs = _run_strategy(df)
    # Stop = 100.0 * 0.88 = 88.0. Low 92.0 > 88.0 never triggers -- holds throughout.
    assert sigs == [1, 1, 1]


def test_hard_exit_after_bar_20():
    """Flat price that never triggers the stop still force-exits after bar index 20."""
    df = pd.DataFrame({
        "Open": [100.0] * 23,
        "High": [100.0] * 23,
        "Low": [100.0] * 23,
        "Close": [100.0] * 23,
    })
    sigs = _run_strategy(df)
    assert sigs[:21] == [1] * 21
    assert sigs[21:] == [0, 0]


def test_a_single_volatile_bar_cannot_trip_its_own_stop():
    """A bar's own High must not inflate the peak used to check that SAME bar's own
    Low -- same regression class ipo_strategy.py's 2026-09-03 fix covers. A wide first
    bar (Open 100, High 150) must still result in a real entry: bar 0's own Low (90) is
    checked against the peak as it stood BEFORE this bar's own high (100, not 150) --
    90 > 100*0.88=88, so it holds. A buggy peak-before-check ordering would compute
    stop = 150*0.88 = 132 and incorrectly trip on Low 90."""
    df = pd.DataFrame({
        "Open": [100.0, 150.0, 150.0],
        "High": [150.0, 150.0, 150.0],
        "Low": [90.0, 150.0, 150.0],
        "Close": [100.0, 150.0, 150.0],
    })
    sigs = _run_strategy(df)
    assert sigs == [1, 1, 1]
