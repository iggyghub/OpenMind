import pandas as pd

from cerebral.trading.trend_basket_strategy import entry_date_of, trend_basket_code


def _run_strategy(df: pd.DataFrame, entry: str = "2026-01-01") -> list:
    """Execute the stored strategy string and return signals for the given DataFrame."""
    ns = {}
    exec(trend_basket_code(entry), ns)
    return ns["strategy"](df)


def _bars(low, open_=None, high=None, start="2026-01-01"):
    n = len(low)
    open_ = open_ or [100.0] * n
    high = high or [100.0] * n
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": [100.0] * n},
                        index=pd.date_range(start, periods=n, freq="D"))


def test_stop_on_12pct_pullback():
    """A >12% pullback from peak (Low breaches peak * 0.88) exits and stays flat."""
    sigs = _run_strategy(_bars([100.0, 100.0, 87.0, 100.0, 100.0]))
    # Stop = 100.0 * 0.88 = 88.0. Low 87.0 <= 88.0 triggers exit on bar 2.
    assert sigs == [1, 1, 0, 0, 0]


def test_hold_under_12pct_pullback():
    """A pullback that stays within 12% of peak never triggers the stop."""
    assert _run_strategy(_bars([100.0, 100.0, 92.0])) == [1, 1, 1]


def test_hard_exit_after_bar_20():
    """Flat price that never triggers the stop still force-exits after bar index 20."""
    sigs = _run_strategy(_bars([100.0] * 23))
    assert sigs[:21] == [1] * 21
    assert sigs[21:] == [0, 0]


def test_a_single_volatile_bar_cannot_trip_its_own_stop():
    """A bar's own High must not inflate the peak used to check that SAME bar's own
    Low -- same regression class ipo_strategy.py's 2026-09-03 fix covers. A wide first
    bar (Open 100, High 150) must still result in a real entry: bar 0's own Low (90) is
    checked against the peak as it stood BEFORE this bar's own high (100, not 150) --
    90 > 100*0.88=88, so it holds. A buggy peak-before-check ordering would compute
    stop = 150*0.88 = 132 and incorrectly trip on Low 90."""
    df = _bars([90.0, 150.0, 150.0], open_=[100.0, 150.0, 150.0], high=[150.0, 150.0, 150.0])
    assert _run_strategy(df) == [1, 1, 1]


def test_live_window_holds_on_the_last_bar_after_a_recent_entry():
    """The 2026-09-24 regression: live_tick passes a ~124-bar window and acts on the LAST
    signal. The old code anchored entry to bar 0, so the last signal was always 0 (never
    bought). Anchored to the real entry date, it must be 1 on the last bar."""
    df = _bars([100.0] * 124, start="2026-05-24")
    entry = str(df.index[-3].date())
    sigs = _run_strategy(df, entry)
    assert sigs[-1] == 1
    assert sigs[:-3] == [0] * 121


def test_no_bars_on_or_after_entry_is_all_flat():
    assert _run_strategy(_bars([100.0] * 5), entry="2027-01-01") == [0] * 5


def test_entry_date_round_trips():
    assert entry_date_of(trend_basket_code("2026-09-24")) == "2026-09-24"
    assert entry_date_of("def strategy(data): return [0]") is None
