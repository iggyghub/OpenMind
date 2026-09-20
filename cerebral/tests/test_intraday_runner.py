"""Intraday storage split, summary, per-pair slicing, background runner and status block."""
import json
import types

import numpy as np
import pandas as pd
import pytest

import plugins.trading_replay as tr
from cerebral.trading.cross_stock_store import CrossStockStore
from cerebral.trading.stress_windows import summarize_intraday


@pytest.fixture
def store(tmp_path):
    return CrossStockStore(db_path=str(tmp_path / "i.db"))


def row(net, gross=None):
    return {"net_return": net, "gross_return": net if gross is None else gross, "benchmark_return": 0.1,
            "max_drawdown": -0.1, "benchmark_max_drawdown": -0.2, "n_trades": 50}


def test_daily_and_intraday_rows_stay_apart(store):
    store.record_stress("daily", "A", "gfc", row(0.1), 0.0002)
    store.record_stress("rule", "A", "i:covid", row(0.1), 0.0002)
    assert set(store.get_stress_rows()) == {"daily"}
    assert set(store.get_intraday_rows()) == {"rule"}
    assert store.get_stress_done() == {("daily", "A")}
    assert store.get_stress_done(intraday=True) == {("rule", "A")}


def test_intraday_summary_needs_positive_net_in_every_window_and_shows_cost_drag():
    def windows(net, gross):
        return {w: [row(net, gross) for _ in range(12)] for w in ("i:covid", "i:recent")}

    res = summarize_intraday({"wins": windows(0.05, 0.08), "loses_to_costs": windows(-0.02, 0.03)})
    by = {r["strategy_id"]: r for r in res["strategies"]}
    assert by["wins"]["profitable"] and not by["loses_to_costs"]["profitable"]
    assert res["profitable"] == 1
    assert by["loses_to_costs"]["windows"]["i:covid"]["median_gross"] == 0.03   # drag visible: gross > 0, net < 0
    assert res["strategies"][0]["strategy_id"] == "wins"


def _bars(n=6000):
    rng = np.random.default_rng(0)
    idx = pd.date_range("2021-01-04 09:30", periods=n, freq="5min")
    idx = idx[(idx.hour * 60 + idx.minute >= 570) & (idx.hour * 60 + idx.minute < 960)]
    return pd.DataFrame({"Close": 100 * np.cumprod(1 + rng.normal(0, 0.001, len(idx)))}, index=idx)


def test_pair_slices_one_run_into_the_intraday_windows():
    bars = _bars(20000)
    runs = []

    def fake_run(code, b, interval):
        runs.append(interval)
        return [], pd.Series(np.where(np.arange(len(b)) % 40 < 20, 1.0, 0.0), index=b.index), {}, None

    out = tr._intraday_pair("c", "A", "2021-06-30", get_bars=lambda *a: bars, run=fake_run)
    assert runs == ["5m"] and "i:bull21" in out


def test_pair_is_none_when_history_is_short_or_the_rule_never_holds():
    bars = _bars(20000)
    flat = lambda c, b, i: ([], pd.Series(0.0, index=b.index), {}, None)
    assert tr._intraday_pair("c", "A", "2021-06-30", get_bars=lambda *a: bars, run=flat) is None
    assert tr._intraday_pair("c", "A", "2021-06-30", get_bars=lambda *a: bars.iloc[:100], run=flat) is None


async def test_runner_registers_rules_and_records_only_them(store, monkeypatch, tmp_path):
    from cerebral.trading.intraday_rules import RULES
    from cerebral.trading.strategy_store import StrategyStore
    strategies = StrategyStore(db_path=tmp_path / "s.db")
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "StrategyStore", lambda: strategies)
    calls = []

    def fake_pair(code, symbol, end):
        calls.append(symbol)
        return {"i:covid": row(0.1)}

    await tr._run_intraday_research(pair_fn=fake_pair, stocks_fn=lambda end: ["A", "B"])
    assert len(calls) == 2 * len(RULES)
    assert {sid for sid, _ in store.get_stress_done(intraday=True)} == set(RULES)
    calls.clear()
    await tr._run_intraday_research(pair_fn=fake_pair, stocks_fn=lambda end: ["A", "B"])
    assert calls == []                                          # resumes: nothing left to do


async def test_status_payload_carries_the_intraday_block(store, monkeypatch):
    for i in range(10):
        store.record_stress("rule", f"S{i}", "i:covid", row(0.05), 0.0002)
    monkeypatch.setattr(tr, "CrossStockStore", lambda: store)
    monkeypatch.setattr(tr, "build_pairs", lambda specs, basket: [])
    monkeypatch.setattr(tr, "StrategyStore", lambda: types.SimpleNamespace(list_all=lambda: []))
    monkeypatch.setattr(tr, "SettingsStore", lambda: types.SimpleNamespace(get=lambda k: None))
    intraday = json.loads(await tr.get_cross_stock_replay_status())["intraday"]
    assert intraday["rules"] == 1 and intraday["profitable"] == 1 and "Informational" in intraday["caveat"]


def test_the_tool_is_registered():
    assert "start_intraday_research" in {t.name for t in tr.create().list_tools()}
