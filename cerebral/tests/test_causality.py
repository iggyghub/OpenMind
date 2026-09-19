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
