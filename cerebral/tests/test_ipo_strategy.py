import pandas as pd
import pytest

from cerebral.trading.ipo_strategy import IPO_POP_FADE_STRATEGY_CODE


def _run_strategy(df: pd.DataFrame) -> list:
    """Execute the stored strategy string and return signals for the given DataFrame."""
    ns = {}
    exec(IPO_POP_FADE_STRATEGY_CODE, ns)
    return ns["strategy"](df)


def test_pop_then_fade_exit_on_post_20pct_peak():
    """(a) Pops to +30%, then pulls back >1% from the post-20% peak -> exit at 1% trail."""
    df = pd.DataFrame({
        "Open": [100.0] * 10,
        "High": [100.0, 130.0] + [130.0] * 8,
        "Low":  [100.0, 130.0, 128.0] + [125.0] * 7,
        "Close":[100.0, 130.0, 128.0] + [125.0] * 7,
    })
    sigs = _run_strategy(df)
    # Exits on bar 2 when Low 128.0 <= Peak 130.0 * 0.99 = 128.7
    assert sigs == [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]


def test_sub_20pct_peak_exit_on_3pct_pullback():
    """(b) Never moves >10% from entry, peaks at +5%, then pulls back 3% from that peak -> exit."""
    df = pd.DataFrame({
        "Open": [100.0] * 10,
        "High": [100.0, 105.0] + [105.0] * 8,
        "Low":  [100.0, 105.0, 101.8] + [101.0] * 7,
        "Close":[100.0, 105.0, 101.8] + [101.0] * 7,
    })
    sigs = _run_strategy(df)
    # Peak 105.0 never hits 120.0, so trail is 3%.
    # Stop = 105.0 * 0.97 = 101.85. Low 101.8 <= 101.85 triggers exit on bar 2.
    assert sigs == [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]


def test_flat_price_holds_throughout():
    """(c) Flat/never-moving price path -> never triggers stop, holds for whole series."""
    df = pd.DataFrame({
        "Open": [100.0] * 5,
        "High": [100.0] * 5,
        "Low":  [100.0] * 5,
        "Close":[100.0] * 5,
    })
    sigs = _run_strategy(df)
    assert sigs == [1, 1, 1, 1, 1]


def test_a_single_volatile_bar_cannot_trip_its_own_stop():
    """2026-09-03 regression: a bar's own High must not inflate the peak used to
    check that SAME bar's own Low -- confirmed live on real IPO data (BRVE's very
    first 5-minute bar alone: Open 30.20, High 31.56, Low 30.15, a >4.5% intrabar
    range), the original ordering exited on bar 0 before ever holding a position.
    A wide first bar must still result in a real entry; the stop only applies from
    the NEXT bar onward, using the peak as it stood before this bar's own high."""
    df = pd.DataFrame({
        "Open": [30.20] + [31.00] * 4,
        "High": [31.56] + [31.20] * 4,
        "Low":  [30.15] + [31.00] * 4,
        "Close":[31.12] + [31.10] * 4,
    })
    sigs = _run_strategy(df)
    # Bar 0's own 30.15 low must NOT be checked against a peak already
    # inflated to 31.56 by that same bar's own high -- it holds.
    assert sigs[0] == 1
    assert sigs == [1, 1, 1, 1, 1]
