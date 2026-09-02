"""Tests for cerebral/trading/sandboxed_eval.py (S13/#858).

Deliberately does NOT mock WindowsSandbox -- the whole point of this module
is that strategy code runs in a real out-of-process sandbox, and a test that
mocks the sandbox out (as the first attempt at this slice did) proves nothing
about whether that's actually true. These tests spawn real child processes
via the real ADR-0010 AppContainer sandbox. Skipped automatically wherever
the sandbox isn't available (non-Windows, or the icacls grant this campaign's
own SAFETY section documents hasn't been applied) rather than failing.
"""
from pathlib import Path

import cerebral
import numpy as np
import pandas as pd
import pytest

from cerebral.sandbox._windows import WindowsSandbox
from cerebral.trading.sandboxed_eval import evaluate_signals

pytestmark = pytest.mark.skipif(
    not WindowsSandbox.available(), reason="ADR-0010 sandbox not available on this machine"
)

MA_CROSS_CODE = (
    "def strategy(data):\n"
    "    fast = data['Close'].rolling(5).mean()\n"
    "    slow = data['Close'].rolling(20).mean()\n"
    "    return (fast > slow).astype(int).tolist()\n"
)


def _bars(n=50):
    return pd.DataFrame(
        {"Open": range(100, 100 + n), "High": range(105, 105 + n),
         "Low": range(95, 95 + n), "Close": range(102, 102 + n),
         "Volume": [1000] * n},
        index=pd.date_range("2026-01-01", periods=n, freq="D"),
    )


def _expected_ma_cross_signals(bars: pd.DataFrame) -> list:
    fast = bars["Close"].rolling(5).mean()
    slow = bars["Close"].rolling(20).mean()
    return (fast > slow).astype(int).tolist()


def test_evaluate_signals_matches_a_direct_in_process_run():
    """The actual acceptance criterion: the sandboxed evaluator must produce
    identical output to running the same strategy function directly."""
    bars = _bars()
    expected = _expected_ma_cross_signals(bars)

    result = evaluate_signals(MA_CROSS_CODE, bars)

    assert result == expected


def test_containment_a_strategy_cannot_write_outside_its_workdir():
    """Real containment, not a mocked-failure stand-in. Targets a path
    inside this repo -- a location the user's own account normally has
    full write access to -- specifically so a blocked write demonstrates
    AppContainer's ACL confinement, not ordinary Windows permissions
    (writing to e.g. C:\\Windows would fail for unrelated reasons even
    outside the sandbox, and wouldn't prove anything). NOT a temp-dir
    path, since TEMP is one of the few env vars the sandbox deliberately
    keeps -- an ambiguous target for a containment test."""
    target = Path(cerebral.__file__).parent / "_sandbox_containment_test.txt"
    malicious_code = (
        "def strategy(data):\n"
        f"    open(r'{target}', 'w').write('escaped the sandbox')\n"
        "    return [0] * len(data)\n"
    )
    bars = _bars()

    try:
        result = evaluate_signals(malicious_code, bars)
        assert all(s == 0 for s in result)
        assert not target.exists()
    finally:
        if target.exists():
            target.unlink()


def test_wrong_shaped_signals_degrades_to_flat_not_a_crash():
    """A strategy that runs successfully but returns garbage (not a list
    of 1/0/-1) must not propagate its wrong-shaped output -- evaluate_signals
    validates the sandbox's own output, not just whether it exited 0."""
    bars = _bars()
    bad_code = (
        "def strategy(data):\n"
        "    return ['a', 'b', 'c']\n"
    )

    result = evaluate_signals(bad_code, bars)

    assert all(s == 0 for s in result)


def test_a_strategy_that_raises_degrades_to_flat():
    bars = _bars()
    raising_code = (
        "def strategy(data):\n"
        "    raise ValueError('boom')\n"
    )

    result = evaluate_signals(raising_code, bars)

    assert all(s == 0 for s in result)


def test_a_full_years_daily_bars_still_produce_real_signals_not_flat():
    """Regression (2026-08-24): a real year of daily OHLCV for one ticker
    is ~36KB base64-encoded -- over CreateProcessW's ~32,767-char command-
    line limit on its own, before the strategy code is even added. Passing
    it as an argv token made every such run fail CreateProcessW outright
    and silently degrade to an all-flat signal (sharpe/return always
    exactly 0.0, regardless of what the strategy code actually said).
    A real crossover strategy against a realistically-sized bar history
    must produce genuine, non-degenerate signals."""
    bars = _bars(n=252)  # ~1 trading year
    expected = _expected_ma_cross_signals(bars)

    result = evaluate_signals(MA_CROSS_CODE, bars)

    assert result == expected
    assert any(s != 0 for s in result), (
        "signals came back all-flat -- the sandbox spawn likely failed "
        "silently (e.g. CreateProcessW command-line length) rather than "
        "actually running the strategy"
    )


def test_workdir_is_cleaned_up_after_a_run():
    from cerebral.trading.sandboxed_eval import _WORKDIR_ROOT

    before = set(_WORKDIR_ROOT.glob("*")) if _WORKDIR_ROOT.exists() else set()
    evaluate_signals(MA_CROSS_CODE, _bars())
    after = set(_WORKDIR_ROOT.glob("*")) if _WORKDIR_ROOT.exists() else set()

    assert after == before


def test_numpy_int64_signal_roundtrips():
    """Strategies returning numpy int64 arrays must round-trip, not degrade to flat."""
    code = (
        "import numpy as np\n"
        "def strategy(data):\n"
        "    return np.array([1, 0, -1], dtype=np.int64)\n"
    )
    bars = _bars(3)
    result = evaluate_signals(code, bars)
    assert result == [1, 0, -1]


def test_pandas_series_signal_roundtrips():
    """Strategies returning a pandas Series of ints must round-trip, not degrade to flat.

    Deliberately does NOT self-import pandas -- a real LLM-generated
    `def strategy(data): ...` body is prompted for the function only, so
    `pd`/`np` must already be available in the exec namespace rather than
    relying on the strategy importing them itself."""
    code = (
        "def strategy(data):\n"
        "    return pd.Series([1, 0, -1], dtype='int64')\n"
    )
    bars = _bars(3)
    result = evaluate_signals(code, bars)
    assert result == [1, 0, -1]


def test_numpy_signal_without_self_import_roundtrips():
    """Same as test_numpy_int64_signal_roundtrips, but without the strategy
    self-importing numpy -- proves np is seeded into the exec namespace,
    not just working by coincidence because the other test imports it."""
    code = (
        "def strategy(data):\n"
        "    return np.array([1, 0, -1], dtype=np.int64)\n"
    )
    bars = _bars(3)
    result = evaluate_signals(code, bars)
    assert result == [1, 0, -1]


def test_plain_python_list_still_works():
    """Existing behavior with plain Python lists of ints must remain unchanged."""
    code = (
        "def strategy(data):\n"
        "    return [1, -1, 0]\n"
    )
    bars = _bars(3)
    result = evaluate_signals(code, bars)
    assert result == [1, -1, 0]
