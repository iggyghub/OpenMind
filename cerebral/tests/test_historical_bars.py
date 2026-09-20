"""Research-only long-history bar loader."""
import numpy as np
import pandas as pd

from cerebral.trading.historical_bars import COLUMNS, _normalise, get_daily_bars


def _frame(n=5, multiindex=True):
    idx = pd.bdate_range("2007-01-02", periods=n, tz="America/New_York")
    data = {c: np.arange(1.0, n + 1) for c in COLUMNS}
    df = pd.DataFrame(data, index=idx)
    if multiindex:
        df.columns = pd.MultiIndex.from_product([COLUMNS, ["AAPL"]])
    return df


def test_normalise_flattens_multiindex_and_strips_tz():
    df = _normalise(_frame())
    assert list(df.columns) == COLUMNS and df.index.tz is None and df.index.is_monotonic_increasing


def test_normalise_of_nothing_is_an_empty_frame():
    assert _normalise(pd.DataFrame()).empty and _normalise(None).empty


def test_fetches_once_then_serves_from_the_cache(tmp_path):
    calls = []

    def dl(symbol, start, end):
        calls.append((symbol, start, end))
        return _frame()

    db = tmp_path / "h.db"
    a = get_daily_bars("AAPL", "2007-01-01", "2007-02-01", db_path=db, download=dl)
    b = get_daily_bars("AAPL", "2007-01-01", "2007-02-01", db_path=db, download=dl)
    assert len(calls) == 1
    assert len(a) == 5 and a.equals(b)
    assert a["Close"].iloc[0] == 1.0


def test_a_narrower_request_inside_the_cached_span_needs_no_fetch(tmp_path):
    calls = []
    dl = lambda s, a, b: calls.append(1) or _frame()
    db = tmp_path / "h.db"
    get_daily_bars("X", "2007-01-01", "2008-01-01", db_path=db, download=dl)
    got = get_daily_bars("X", "2007-01-03", "2007-01-06", db_path=db, download=dl)
    assert len(calls) == 1 and len(got) == 3


def test_a_symbol_with_no_data_is_remembered_not_refetched(tmp_path):
    calls = []
    dl = lambda s, a, b: calls.append(1) or pd.DataFrame()
    db = tmp_path / "h.db"
    assert get_daily_bars("DEAD", "2007-01-01", "2008-01-01", db_path=db, download=dl).empty
    assert get_daily_bars("DEAD", "2007-01-01", "2008-01-01", db_path=db, download=dl).empty
    assert len(calls) == 1
