"""Unit tests for SchedulerPlugin's paper-trade execution + dispatch (issue #848)."""
import pytest
from datetime import datetime, timedelta, timezone

from plugins.scheduler import SchedulerPlugin
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.forward_record import ForwardRecord
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


def _fetch(symbol, start, end):
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


def test_run_paper_strategy_reports_a_bad_strategy_instead_of_raising(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    record = _record(tmp_path, monkeypatch)

    result = plugin._run_paper_strategy(
        "broken", StubBrokerClient(), record,
        {"symbol": "AAPL", "code": "def not_a_strategy(): pass"}, fetch=_fetch,
    )

    assert result["status"] == "error"


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
    forward_fills."""
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
    fills = record.get_fills(strategy_id="MA cross test")
    assert len(fills) == 1
    assert fills[0]["price"] > 0  # StubBrokerClient's real simulated price (S8), not a placeholder
    assert fills[0]["symbol"] == "AAPL"  # the spec's real ticker, not "SYMBOL"
    assert plugin.list_due_events() == []  # idempotent -- won't re-fire immediately


def test_end_to_end_scheduled_strategy_buys_then_sells_with_real_pnl(tmp_path, monkeypatch):
    """Two scheduled dispatches of one strategy: the signal flips from long to
    flat between ticks, so tick 1 opens and tick 2 closes -- and the closing
    fill carries a REAL realized P&L (entry vs exit x qty, less the broker's
    own reported fee), not the hardcoded 0.0 every fill used to record."""
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
    lifecycle = StrategyLifecycle()
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
    exit_fees = round(4.0 * 55.0 * 0.001, 2)          # the broker's own 0.1% sim fee
    expected_pnl = (55.0 - 50.0) * 4.0 - exit_fees    # 20.00 gross - 0.22 = 19.78
    assert closed[0]["pnl"] == pytest.approx(expected_pnl)

    rows = record.get_fills(strategy_id="penny breakout")
    assert len(rows) == 2
    assert record.get_equity_curve("penny breakout")[-1] == pytest.approx(expected_pnl)
    assert broker.list_positions() == []  # flat again, per the broker's own book
    # Paper only: nothing here can graduate on 2 trades, and nothing live fired.
    assert lifecycle.get_state("penny breakout").status == "paper"
    assert all(r["phase"] == "paper" for r in rows)
