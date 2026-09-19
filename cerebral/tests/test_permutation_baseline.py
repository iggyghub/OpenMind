"""#1251 axis 2: storage, combination, the per-pair test and the background runner."""
import json
import types

import numpy as np
import pandas as pd
import pytest

import plugins.trading_replay as tr
from cerebral.trading.cross_stock_stats import summarize_permutation
from cerebral.trading.cross_stock_store import CrossStockStore
from cerebral.trading.permutation_null import NullResult


@pytest.fixture
def store(tmp_path):
    return CrossStockStore(db_path=str(tmp_path / "p.db"))


def test_permutation_rows_round_trip_and_overwrite(store):
    store.record_permutation("s", "A", 0.5, 0.1, 0.02, 500, 0.0005)
    store.record_permutation("s", "A", 0.6, 0.1, 0.01, 500, 0.0005)  # same pair overwrites
    store.record_permutation("s", "B", 0.2, 0.1, 0.30, 500, 0.0005)
    assert store.get_permutation_done() == {("s", "A"), ("s", "B")}
    assert sorted(store.get_permutation_pvalues()["s"]) == [0.01, 0.30]


def test_non_causal_strategies_are_left_out_of_the_pvalues(store):
    store.record_permutation("leaky", "A", 9.0, 0.0, 0.001, 500, 0.0005)
    store.record_causality("leaky", False, 1, 5)
    assert store.get_permutation_pvalues() == {}


def test_combination_is_bonferroni_over_a_strategys_stocks_then_bh_across_strategies():
    rows = summarize_permutation({
        "edge": [0.0005, 0.4, 0.6, 0.5],       # one very small p among 4 stocks -> 0.002
        "lucky": [0.04, 0.5, 0.7, 0.9],         # one nominal hit among 4 -> 0.16, not significant
        "few": [0.001, 0.002],                   # tested on < 3 stocks: not ranked
    })
    by = {r["strategy_id"]: r for r in rows}
    assert "few" not in by
    assert by["edge"]["p_value"] == pytest.approx(0.002)
    assert by["lucky"]["p_value"] == pytest.approx(0.16)
    assert by["edge"]["significant"] is True
    assert by["lucky"]["significant"] is False
    assert rows[0]["strategy_id"] == "edge"          # sorted by q


def _bars(n=400, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=n)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, n))
    return pd.DataFrame({"Close": close}, index=idx)


def test_pair_uses_the_in_window_position_and_returns():
    bars = _bars()
    position = pd.Series(np.where(np.arange(len(bars)) % 20 < 10, 1.0, 0.0), index=bars.index)
    seen = {}

    def fake_run(code, b, interval):
        seen["n"] = len(b)
        return [], position, {}, None

    start = bars.index[100].isoformat()
    res = tr._permutation_pair("code", "AAPL", "1d", start, "2030-01-01", get_bars=lambda *a: bars, run=fake_run)
    assert isinstance(res, NullResult) and res.n_sims > 0
    assert seen["n"] == len(bars)


def test_pair_is_none_when_the_strategy_fails_to_run_or_never_holds():
    bars = _bars()
    fail = lambda code, b, i: ([], pd.Series(0.0, index=b.index), {}, "sandbox failed")
    flat = lambda code, b, i: ([], pd.Series(0.0, index=b.index), {}, None)
    for run in (fail, flat):
        assert tr._permutation_pair("c", "X", "1d", bars.index[100].isoformat(), "2030-01-01",
                                    get_bars=lambda *a: bars, run=run) is None


async def test_runner_records_results_skips_done_pairs_and_non_causal(store, monkeypatch):
    run = store.create_run("2021-01-01", "2026-01-01")
    for sid in ("good", "leaky", "done"):
        for sym in ("A", "B"):
            store.record_result(run, sid, sym, 0.1, -0.1, 50, None, 0.05)
    store.record_causality("leaky", False, 1, 5)
    store.record_permutation("done", "A", 0.1, 0.0, 0.5, 500, 0.0005)
    specs = [types.SimpleNamespace(strategy_id=s, code="c", interval="1d", cross_test_eligible=True)
             for s in ("good", "leaky", "done")]
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "StrategyStore", lambda: types.SimpleNamespace(list_all=lambda: specs))
    calls = []

    def fake_pair(code, symbol, interval, start, end):
        calls.append(symbol)
        return NullResult(0.3, 0.1, 0.04, 500)

    await tr._run_permutation_baseline(pair_fn=fake_pair)
    done = store.get_permutation_done()
    assert ("good", "A") in done and ("good", "B") in done and ("done", "B") in done
    assert not any(sid == "leaky" for sid, _ in done)
    assert len(calls) == 3  # good/A, good/B, done/B -- not leaky, not the already-done pair


async def test_status_payload_carries_the_permutation_fields(store, monkeypatch):
    for sym in "ABC":
        store.record_permutation("s", sym, 0.5, 0.0, 0.01, 500, 0.0005)
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "build_pairs", lambda specs, basket: [])
    monkeypatch.setattr(tr, "StrategyStore", lambda: types.SimpleNamespace(list_all=lambda: []))
    monkeypatch.setattr(tr, "SettingsStore", lambda: types.SimpleNamespace(get=lambda k: None))
    data = json.loads(await tr.get_cross_stock_replay_status())
    assert data["perm_ranked"] == 1 and data["top_permutation"][0]["strategy_id"] == "s"
    assert "Informational" in data["perm_caveat"]


def test_the_tool_is_registered_and_dispatches(monkeypatch):
    plugin = tr.create()
    assert "start_permutation_baseline" in {t.name for t in plugin.list_tools()}
