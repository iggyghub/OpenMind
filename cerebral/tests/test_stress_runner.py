"""Stress-window storage, per-pair slicing, background runner and status block."""
import json
import types

import numpy as np
import pandas as pd
import pytest

import plugins.trading_replay as tr
from cerebral.trading.cross_stock_store import CrossStockStore

ROW = {"net_return": 0.1, "gross_return": 0.11, "benchmark_return": 0.05,
       "max_drawdown": -0.1, "benchmark_max_drawdown": -0.3, "n_trades": 4}


@pytest.fixture
def store(tmp_path):
    return CrossStockStore(db_path=str(tmp_path / "s.db"))


def test_stress_rows_round_trip_and_leave_out_non_causal(store):
    store.record_stress("s", "A", "gfc", ROW, 0.0002)
    store.record_stress("s", "A", "gfc", {**ROW, "net_return": 0.2}, 0.0002)   # overwrite
    store.record_stress("s", "A", "main", ROW, 0.0002)
    store.record_stress("leaky", "A", "gfc", ROW, 0.0002)
    store.record_causality("leaky", False, 1, 5)
    assert store.get_stress_done() == {("s", "A"), ("leaky", "A")}
    rows = store.get_stress_rows()
    assert set(rows) == {"s"} and set(rows["s"]) == {"gfc", "main"}
    assert rows["s"]["gfc"][0]["net_return"] == 0.2


def _bars(n=1500, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2006-01-02", periods=n)
    return pd.DataFrame({"Close": 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, n))}, index=idx)


def test_pair_slices_one_run_into_every_window():
    bars = _bars()
    calls = []

    def fake_run(code, b, interval):
        calls.append(interval)
        return [], pd.Series(np.where(np.arange(len(b)) % 20 < 10, 1.0, 0.0), index=b.index), {}, None

    out = tr._stress_pair("c", "A", "2013-01-01", get_bars=lambda *a: bars, run=fake_run)
    assert len(calls) == 1 and calls == ["1d"]
    assert "gfc" in out and out["gfc"]["n_trades"] > 0


def test_pair_is_none_when_the_run_fails_or_never_holds_or_history_is_short():
    bars = _bars()
    fail = lambda c, b, i: ([], pd.Series(0.0, index=b.index), {}, "boom")
    flat = lambda c, b, i: ([], pd.Series(0.0, index=b.index), {}, None)
    for run in (fail, flat):
        assert tr._stress_pair("c", "A", "2013-01-01", get_bars=lambda *a: bars, run=run) is None
    assert tr._stress_pair("c", "A", "2013-01-01", get_bars=lambda *a: bars.iloc[:50], run=flat) is None


def test_stocks_need_2008_history():
    bars = _bars()
    short = bars[bars.index >= "2012-01-01"]
    got = tr._stress_stocks("2013-01-01", get_bars=lambda s, *a: bars if s == tr.BASKET[0] else short, limit=5)
    assert got == [tr.BASKET[0]]


async def test_runner_records_skips_done_non_causal_and_non_daily(store, monkeypatch):
    store.record_causality("leaky", False, 1, 5)
    store.record_stress("done", "A", "gfc", ROW, 0.0002)
    specs = [types.SimpleNamespace(strategy_id=s, code="c", interval=i, cross_test_eligible=True)
             for s, i in (("good", "1d"), ("leaky", "1d"), ("done", "1d"), ("intraday", "5m"))]
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "StrategyStore", lambda: types.SimpleNamespace(list_all=lambda: specs))
    calls = []

    def fake_pair(code, symbol, end):
        calls.append(symbol)
        return {"gfc": ROW, "main": ROW}

    await tr._run_stress_windows(pair_fn=fake_pair, stocks_fn=lambda end: ["A", "B"])
    done = store.get_stress_done()
    assert done == {("good", "A"), ("good", "B"), ("done", "A"), ("done", "B")}
    assert len(calls) == 3   # good/A, good/B, done/B


async def test_status_payload_carries_the_stress_block(store, monkeypatch):
    for i in range(10):
        for w in ("gfc", "main"):
            store.record_stress("s", f"S{i}", w, ROW, 0.0002)
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "build_pairs", lambda specs, basket: [])
    monkeypatch.setattr(tr, "StrategyStore", lambda: types.SimpleNamespace(list_all=lambda: []))
    monkeypatch.setattr(tr, "SettingsStore", lambda: types.SimpleNamespace(get=lambda k: None))
    stress = json.loads(await tr.get_cross_stock_replay_status())["stress"]
    assert stress["strategies"] == 1 and stress["robust"] == 1 and stress["defensive"] == 1
    assert "Informational" in stress["caveat"]


def test_the_tool_is_registered():
    assert "start_stress_windows" in {t.name for t in tr.create().list_tools()}


async def test_status_payload_lists_strategies_held_for_review(store, monkeypatch):
    store.set_review("s", "untrusted_text", "why")
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "build_pairs", lambda specs, basket: [])
    monkeypatch.setattr(tr, "StrategyStore", lambda: types.SimpleNamespace(list_all=lambda: []))
    monkeypatch.setattr(tr, "SettingsStore", lambda: types.SimpleNamespace(get=lambda k: None))
    data = json.loads(await tr.get_cross_stock_replay_status())
    assert [r["category"] for r in data["needs_review"]] == ["untrusted_text"]
