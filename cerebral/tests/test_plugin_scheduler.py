"""Unit tests for SchedulerPlugin's paper-trade execution + dispatch (issue #848)."""
import asyncio
import json
import pytest
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugins.scheduler import SchedulerPlugin
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.forward_record import ForwardRecord
from cerebral.trading.lifecycle import StrategyLifecycle
from cerebral.trading.strategy_store import StrategySpec, StrategyStore

ALWAYS_LONG = "def strategy(data):\n    return [1] * len(data)"
ALWAYS_FLAT = "def strategy(data):\n    return [0] * len(data)"


def _plugin(tmp_path):
    return SchedulerPlugin(db_path=str(tmp_path / "sched.db"))


def _record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cerebral.trading.forward_record._DB_PATH", tmp_path / "forward_fills.db"
    )
    return ForwardRecord()


def _bars():
    import pandas as pd
    return pd.DataFrame(
        {"Open": [10.0] * 3, "High": [11.0] * 3, "Low": [9.0] * 3,
         "Close": [10.0, 11.0, 12.0], "Volume": [100] * 3},
        index=pd.date_range("2026-01-01", periods=3, freq="D"),
    )


def _fetch(symbol, start, end, interval="1d"):
    """Injected everywhere -- a test must never reach yfinance."""
    return _bars()


def test_run_paper_strategy_evaluates_the_strategy_and_records_the_fill(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    broker = StubBrokerClient()
    record = _record(tmp_path, monkeypatch)

    result = plugin._run_paper_strategy(
        "MA cross test", broker, record,
        {"symbol": "AAPL", "code": ALWAYS_LONG, "qty": 10}, fetch=_fetch,
    )

    assert result["status"] == "opened"
    fills = record.get_fills(strategy_id="MA cross test")
    assert len(fills) == 1
    assert fills[0]["symbol"] == "AAPL"
    assert fills[0]["phase"] == "paper"
    assert fills[0]["qty"] == 10


def test_run_paper_strategy_looks_the_spec_up_in_the_store(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")
    store.save(StrategySpec("stored strat", "MSFT", ALWAYS_LONG, qty=4.0))
    record = _record(tmp_path, monkeypatch)

    result = plugin._run_paper_strategy(
        "stored strat", StubBrokerClient(), record, {}, store=store, fetch=_fetch
    )

    assert result["status"] == "opened"
    assert result["symbol"] == "MSFT"


def test_run_paper_strategy_without_a_spec_places_no_trade(tmp_path, monkeypatch):
    """Regression: with no config it used to buy 1 share of a ticker literally
    named "SYMBOL", forever, with no strategy consulted at all."""
    plugin = _plugin(tmp_path)
    record = _record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    store = StrategyStore(db_path=tmp_path / "specs.db")

    result = plugin._run_paper_strategy("unregistered", broker, record, store=store)

    assert result["status"] == "skipped"
    assert "no strategy spec" in result["reason"]
    assert broker.list_positions() == []
    assert record.trade_count(strategy_id="unregistered") == 0


def test_run_paper_strategy_no_broker_skips(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    record = _record(tmp_path, monkeypatch)

    result = plugin._run_paper_strategy("no broker", None, record)

    assert result["status"] == "skipped"


def test_run_paper_strategy_degrades_a_bad_strategy_to_hold_instead_of_raising(tmp_path, monkeypatch, caplog):
    """S13/S14 (#858/#859): strategy code now runs in a real sandbox, which
    never propagates a strategy's own failure as an exception -- it degrades
    to an all-flat signal instead (never crash, never trade on garbage).
    A strategy missing its `def strategy(data)` entirely is exactly this
    case: no exception, no error status, just a hold -- but it must still
    be observable via a WARNING log, not silent."""
    plugin = _plugin(tmp_path)
    record = _record(tmp_path, monkeypatch)

    with caplog.at_level("WARNING", logger="cerebral.trading.sandboxed_eval"):
        result = plugin._run_paper_strategy(
            "broken", StubBrokerClient(), record,
            {"symbol": "AAPL", "code": "def not_a_strategy(): pass"}, fetch=_fetch,
        )

    assert result["status"] == "hold"
    assert any("sandboxed_eval" in r.name for r in caplog.records)


def test_create_event_accepts_short_recurrence(tmp_path):
    plugin = _plugin(tmp_path)
    r = plugin._create_event({
        "title": "strat", "start_iso": "2026-01-01T00:00:00", "recurrence": "5m",
    })
    assert not r.is_error, r.content


def test_create_event_rejects_bad_recurrence(tmp_path):
    plugin = _plugin(tmp_path)
    r = plugin._create_event({
        "title": "strat", "start_iso": "2026-01-01T00:00:00", "recurrence": "bogus",
    })
    assert r.is_error


def test_list_due_events_fires_never_run_event_in_the_past(tmp_path):
    plugin = _plugin(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._create_event({"title": "strat", "start_iso": past, "recurrence": "5m"})

    due = plugin.list_due_events()

    assert len(due) == 1
    assert due[0]["title"] == "strat"


def test_list_due_events_fires_a_past_event_with_a_tz_aware_start_iso(tmp_path):
    """Regression (found live 2026-08-25): ensure_discovery_event stores
    start_iso via datetime.now(timezone.utc).isoformat() -- an offset-
    suffixed string -- while list_due_events compares against a naive
    `now`. fromisoformat used to return that offset straight through,
    crashing every call with "can't compare offset-naive and
    offset-aware datetimes". The autonomous discovery event never fired
    via this path in production because of exactly this."""
    plugin = _plugin(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    plugin._create_event({"title": "strat", "start_iso": past, "recurrence": "5m"})

    due = plugin.list_due_events()

    assert len(due) == 1
    assert due[0]["title"] == "strat"


def test_ensure_discovery_event_is_actually_due_immediately(tmp_path):
    """Integration-level regression for the same bug: the real production
    call path (ensure_discovery_event -> list_due_events), not just a
    hand-built tz-aware start_iso."""
    plugin = _plugin(tmp_path)
    plugin.ensure_discovery_event()

    due = plugin.list_due_events()

    assert any(e["title"] == plugin.DISCOVERY_EVENT_TITLE for e in due)


def test_schema_migrates_an_events_table_missing_last_run_iso(tmp_path):
    """Regression (found live 2026-08-25): the real production
    openmind.db's `events` table predated the last_run_iso column --
    CREATE TABLE IF NOT EXISTS is a no-op against an existing table, so
    every list_due_events() call there threw "no such column:
    last_run_iso", silently swallowed by cerebral/main.py's
    _scheduler_loop. Simulates that pre-migration table shape."""
    import sqlite3
    db_path = tmp_path / "old_schema.db"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            start_iso   TEXT    NOT NULL,
            end_iso     TEXT,
            recurrence  TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

    plugin = SchedulerPlugin(db_path=str(db_path))
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._create_event({"title": "strat", "start_iso": past, "recurrence": "5m"})

    due = plugin.list_due_events()  # must not raise

    assert len(due) == 1


def test_list_due_events_skips_future_event(tmp_path):
    plugin = _plugin(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._create_event({"title": "strat", "start_iso": future, "recurrence": "5m"})

    assert plugin.list_due_events() == []


def test_mark_event_run_makes_it_not_due_until_interval_elapses(tmp_path):
    # Regression: the original idempotency check compared last_run_iso to
    # start_iso (two different quantities -- one "now"-ish, one fixed), which
    # never matched even after a real run, so the same event fired on every
    # single 5-minute tick forever instead of respecting its own recurrence.
    plugin = _plugin(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    r = plugin._create_event({"title": "strat", "start_iso": past, "recurrence": "5m"})
    import json
    event_id = json.loads(r.content)["id"]

    assert len(plugin.list_due_events()) == 1
    plugin.mark_event_run(event_id)
    assert plugin.list_due_events() == []  # just ran -- not due again for 5m


def test_due_event_fires_again_after_recurrence_interval(tmp_path):
    plugin = _plugin(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    r = plugin._create_event({"title": "strat", "start_iso": past, "recurrence": "5m"})
    import json
    event_id = json.loads(r.content)["id"]
    plugin.mark_event_run(event_id)
    assert plugin.list_due_events() == []

    # Simulate 6 minutes having passed since the last run.
    stale = (datetime.now(timezone.utc) - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._con.execute("UPDATE events SET last_run_iso=? WHERE id=?", (stale, event_id))
    plugin._con.commit()

    due = plugin.list_due_events()
    assert len(due) == 1


def test_end_to_end_due_event_dispatches_a_real_paper_trade(tmp_path, monkeypatch):
    """The full chain this whole slice exists for: a strategy gets scheduled,
    the dispatcher (cerebral/main.py's _scheduler_loop calls exactly this
    function) finds it due, evaluates the real strategy, executes it, and a
    real fill with a real price and the correct strategy_id lands in
    forward_fills.

    S17 (#862): the fill lands under the VERSIONED dispatch key
    ("<id>@v<n>"), not the bare strategy_id -- store.save() (S16) always
    creates a lineage row now, so dispatch_due_events always has a real
    current_version to key the forward record by (decision #27's
    restarts-clean-on-edit scoping)."""
    from cerebral.trading.live_tick import dispatch_due_events

    plugin = _plugin(tmp_path)
    broker = StubBrokerClient()
    record = _record(tmp_path, monkeypatch)
    store = StrategyStore(db_path=tmp_path / "specs.db")
    store.save(StrategySpec("MA cross test", "AAPL", ALWAYS_LONG, qty=2.0))
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._create_event({"title": "MA cross test", "start_iso": past, "recurrence": "5m"})

    results = dispatch_due_events(plugin, broker, record, store=store, fetch=_fetch)

    assert [r["status"] for r in results] == ["opened"]
    fills = record.get_fills(strategy_id="MA cross test@v1")
    assert len(fills) == 1
    assert fills[0]["price"] > 0  # StubBrokerClient's real simulated price (S8), not a placeholder
    assert fills[0]["symbol"] == "AAPL"  # the spec's real ticker, not "SYMBOL"
    assert plugin.list_due_events() == []  # idempotent -- won't re-fire immediately


def test_end_to_end_scheduled_strategy_buys_then_sells_with_real_pnl(tmp_path, monkeypatch):
    """Two scheduled dispatches of one strategy: the signal flips from long to
    flat between ticks, so tick 1 opens and tick 2 closes -- and the closing
    fill carries a REAL realized P&L (entry vs exit x qty, less the broker's
    own reported fee), not the hardcoded 0.0 every fill used to record.

    S17 (#862): the second store.save() below (swapping the code so the
    strategy goes flat) is itself a new lineage version -- exactly the
    "editing a strategy restarts its forward record" behavior decision #27
    confirmed. So the open (v1) and close (v2) fills land under two
    different dispatch keys, not one; the P&L math itself (closed[0]["pnl"],
    computed live from the broker's own position, not read back from
    storage) is unaffected and is still the test's real claim."""
    from cerebral.trading.live_tick import dispatch_due_events
    from cerebral.trading.lifecycle import StrategyLifecycle

    class ScriptedPriceBroker(StubBrokerClient):
        def __init__(self, prices):
            super().__init__()
            self._scripted = list(prices)

        def _simulated_price(self, symbol):
            return self._scripted.pop(0) if self._scripted else super()._simulated_price(symbol)

    plugin = _plugin(tmp_path)
    broker = ScriptedPriceBroker([50.0, 55.0])  # buy at 50, sell at 55
    record = _record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    store = StrategyStore(db_path=tmp_path / "specs.db")
    store.save(StrategySpec("penny breakout", "PENNY", ALWAYS_LONG, qty=4.0))
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._create_event({"title": "penny breakout", "start_iso": past, "recurrence": "5m"})

    opened = dispatch_due_events(plugin, broker, record, lifecycle, store, fetch=_fetch)
    assert opened[0]["status"] == "opened"
    assert opened[0]["price"] == 50.0
    assert opened[0]["pnl"] == 0.0

    # The strategy's view changes (re-registered spec now says "hold nothing")
    # and 6 minutes pass, so the recurring event is due again.
    store.save(StrategySpec("penny breakout", "PENNY", ALWAYS_FLAT, qty=4.0))
    stale = (datetime.now(timezone.utc) - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._con.execute("UPDATE events SET last_run_iso=?", (stale,))
    plugin._con.commit()

    closed = dispatch_due_events(plugin, broker, record, lifecycle, store, fetch=_fetch)

    assert closed[0]["status"] == "closed"
    assert closed[0]["side"] == "sell"
    assert closed[0]["price"] == 55.0
    # StubBrokerClient is commission-free (Alpaca) -- pnl is exactly gross.
    expected_pnl = (55.0 - 50.0) * 4.0
    assert closed[0]["pnl"] == pytest.approx(expected_pnl)

    open_rows = record.get_fills(strategy_id="penny breakout@v1")
    close_rows = record.get_fills(strategy_id="penny breakout@v2")
    assert len(open_rows) == 1
    assert len(close_rows) == 1
    assert record.get_equity_curve("penny breakout@v2")[-1] == pytest.approx(expected_pnl)
    assert broker.list_positions() == []  # flat again, per the broker's own book
    # Paper only: nothing here can graduate on 2 trades, and nothing live fired.
    assert lifecycle.get_state("penny breakout@v2").status == "paper"
    assert all(r["phase"] == "paper" for r in open_rows + close_rows)


# ── S11 Part 3: run_gauntlet production entry point ─────────────────────────
# The first attempt at this (PR #855, closed unmerged) called the real
# run_gauntlet with the wrong parameter names entirely (guaranteed TypeError)
# behind a test that mocked run_gauntlet out completely, so the mismatch
# never surfaced. These tests use the REAL run_gauntlet and a REAL compiled
# strategy against injected (never yfinance, never network) price data.

def _trend_prices(n=200, seed=42):
    """A clean regime change (strong uptrend, then strong downtrend) -- a
    trend-following strategy should catch the reversal and keep its gains,
    clearly beating naive buy-and-hold of the same series through the whole
    round trip. Deterministic and reliably VALIDATED with MA_CROSS_CODE."""
    import numpy as np
    rng = np.random.default_rng(seed)
    up = rng.normal(0.004, 0.008, n // 2)
    down = rng.normal(-0.004, 0.008, n - n // 2)
    close = 100 * np.cumprod(1 + np.concatenate([up, down]))
    return pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": np.full(n, 2000),
    })


MA_CROSS_CODE = (
    "def strategy(data):\n"
    "    fast = data['Close'].rolling(10).mean()\n"
    "    slow = data['Close'].rolling(30).mean()\n"
    "    return (fast > slow).astype(int).tolist()\n"
)


async def test_run_gauntlet_requires_symbol_and_hypothesis(tmp_path):
    plugin = _plugin(tmp_path)
    r = await plugin._run_gauntlet({"code": MA_CROSS_CODE})
    assert r.is_error
    assert "symbol" in r.content and "hypothesis" in r.content


async def test_run_gauntlet_requires_code_or_an_idea_source(tmp_path):
    """S15b/#860: code is no longer the only way in -- but at least one
    of code/claim/book+chapter/url must be given."""
    plugin = _plugin(tmp_path)
    r = await plugin._run_gauntlet({"symbol": "AAPL", "hypothesis": "x"})
    assert r.is_error
    assert "code" in r.content


async def test_run_gauntlet_validated_registers_spec_and_schedules_event(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    result = await plugin._run_gauntlet(
        {"code": MA_CROSS_CODE, "symbol": "AAPL", "hypothesis": "MA cross trend test"},
        strategy_store=store, fetch=fetch,
    )

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["verdict"] == "VALIDATED"

    # The real chain this whole slice exists to reach: a spec registered
    # under the hypothesis, and a due, ready-to-dispatch event.
    spec = store.get("MA cross trend test")
    assert spec is not None
    assert spec.symbol == "AAPL"

    due = plugin.list_due_events()
    assert len(due) == 1
    assert due[0]["title"] == "MA cross trend test"


async def test_run_gauntlet_validated_event_actually_dispatches(tmp_path, monkeypatch):
    """Beyond "an event got scheduled" -- proves dispatch_due_events can
    actually pick it up and place a real (paper) trade off the registered
    spec, the same chain S9/S10 already built."""
    from cerebral.trading.live_tick import dispatch_due_events

    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")
    broker = StubBrokerClient()
    record = _record(tmp_path, monkeypatch)

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    result = await plugin._run_gauntlet(
        {"code": MA_CROSS_CODE, "symbol": "AAPL", "hypothesis": "MA cross trend test"},
        strategy_store=store, fetch=fetch,
    )
    assert json.loads(result.content)["verdict"] == "VALIDATED"

    results = dispatch_due_events(plugin, broker, record, store=store, fetch=fetch)

    assert len(results) == 1
    assert results[0]["status"] in ("opened", "hold")  # depends on the signal at the last bar
    assert results[0]["strategy"] == "MA cross trend test"


async def test_run_gauntlet_unvalidated_schedules_nothing(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def flat_fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    result = await plugin._run_gauntlet(
        {"code": "def strategy(data):\n    return [0] * len(data)\n",
         "symbol": "AAPL", "hypothesis": "always flat"},
        strategy_store=store, fetch=flat_fetch,
    )

    assert not result.is_error
    assert json.loads(result.content)["verdict"] == "UNVALIDATED"
    assert plugin.list_due_events() == []


async def test_run_gauntlet_generates_code_from_a_claim_via_the_router(tmp_path):
    """S15b/#860: the actual point of this slice -- a claim (not code)
    reaches a real router-backed to_strategy call and the generated code
    flows into the same gauntlet path code= already used."""
    class FakeRouter:
        async def complete(self, prompt: str, task_type: str) -> str:
            assert task_type == "coding"
            return MA_CROSS_CODE

    plugin = _plugin(tmp_path)
    plugin._router = FakeRouter()
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    result = await plugin._run_gauntlet(
        {"claim": "MA cross trend test", "symbol": "AAPL", "hypothesis": "MA cross trend test"},
        strategy_store=store, fetch=fetch,
    )

    assert not result.is_error, result.content
    assert json.loads(result.content)["verdict"] == "VALIDATED"
    spec = store.get("MA cross trend test")
    assert spec is not None
    assert "rolling(10)" in spec.code  # the router's real output, not the stub


async def test_run_gauntlet_claim_without_a_router_uses_the_stub(tmp_path):
    """No router configured (self._router is None, the default) must not
    crash -- to_strategy's own stub fallback handles it."""
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    result = await plugin._run_gauntlet(
        {"claim": "Buy when RSI < 30", "symbol": "AAPL", "hypothesis": "RSI test"},
        strategy_store=store, fetch=fetch,
    )

    assert not result.is_error, result.content  # degrades to the stub, never crashes
    assert store.get("always flat") is None


# ── S17 (#862): edit_strategy / get_strategy_code ────────────────────────────
# edit_strategy delegates entirely to _run_gauntlet's existing auto-promote
# path (origin/parent_version/strategy_id, added for exactly this call) --
# these tests prove that delegation actually produces real lineage and only
# moves the dispatch pointer on VALIDATED, using the real (unmocked)
# run_gauntlet against the same _trend_prices/MA_CROSS_CODE fixtures S11c's
# tests above already established.

ALWAYS_FLAT_CODE = "def strategy(data):\n    return [0] * len(data)\n"


async def _register_ma_cross(plugin, store, fetch):
    result = await plugin._run_gauntlet(
        {"code": MA_CROSS_CODE, "symbol": "AAPL", "hypothesis": "MA cross trend test"},
        strategy_store=store, fetch=fetch,
    )
    assert json.loads(result.content)["verdict"] == "VALIDATED"


async def test_edit_strategy_validated_records_a_new_version_and_moves_the_pointer(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    await _register_ma_cross(plugin, store, fetch)
    edited_code = MA_CROSS_CODE.replace("rolling(10)", "rolling(9)")

    result = await plugin._edit_strategy(
        {"strategy_id": "MA cross trend test", "code": edited_code},
        strategy_store=store, fetch=fetch,
    )

    assert not result.is_error, result.content
    assert json.loads(result.content)["verdict"] == "VALIDATED"

    version_row = store.get_current_version("MA cross trend test")
    assert version_row["version"] == 2
    assert version_row["origin"] == "user_edited"
    assert version_row["parent_version"] == 1

    spec = store.get("MA cross trend test")
    assert spec.code.rstrip() == edited_code.rstrip()  # dispatch pointer moved to the edit


async def test_edit_strategy_unvalidated_does_not_move_the_dispatch_pointer(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    await _register_ma_cross(plugin, store, fetch)

    result = await plugin._edit_strategy(
        {"strategy_id": "MA cross trend test", "code": ALWAYS_FLAT_CODE},
        strategy_store=store, fetch=fetch,
    )

    assert not result.is_error, result.content
    assert json.loads(result.content)["verdict"] == "UNVALIDATED"

    # No new lineage row, and the strategy keeps running its last-good code.
    version_row = store.get_current_version("MA cross trend test")
    assert version_row["version"] == 1
    spec = store.get("MA cross trend test")
    assert spec.code.rstrip() == MA_CROSS_CODE.rstrip()


async def test_edit_strategy_requires_an_existing_strategy(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    result = await plugin._edit_strategy(
        {"strategy_id": "never registered", "code": ALWAYS_FLAT_CODE},
        strategy_store=store,
    )

    assert result.is_error
    assert "never registered" in result.content


async def test_get_strategy_code_returns_real_source_and_provenance(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    await _register_ma_cross(plugin, store, fetch)

    result = plugin._get_strategy_code({"strategy_id": "MA cross trend test"}, strategy_store=store)

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["code"].rstrip() == MA_CROSS_CODE.rstrip()
    assert "(v1)" in data["provenance"]


def test_get_strategy_code_unknown_strategy_is_an_error(tmp_path):
    plugin = _plugin(tmp_path)
    store = StrategyStore(db_path=tmp_path / "specs.db")

    result = plugin._get_strategy_code({"strategy_id": "never registered"}, strategy_store=store)

    assert result.is_error


# ── S27 (#880): autonomous discovery loop ────────────────────────────────
# Real _run_gauntlet, real compiled strategies, injected fetch/router/
# web_search -- never real network, never a real LLM, matching every other
# slice's SAFETY discipline in this campaign.

class FakeRouterReturningCode:
    """A router whose .complete() always returns MA_CROSS_CODE, so a
    discovered idea's generated strategy is the same deterministic,
    reliably-VALIDATED fixture the rest of this file already uses."""
    def __init__(self):
        self.calls = []

    async def complete(self, prompt: str, task_type: str) -> str:
        self.calls.append((prompt, task_type))
        return MA_CROSS_CODE


class AcceptingRouter(FakeRouterReturningCode):
    """judge_idea's router.complete is called with a DIFFERENT prompt than
    to_strategy's (screening vs. generation) -- distinguish by content so
    one fake router can serve both roles in one test."""
    async def complete(self, prompt: str, task_type: str) -> str:
        self.calls.append((prompt, task_type))
        if "REJECT" in prompt or "ACCEPT" in prompt:  # judge_idea's own prompt
            return "ACCEPT"
        return MA_CROSS_CODE  # to_strategy's own prompt


class RejectingRouter(FakeRouterReturningCode):
    async def complete(self, prompt: str, task_type: str) -> str:
        self.calls.append((prompt, task_type))
        if "REJECT" in prompt or "ACCEPT" in prompt:
            return "REJECT: too vague to test"
        return MA_CROSS_CODE


def _web_search_hits(*hits):
    async def fn(query: str):
        return list(hits)
    return fn


async def test_ensure_discovery_event_is_idempotent(tmp_path):
    plugin = _plugin(tmp_path)

    plugin.ensure_discovery_event()
    plugin.ensure_discovery_event()

    events = plugin._con.execute(
        "SELECT COUNT(*) FROM events WHERE title = ?", (plugin.DISCOVERY_EVENT_TITLE,)
    ).fetchone()[0]
    assert events == 1


async def test_ticker_specific_idea_reaches_run_gauntlet_with_origin_discovered(tmp_path):
    """The acceptance test #880 names: a ticker-specific sourced idea
    reaches run_gauntlet with origin='discovered' and the source URL as
    provenance, without going through the screening pre-filter."""
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    router = FakeRouterReturningCode()
    plugin = SchedulerPlugin(
        db_path=str(tmp_path / "sched.db"), router=router,
        web_search_fn=_web_search_hits({
            "url": "https://example.com/aapl-earnings",
            "title": "AAPL beats on strong earnings",
            "snippet": "AAPL tends to rally after a strong earnings beat.",
        }),
    )

    result = await plugin._run_discovery({"queries": ["aapl earnings"]}, strategy_store=store, fetch=fetch)

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["sourced"] == 1
    assert data["dispatched"] == 1
    # Real registration proves it actually reached run_gauntlet, not a mock.
    # strategy_id defaults to the hypothesis text (pre-existing _run_gauntlet
    # behavior -- no strategy_id was passed), so look up by symbol instead.
    specs = [s for s in store.list_all() if s.symbol == "AAPL"]
    assert len(specs) == 1
    version = store.get_current_version(specs[0].strategy_id)
    assert version["origin"] == "discovered"
    provenance = json.loads(version["provenance_json"])
    assert "example.com/aapl-earnings" in provenance["source"]
    # judge_idea must never have been consulted for a ticker-specific idea.
    judge_prompts = [p for p, t in router.calls if "REJECT" in p or "ACCEPT" in p]
    assert judge_prompts == []


async def test_run_discovery_persists_the_attempt_outcome(tmp_path):
    """S30/#894: the real gauntlet outcome must land in
    plugin._discovery_attempts, not just get counted and discarded."""
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    router = FakeRouterReturningCode()
    plugin = SchedulerPlugin(
        db_path=str(tmp_path / "sched.db"), router=router,
        web_search_fn=_web_search_hits({
            "url": "https://example.com/aapl-earnings",
            "title": "AAPL beats on strong earnings",
            "snippet": "AAPL tends to rally after a strong earnings beat.",
        }),
    )

    result = await plugin._run_discovery({"queries": ["aapl earnings"]}, strategy_store=store, fetch=fetch)

    assert not result.is_error, result.content
    latest = plugin._discovery_attempts.get_latest("AAPL")
    assert latest is not None
    assert latest["verdict"] == "VALIDATED"


async def test_pattern_general_idea_is_screened_and_accepted_reaches_gauntlet(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    router = AcceptingRouter()
    plugin = SchedulerPlugin(
        db_path=str(tmp_path / "sched.db"), router=router,
        web_search_fn=_web_search_hits({
            "url": "https://example.com/mean-reversion",
            "title": "A mean-reversion pattern in equities",
            "snippet": "Stocks that fall 3 days in a row tend to bounce.",
        }),
    )
    plugin._discovery_watchlist.upsert("MSFT")
    # Isolate from the known-liquid overflow (2026-08-26 fix, see
    # discovery.py's prefilter_candidates) -- this test only cares that
    # MSFT specifically reaches the gauntlet, not how wide the pool is.
    plugin._settings.set("discovery_candidate_limit", 1)

    result = await plugin._run_discovery({"queries": ["mean reversion pattern"]}, strategy_store=store, fetch=fetch)

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["dispatched"] == 1
    specs = [s for s in store.list_all() if s.symbol == "MSFT"]
    assert len(specs) == 1
    version = store.get_current_version(specs[0].strategy_id)
    assert version["origin"] == "discovered"
    # judge_idea WAS consulted for a pattern-general idea.
    judge_prompts = [p for p, t in router.calls if "REJECT" in p or "ACCEPT" in p]
    assert len(judge_prompts) == 1


async def test_rejected_pattern_idea_never_reaches_run_gauntlet(tmp_path):
    """The acceptance test #880 names explicitly, exercised at the real
    SchedulerPlugin level (not just discovery.py's own unit test)."""
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        raise AssertionError("fetch must never be called -- the idea was rejected")

    router = RejectingRouter()
    plugin = SchedulerPlugin(
        db_path=str(tmp_path / "sched.db"), router=router,
        web_search_fn=_web_search_hits({
            "url": "https://example.com/vague",
            "title": "Markets go up sometimes",
            "snippet": "Good companies tend to do well over time.",
        }),
    )
    plugin._discovery_watchlist.upsert("MSFT")

    result = await plugin._run_discovery({}, strategy_store=store, fetch=fetch)

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["dispatched"] == 0
    assert [s for s in store.list_all() if s.symbol == "MSFT"] == []


async def test_both_accepted_and_rejected_ideas_log_to_the_activity_log(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    logged = []

    async def record_activity(kind, content):
        logged.append((kind, content))

    router = RejectingRouter()
    plugin = SchedulerPlugin(
        db_path=str(tmp_path / "sched.db"), router=router,
        record_activity_fn=record_activity,
        web_search_fn=_web_search_hits({
            "url": "https://example.com/vague",
            "title": "Markets go up sometimes",
            "snippet": "Good companies tend to do well over time.",
        }),
    )

    await plugin._run_discovery({"queries": ["vague claim"]}, strategy_store=store, fetch=fetch)

    assert len(logged) == 1
    kind, content = logged[0]
    assert content["source"] == "discovery"
    assert content["status"] == "rejected"


async def test_run_discovery_uses_default_queries_when_none_given(tmp_path):
    calls = []

    async def web_search(query):
        calls.append(query)
        return []

    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), web_search_fn=web_search)

    result = await plugin._run_discovery({})

    assert not result.is_error
    assert len(calls) >= 1  # a real default query list was used, not empty


async def test_run_discovery_accepts_explicit_queries(tmp_path):
    calls = []

    async def web_search(query):
        calls.append(query)
        return []

    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), web_search_fn=web_search)

    await plugin._run_discovery({"queries": ["my custom query"]})

    assert calls == ["my custom query"]


async def test_run_discovery_defaults_to_a_day_trading_interval(tmp_path):
    """Fix (2026-08-25): without an explicit interval, _run_gauntlet
    defaults to "1d" -- every discovered strategy was a swing/position
    strategy regardless of what the sourced claim was actually about.
    run_discovery must now ask for an intraday interval by default so
    "focus on day trading" is real, not just a query-wording change."""
    store = StrategyStore(db_path=tmp_path / "specs.db")
    seen_intervals = []

    def fetch(symbol, start, end, interval="1d"):
        seen_intervals.append(interval)
        return _trend_prices()

    router = FakeRouterReturningCode()
    plugin = SchedulerPlugin(
        db_path=str(tmp_path / "sched.db"), router=router,
        web_search_fn=_web_search_hits({
            "url": "https://example.com/aapl-earnings",
            "title": "AAPL beats on strong earnings",
            "snippet": "AAPL tends to rally after a strong earnings beat.",
        }),
    )

    result = await plugin._run_discovery({"queries": ["aapl earnings"]}, strategy_store=store, fetch=fetch)

    assert not result.is_error, result.content
    assert seen_intervals == ["15m"]


async def test_run_discovery_accepts_an_explicit_interval_override(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")
    seen_intervals = []

    def fetch(symbol, start, end, interval="1d"):
        seen_intervals.append(interval)
        return _trend_prices()

    router = FakeRouterReturningCode()
    plugin = SchedulerPlugin(
        db_path=str(tmp_path / "sched.db"), router=router,
        web_search_fn=_web_search_hits({
            "url": "https://example.com/aapl-earnings",
            "title": "AAPL beats on strong earnings",
            "snippet": "AAPL tends to rally after a strong earnings beat.",
        }),
    )

    result = await plugin._run_discovery(
        {"queries": ["aapl earnings"], "interval": "5m"}, strategy_store=store, fetch=fetch,
    )

    assert not result.is_error, result.content
    assert seen_intervals == ["5m"]


# ── S31 (#896): manual discovery start/stop + duration ────────────────────

def test_discovery_defaults_to_disabled(tmp_path):
    """discovery_enabled defaults False -- discovery does NOT run on its
    own until explicitly started (the intended behavior change from
    "always on once the underlying scheduler-loop bug is fixed")."""
    plugin = _plugin(tmp_path)
    status = json.loads(plugin._get_discovery_status({}).content)
    assert status["enabled"] is False


def test_start_discovery_with_no_args_enables_indefinitely(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._start_discovery({})
    status = json.loads(result.content)
    assert status["enabled"] is True
    assert status["stop_at"] == ""


def test_start_discovery_with_duration_sets_a_real_stop_at(tmp_path):
    plugin = _plugin(tmp_path)
    before = datetime.now(timezone.utc)

    result = plugin._start_discovery({"duration_hours": 2})

    status = json.loads(result.content)
    assert status["enabled"] is True
    stop_at = datetime.fromisoformat(status["stop_at"])
    assert stop_at.tzinfo is not None
    delta = stop_at - before
    assert timedelta(hours=1, minutes=59) < delta < timedelta(hours=2, minutes=1)


def test_start_discovery_stores_custom_queries_and_interval(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._start_discovery({"queries": ["5 minute ORB strategy"], "interval": "5m"})

    status = json.loads(plugin._get_discovery_status({}).content)
    assert status["queries"] == ["5 minute ORB strategy"]
    assert status["interval"] == "5m"


def test_start_discovery_with_empty_queries_leaves_existing_value_alone(tmp_path):
    """Empty queries/interval on start_discovery must not reset an already-
    customized value back to the built-in default -- #896's own scope note."""
    plugin = _plugin(tmp_path)
    plugin._start_discovery({"queries": ["day trading momentum"], "interval": "5m"})

    plugin._start_discovery({})  # re-enable with no overrides

    status = json.loads(plugin._get_discovery_status({}).content)
    assert status["queries"] == ["day trading momentum"]
    assert status["interval"] == "5m"


def test_start_discovery_defaults_candidate_limit_to_10(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._start_discovery({})
    status = json.loads(result.content)
    assert status["candidate_limit"] == 10


def test_start_discovery_stores_custom_candidate_limit(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._start_discovery({"candidate_limit": 25})

    status = json.loads(plugin._get_discovery_status({}).content)
    assert status["candidate_limit"] == 25


def test_start_discovery_with_no_candidate_limit_leaves_existing_value_alone(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._start_discovery({"candidate_limit": 25})

    plugin._start_discovery({})  # re-enable with no overrides

    status = json.loads(plugin._get_discovery_status({}).content)
    assert status["candidate_limit"] == 25


def test_get_discovery_status_reports_scheduler_heartbeat(tmp_path):
    """Proves the background loop is alive independent of whether discovery
    itself has found anything due -- see scheduler_heartbeat's comment in
    settings.py for why this was added (found live 2026-08-26: the loop
    silently stopped ticking overnight with no visible error anywhere)."""
    plugin = _plugin(tmp_path)
    plugin._settings.set("scheduler_heartbeat", "2026-08-26T02:14:56+00:00")

    status = json.loads(plugin._get_discovery_status({}).content)

    assert status["scheduler_heartbeat"] == "2026-08-26T02:14:56+00:00"


def test_stop_discovery_disables_and_clears_stop_at(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._start_discovery({"duration_hours": 4})

    result = plugin._stop_discovery({})

    status = json.loads(result.content)
    assert status["enabled"] is False
    assert status["stop_at"] == ""


def test_get_discovery_status_reflects_real_settings_store_state(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._settings.set("discovery_enabled", True)
    plugin._settings.set("discovery_interval", "1m")

    status = json.loads(plugin._get_discovery_status({}).content)
    assert status["enabled"] is True
    assert status["interval"] == "1m"


def test_settings_injection_isolates_from_the_real_production_file(tmp_path):
    """Same isolation convention as discovery_watchlist/discovery_attempts:
    a tmp_path-scoped plugin must get its own felix-settings.json, never
    the real production one -- checked by path, not by real-file content
    (which is live machine state this test must not depend on: an
    unrelated setting saved by the real running process at any point would
    make a content-based assertion flaky for reasons having nothing to do
    with this isolation)."""
    from cerebral.settings import _SETTINGS_PATH
    plugin = _plugin(tmp_path)

    assert plugin._settings._path != _SETTINGS_PATH
    assert plugin._settings._path == tmp_path / "felix-settings.json"


def test_start_stop_trading_tools_exist_in_list_tools(tmp_path):
    plugin = _plugin(tmp_path)
    tool_names = [t.name for t in plugin.list_tools()]
    assert "start_trading" in tool_names
    assert "stop_trading" in tool_names


def test_start_trading_enables_paper_trading(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._start_trading({})
    assert not result.is_error
    assert json.loads(result.content) == {"enabled": True}
    assert plugin._settings.get("trading_paper_enabled") is True


def test_stop_trading_disables_paper_trading(tmp_path):
    plugin = _plugin(tmp_path)
    result = plugin._stop_trading({})
    assert not result.is_error
    assert json.loads(result.content) == {"enabled": False}
    assert plugin._settings.get("trading_paper_enabled") is False


# ── 2026-08-26: book ingestion ───────────────────────────────────────────
# Real _run_gauntlet, real compiled strategies, injected fetch/router --
# same SAFETY discipline as the discovery section above. A book is just a
# different idea source; everything downstream is the exact same pipeline.

class BookRouter(FakeRouterReturningCode):
    """Distinguishes THREE prompt shapes sharing one fake: claim
    extraction (books.py's own prompt), judge_idea's ACCEPT/REJECT
    screen, and to_strategy's code generation -- AcceptingRouter above
    only needs to handle the latter two."""
    def __init__(self, claims: list):
        super().__init__()
        self._claims = claims

    async def complete(self, prompt: str, task_type: str) -> str:
        self.calls.append((prompt, task_type))
        if "testable trading-strategy claims" in prompt:
            return "\n".join(self._claims) if self._claims else "NONE"
        if "REJECT" in prompt or "ACCEPT" in prompt:
            return "ACCEPT"
        return MA_CROSS_CODE


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


async def test_upload_book_extracts_and_dispatches_a_claim(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    router = BookRouter(["AAPL tends to rally after strong earnings beats."])
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book(
        {"filename": "wizards.txt", "data_base64": _b64("Some book content about earnings.")},
        strategy_store=store, fetch=fetch,
    )
    assert not result.is_error, result.content
    data = json.loads(result.content)
    book_id = data["book_id"]
    assert data["status"] == "queued"
    assert data["total_chunks"] == 1

    await plugin._book_tasks[book_id]  # wait for the background ingestion to finish

    books = json.loads(plugin._list_books({}, strategy_store=store).content)
    book = next(b for b in books if b["id"] == book_id)
    assert book["status"] == "done"
    assert book["processed_chunks"] == 1
    assert book["strategies_found"] == 1
    # 2026-08-27: the real validated/persisted list, distinct from the
    # dispatch-attempt count above.
    assert len(book["valid_strategies"]) == 1
    assert book["valid_strategies"][0]["symbol"] == "AAPL"

    specs = [s for s in store.list_all() if s.symbol == "AAPL"]
    assert len(specs) == 1
    version = store.get_current_version(specs[0].strategy_id)
    assert version["origin"] == "discovered"


async def test_upload_book_requires_filename_and_data(tmp_path):
    plugin = _plugin(tmp_path)

    result = await plugin._upload_book({})

    assert result.is_error


async def test_upload_book_rejects_invalid_base64(tmp_path):
    plugin = _plugin(tmp_path)

    result = await plugin._upload_book({"filename": "a.txt", "data_base64": "not-valid-base64!!"})

    assert result.is_error


async def test_upload_book_rejects_a_file_with_no_extractable_text(tmp_path):
    plugin = _plugin(tmp_path)

    result = await plugin._upload_book({"filename": "book.epub", "data_base64": _b64("whatever")})

    assert result.is_error
    assert "Could not extract" in result.content


async def test_upload_book_with_no_claims_still_completes(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")
    router = BookRouter([])  # NONE every chunk
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book(
        {"filename": "empty.txt", "data_base64": _b64("Dry narrative, no claims here.")},
        strategy_store=store,
    )
    book_id = json.loads(result.content)["book_id"]

    await plugin._book_tasks[book_id]

    books = json.loads(plugin._list_books({}, strategy_store=store).content)
    book = next(b for b in books if b["id"] == book_id)
    assert book["status"] == "done"
    assert book["strategies_found"] == 0
    assert book["valid_strategies"] == []


async def test_list_books_orders_newest_first(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=BookRouter([]))

    r1 = await plugin._upload_book({"filename": "first.txt", "data_base64": _b64("first book text")}, strategy_store=store)
    await plugin._book_tasks[json.loads(r1.content)["book_id"]]
    r2 = await plugin._upload_book({"filename": "second.txt", "data_base64": _b64("second book text")}, strategy_store=store)
    await plugin._book_tasks[json.loads(r2.content)["book_id"]]

    titles = [b["title"] for b in json.loads(plugin._list_books({}, strategy_store=store).content)]

    assert titles == ["second", "first"]


# ── 2026-08-26: stop_book / retry_book / delete_book ─────────────────────
# User-facing gap found live: no way to stop, redo, or delete an uploaded
# book once it's queued/processing/done.

class BlockingRouter(BookRouter):
    """Blocks the FIRST call to .complete() on `gate` until a test releases
    it -- lets a test observe (and act on) a book while its ingestion task
    is still genuinely mid-chunk, instead of racing a fast fake."""
    def __init__(self, claims):
        super().__init__(claims)
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def complete(self, prompt: str, task_type: str) -> str:
        self.entered.set()
        await self.gate.wait()
        return await super().complete(prompt, task_type)


async def test_stop_book_cancels_the_running_task(tmp_path):
    router = BlockingRouter(["AAPL tends to rally after strong earnings beats."])
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book({"filename": "book.txt", "data_base64": _b64("Some content.")})
    book_id = json.loads(result.content)["book_id"]
    task = plugin._book_tasks[book_id]
    await router.entered.wait()  # ingestion is now blocked mid chunk 1

    stop_result = plugin._stop_book({"book_id": book_id})
    assert not stop_result.is_error
    await task  # cancellation is swallowed internally -- completes normally

    assert plugin._book_store.get(book_id).status == "stopped"


async def test_stop_book_marks_an_orphaned_processing_book_as_stopped(tmp_path):
    """No entry in _book_tasks -- simulates a book left stuck at
    'processing' by a Cerebral restart that lost the in-memory task."""
    plugin = _plugin(tmp_path)
    book = plugin._book_store.add("Orphaned", "o.txt", "/tmp/o.txt")
    plugin._book_store.set_total_chunks(book.id, 5)
    plugin._book_store.update_progress(book.id, 2, 0)

    result = plugin._stop_book({"book_id": book.id})

    assert not result.is_error
    assert plugin._book_store.get(book.id).status == "stopped"


async def test_stop_book_unknown_id_is_an_error(tmp_path):
    plugin = _plugin(tmp_path)

    result = plugin._stop_book({"book_id": 9999})

    assert result.is_error


async def test_retry_book_reprocesses_from_scratch(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    router = BookRouter(["AAPL tends to rally after strong earnings beats."])
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book(
        {"filename": "wizards.txt", "data_base64": _b64("Some book content about earnings.")},
        strategy_store=store, fetch=fetch,
    )
    book_id = json.loads(result.content)["book_id"]
    await plugin._book_tasks[book_id]
    assert plugin._book_store.get(book_id).status == "done"

    retry_result = await plugin._retry_book({"book_id": book_id}, strategy_store=store, fetch=fetch)
    assert not retry_result.is_error, retry_result.content
    await plugin._book_tasks[book_id]

    book = plugin._book_store.get(book_id)
    assert book.status == "done"
    assert book.processed_chunks == 1
    assert book.strategies_found == 1


async def test_retry_book_errors_when_the_stored_file_is_gone(tmp_path):
    plugin = _plugin(tmp_path)
    book = plugin._book_store.add("Missing File", "m.txt", str(tmp_path / "does_not_exist.txt"))

    result = await plugin._retry_book({"book_id": book.id})

    assert result.is_error
    assert "no longer on disk" in result.content


async def test_retry_book_unknown_id_is_an_error(tmp_path):
    plugin = _plugin(tmp_path)

    result = await plugin._retry_book({"book_id": 9999})

    assert result.is_error


async def test_retry_book_is_not_clobbered_by_the_superseded_tasks_late_completion(tmp_path):
    """Cancellation is delivered asynchronously -- retry_book cancels the
    old task and immediately registers a new one under the same book_id.
    The old task's own completion handler must recognize it's no longer
    'current' and skip writing terminal state, or its late cancellation
    settling could clobber the new run's fresh status."""
    router = BlockingRouter(["AAPL tends to rally after strong earnings beats."])
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book({"filename": "book.txt", "data_base64": _b64("Some content.")})
    book_id = json.loads(result.content)["book_id"]
    old_task = plugin._book_tasks[book_id]
    await router.entered.wait()

    retry_result = await plugin._retry_book({"book_id": book_id})
    assert not retry_result.is_error, retry_result.content
    new_task = plugin._book_tasks[book_id]
    assert new_task is not old_task

    await old_task  # let the superseded task's cancellation settle first
    assert plugin._book_store.get(book_id).status != "stopped"  # not clobbered

    router.gate.set()  # release the still-blocked new run so it can finish
    await new_task

    assert plugin._book_store.get(book_id).status == "done"


# ── resume_book / stop-on-terminal-book guard (S33/#900, 2026-08-28) ────
# Real pause/resume: Stop freezes progress, Resume continues from
# processed_chunks instead of Redo's always-restart-from-0.

def _multi_chunk_text(n=3, para_len=6500):
    """Each paragraph exceeds chunk_text's default 6000-char chunk_chars,
    so chunk_text's own documented behavior ("a paragraph longer than
    chunk_chars becomes its own oversized chunk") gives exactly n chunks
    without needing an absurd amount of test text."""
    return "\n\n".join(f"Paragraph {i}: " + ("x" * para_len) for i in range(n))


async def test_resume_book_continues_from_processed_chunks_not_zero(tmp_path):
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    router = BookRouter(["AAPL tends to rally after strong earnings beats."])
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book(
        {"filename": "book.txt", "data_base64": _b64(_multi_chunk_text(3))},
        strategy_store=store, fetch=fetch,
    )
    book_id = json.loads(result.content)["book_id"]
    await plugin._book_tasks[book_id]
    assert plugin._book_store.get(book_id).total_chunks == 3
    assert plugin._book_store.get(book_id).status == "done"

    # Simulate "stopped after chunk 1 of 3" -- update_progress/set_stopped
    # are the exact writes a real interruption mid-run would have made.
    plugin._book_store.update_progress(book_id, 1, 1)
    plugin._book_store.set_stopped(book_id)

    resume_result = await plugin._resume_book({"book_id": book_id}, strategy_store=store, fetch=fetch)
    assert not resume_result.is_error, resume_result.content
    resumed = json.loads(resume_result.content)
    assert resumed["resumed_from_chunk"] == 1

    await plugin._book_tasks[book_id]

    book = plugin._book_store.get(book_id)
    assert book.status == "done"
    assert book.processed_chunks == 3  # not reset, not restarted at 0
    assert book.strategies_found == 3  # 1 already-known + 2 from the resumed chunks


async def test_resume_book_rejects_a_book_that_is_not_stopped(tmp_path):
    plugin = _plugin(tmp_path)
    book = plugin._book_store.add("Active Book", "a.txt", "/a.txt")
    plugin._book_store.set_total_chunks(book.id, 5)  # leaves status "processing"

    result = await plugin._resume_book({"book_id": book.id})

    assert result.is_error
    assert "not stopped" in result.content


async def test_resume_book_unknown_id_is_an_error(tmp_path):
    plugin = _plugin(tmp_path)

    result = await plugin._resume_book({"book_id": 9999})

    assert result.is_error


async def test_resume_book_errors_when_the_stored_file_is_gone(tmp_path):
    plugin = _plugin(tmp_path)
    book = plugin._book_store.add("Missing File", "m.txt", str(tmp_path / "does_not_exist.txt"))
    plugin._book_store.set_total_chunks(book.id, 3)
    plugin._book_store.set_stopped(book.id)

    result = await plugin._resume_book({"book_id": book.id})

    assert result.is_error
    assert "no longer on disk" in result.content


async def test_resume_book_refuses_on_a_chunk_count_mismatch(tmp_path):
    """If re-chunking the stored file today doesn't reproduce the same
    total_chunks recorded when the book was first processed, resuming by
    index would silently misalign -- must refuse, not guess."""
    plugin = _plugin(tmp_path)
    stored = tmp_path / "book.txt"
    stored.write_bytes(_multi_chunk_text(3).encode("utf-8"))
    book = plugin._book_store.add("Drifted Book", "book.txt", str(stored))
    plugin._book_store.set_total_chunks(book.id, 99)  # doesn't match the real re-chunk of 3
    plugin._book_store.set_stopped(book.id)

    result = await plugin._resume_book({"book_id": book.id})

    assert result.is_error
    assert "changed since it was stopped" in result.content


async def test_resume_book_with_nothing_remaining_marks_it_done(tmp_path):
    """processed_chunks already >= total_chunks (shouldn't normally
    happen, but a stray Stop click could land here) -- resume should just
    settle the book as done, not launch a no-op ingestion task."""
    plugin = _plugin(tmp_path)
    stored = tmp_path / "book.txt"
    stored.write_bytes(_multi_chunk_text(3).encode("utf-8"))
    book = plugin._book_store.add("Fully Processed", "book.txt", str(stored))
    plugin._book_store.set_total_chunks(book.id, 3)
    plugin._book_store.update_progress(book.id, 3, 2)
    plugin._book_store.set_stopped(book.id)

    result = await plugin._resume_book({"book_id": book.id})

    assert not result.is_error, result.content
    assert json.loads(result.content)["status"] == "done"
    assert plugin._book_store.get(book.id).status == "done"
    assert book.id not in plugin._book_tasks


async def test_stop_book_on_an_already_done_book_is_an_error_and_does_not_change_status(tmp_path):
    """Regression: this used to silently overwrite a finished book's
    status to 'stopped' -- found live 2026-08-27/28, hand-corrected via
    direct DB calls both times it happened."""
    plugin = _plugin(tmp_path)
    book = plugin._book_store.add("Finished Book", "f.txt", "/f.txt")
    plugin._book_store.set_total_chunks(book.id, 5)
    plugin._book_store.update_progress(book.id, 5, 3)
    plugin._book_store.set_done(book.id)

    result = plugin._stop_book({"book_id": book.id})

    assert result.is_error
    assert "already done" in result.content
    assert plugin._book_store.get(book.id).status == "done"


async def test_stop_book_on_an_already_errored_book_is_an_error_and_does_not_change_status(tmp_path):
    plugin = _plugin(tmp_path)
    book = plugin._book_store.add("Errored Book", "e.txt", "/e.txt")
    plugin._book_store.set_error(book.id, "some failure")

    result = plugin._stop_book({"book_id": book.id})

    assert result.is_error
    assert plugin._book_store.get(book.id).status == "error"


async def test_delete_book_removes_the_record_and_the_stored_file(tmp_path):
    router = BookRouter([])
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book({"filename": "book.txt", "data_base64": _b64("Some content.")})
    book_id = json.loads(result.content)["book_id"]
    await plugin._book_tasks[book_id]
    stored_path = Path(plugin._book_store.get(book_id).stored_path)
    assert stored_path.exists()

    delete_result = plugin._delete_book({"book_id": book_id})

    assert not delete_result.is_error
    assert plugin._book_store.get(book_id) is None
    assert not stored_path.exists()


async def test_delete_book_cancels_an_in_progress_task(tmp_path):
    router = BlockingRouter(["Some claim about AAPL."])
    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"), router=router)

    result = await plugin._upload_book({"filename": "book.txt", "data_base64": _b64("Some content.")})
    book_id = json.loads(result.content)["book_id"]
    task = plugin._book_tasks[book_id]
    await router.entered.wait()

    delete_result = plugin._delete_book({"book_id": book_id})
    assert not delete_result.is_error
    await task  # cancellation settles cleanly, no crash

    assert plugin._book_store.get(book_id) is None


async def test_delete_book_unknown_id_is_an_error(tmp_path):
    plugin = _plugin(tmp_path)

    result = plugin._delete_book({"book_id": 9999})

    assert result.is_error


# ── halt_strategy / resume_strategy (S32/#898, 2026-08-27) ──────────────
# _lifecycle is a post-construction seam (like _on_trading_change) --
# cerebral/main.py wires the real StrategyLifecycle singleton in after
# both objects already exist, so tests inject their own tmp_path-scoped
# one the same way.

def test_halt_strategy_requires_strategy_id(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")

    result = plugin._halt_strategy({})

    assert result.is_error


def test_halt_strategy_without_a_wired_lifecycle_is_an_error(tmp_path):
    plugin = _plugin(tmp_path)  # _lifecycle left at its None default

    result = plugin._halt_strategy({"strategy_id": "s1"})

    assert result.is_error


def test_halt_strategy_sets_status_to_halted(tmp_path):
    plugin = _plugin(tmp_path)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    plugin._lifecycle = lifecycle
    lifecycle.get_state("s1")  # create it, default "paper"

    result = plugin._halt_strategy({"strategy_id": "s1"})

    assert not result.is_error, result.content
    assert lifecycle.get_state("s1").status == "halted"


def test_halt_strategy_triggers_on_trading_change(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    calls = []
    plugin._on_trading_change = lambda: calls.append(1)

    plugin._halt_strategy({"strategy_id": "s1"})

    assert calls == [1]


def test_resume_strategy_requires_strategy_id(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")

    result = plugin._resume_strategy({})

    assert result.is_error


def test_resume_strategy_rejects_a_strategy_that_is_not_halted(tmp_path):
    plugin = _plugin(tmp_path)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    plugin._lifecycle = lifecycle
    lifecycle.get_state("s1")  # default "paper", never halted

    result = plugin._resume_strategy({"strategy_id": "s1"})

    assert result.is_error
    assert "not halted" in result.content


def test_resume_strategy_goes_back_to_paper(tmp_path):
    plugin = _plugin(tmp_path)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    plugin._lifecycle = lifecycle
    lifecycle.halt_strategy("s1")

    result = plugin._resume_strategy({"strategy_id": "s1"})

    assert not result.is_error, result.content
    assert lifecycle.get_state("s1").status == "paper"


async def test_halt_and_resume_strategy_are_reachable_via_call_tool(tmp_path):
    plugin = _plugin(tmp_path)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    plugin._lifecycle = lifecycle
    lifecycle.get_state("s1")

    halt_result = await plugin.call_tool("halt_strategy", {"strategy_id": "s1"})
    assert not halt_result.is_error, halt_result.content
    assert lifecycle.get_state("s1").status == "halted"

    resume_result = await plugin.call_tool("resume_strategy", {"strategy_id": "s1"})
    assert not resume_result.is_error, resume_result.content
    assert lifecycle.get_state("s1").status == "paper"
