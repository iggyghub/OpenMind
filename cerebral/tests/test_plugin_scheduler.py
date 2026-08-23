"""Unit tests for SchedulerPlugin._run_paper_strategy (S7, issue #848)."""
import pytest

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
