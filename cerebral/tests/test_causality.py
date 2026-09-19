import pandas as pd
import pytest
from cerebral.trading.causality import check_causality, CausalityResult


def _make_bars(n: int) -> pd.DataFrame:
    return pd.DataFrame({"Close": range(n)})


def test_causal_fake():
    bars = _make_bars(1000)

    def fake_eval(code, b):
        # signal at i depends only on bars.Close.iloc[:i+1]
        sigs = [1 if i == 50 else 0 for i in range(len(b))]
        return sigs, None

    res = check_causality(None, bars, evaluate=fake_eval)
    assert res.causal is True
    assert res.mismatches == 0


def test_leaky_fake():
    bars = _make_bars(1000)

    def fake_eval(code, b):
        # signal at i depends on bars.Close.iloc[i+1] when it exists
        sigs = []
        for i in range(len(b)):
            sigs.append(1 if i + 1 < len(b) and b.iloc[i + 1].Close > b.iloc[i].Close else 0)
        return sigs, None

    res = check_causality(None, bars, evaluate=fake_eval)
    assert res.causal is False
    assert res.mismatches >= 1


def test_all_flat():
    bars = _make_bars(1000)

    def fake_eval(code, b):
        return [0] * len(b), None

    res = check_causality(None, bars, evaluate=fake_eval)
    assert res.causal is None
    assert res.mismatches == 0


def test_reason_on_full():
    bars = _make_bars(1000)

    def fake_eval(code, b):
        return [1] * len(b), "some reason"

    res = check_causality(None, bars, evaluate=fake_eval)
    assert res.causal is None


def test_reason_on_truncated():
    bars = _make_bars(1000)

    def fake_eval(code, b):
        return [], "concurrent run limit"

    res = check_causality(None, bars, evaluate=fake_eval)
    assert res.causal is None
    assert res.mismatches == 0


def test_too_few_bars():
    bars = _make_bars(50)  # min_bars=600, n_cuts=11 -> needs >= 611

    def fake_eval(code, b):
        return [0] * len(b), None

    res = check_causality(None, bars, evaluate=fake_eval)
    assert res.causal is None


def test_evaluate_raises():
    bars = _make_bars(1000)

    def fake_eval(code, b):
        raise RuntimeError("boom")

    res = check_causality(None, bars, evaluate=fake_eval)
    assert res.causal is None


def test_static_lookahead_reason_is_non_causal_not_untestable():
    """sandboxed_eval's shift(-N) guard forces the strategy flat WITH a lookahead reason:
    that is proof of a future read, not an untestable strategy."""
    from cerebral.trading.causality import check_causality

    bars = pd.DataFrame({"Close": range(800)})
    ev = lambda code, b: ([0] * len(b), "strategy code reads a future bar via shift(-N) -- lookahead, degrading to flat")
    r = check_causality("x", bars, evaluate=ev)
    assert r.causal is False and r.mismatches == 1


def test_default_cut_count_catches_a_leak_that_only_shows_on_few_bars():
    """11 cut points missed two real leaky strategies that 60 caught (measured 2026-09-19)."""
    import numpy as np
    from cerebral.trading.causality import DEFAULT_CUTS, check_causality

    assert DEFAULT_CUTS == 60
    n = 1500
    bars = pd.DataFrame({"Close": range(n)})
    cuts60 = sorted(set(np.linspace(600, n - 1, 60).astype(int)))
    cuts11 = set(np.linspace(600, n - 1, 11).astype(int))
    leak_at = next(c for c in cuts60 if c not in cuts11)

    def ev(code, b):
        sig = [1] * len(b)
        if len(b) == leak_at:
            sig[-1] = 0  # the truncated run disagrees with the full run at exactly one cut
        return sig, None

    assert check_causality("x", bars, evaluate=ev, n_cuts=11).causal is True   # the old default misses it
    assert check_causality("x", bars, evaluate=ev).causal is False            # the new default catches it


def test_stops_at_the_first_mismatch():
    from cerebral.trading.causality import check_causality

    bars = pd.DataFrame({"Close": range(1500)})
    calls = []

    def ev(code, b):
        calls.append(len(b))
        return ([1] * len(b) if len(b) == len(bars) else [0] * len(b)), None  # every truncated run disagrees

    r = check_causality("x", bars, evaluate=ev)
    assert r.causal is False and r.mismatches == 1 and r.tested == 1
    assert len(calls) == 2  # the full run + the first cut only
