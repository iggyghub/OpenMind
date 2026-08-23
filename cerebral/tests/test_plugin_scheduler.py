"""Unit tests for SchedulerPlugin's paper-trade execution + dispatch (issue #848)."""
import pytest
from datetime import datetime, timedelta, timezone

from plugins.scheduler import SchedulerPlugin
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.forward_record import ForwardRecord


def _plugin(tmp_path):
    return SchedulerPlugin(db_path=str(tmp_path / "sched.db"))


def _record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cerebral.trading.forward_record._DB_PATH", tmp_path / "forward_fills.db"
    )
    return ForwardRecord()


def test_run_paper_strategy_places_order_and_records_fill(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    broker = StubBrokerClient()
    record = _record(tmp_path, monkeypatch)

    result = plugin._run_paper_strategy(
        "MA cross test", broker, record, {"symbol": "AAPL", "position_size": 10}
    )

    assert result["status"] == "executed"
    fills = record.get_fills(strategy_id="MA cross test")
    assert len(fills) == 1
    assert fills[0]["symbol"] == "AAPL"
    assert fills[0]["phase"] == "paper"


def test_run_paper_strategy_no_config_does_not_crash(tmp_path, monkeypatch):
    # config=None (the default) must not raise -- .get() was previously
    # called on it before a None-guard existed.
    plugin = _plugin(tmp_path)
    broker = StubBrokerClient()
    record = _record(tmp_path, monkeypatch)

    result = plugin._run_paper_strategy("unconfigured strat", broker, record)

    assert result["status"] == "executed"


def test_run_paper_strategy_no_broker_skips(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    record = _record(tmp_path, monkeypatch)

    result = plugin._run_paper_strategy("no broker", None, record)

    assert result["status"] == "skipped"


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
    the dispatcher's tick logic (inlined here, matching cerebral/main.py's
    _scheduler_loop) finds it due, executes it, and a real fill with a real
    price and the correct strategy_id lands in forward_fills."""
    plugin = _plugin(tmp_path)
    broker = StubBrokerClient()
    record = _record(tmp_path, monkeypatch)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    plugin._create_event({"title": "MA cross test", "start_iso": past, "recurrence": "5m"})

    due = plugin.list_due_events()
    assert len(due) == 1
    for evt in due:
        result = plugin._run_paper_strategy(evt["title"], broker, record, {})
        assert result["status"] == "executed"
        plugin.mark_event_run(evt["id"])

    fills = record.get_fills(strategy_id="MA cross test")
    assert len(fills) == 1
    assert fills[0]["price"] > 0  # StubBrokerClient's real simulated price (S8), not a placeholder
    assert plugin.list_due_events() == []  # idempotent -- won't re-fire immediately
