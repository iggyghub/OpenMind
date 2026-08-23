"""Tests for the live/paper dispatch bridge (cerebral/trading/live_tick.py).

Covers the four pieces that never existed before: evaluating a real strategy
signal, deciding an order against the broker's real position state, computing
real realized P&L on a close, and the graduation/retirement wiring.

No network: every test injects `fetch`.
"""
import pandas as pd
import pytest

from cerebral.trading.broker import Position, StubBrokerClient
from cerebral.trading.forward_record import ForwardRecord
from cerebral.trading.lifecycle import StrategyLifecycle
from cerebral.trading.live_tick import (
    decide_action,
    dispatch_due_events,
    evaluate_signal,
    find_position,
    position_direction,
    realized_pnl,
    run_strategy_tick,
)
from cerebral.trading.strategy_store import StrategySpec, StrategyStore

ALWAYS_LONG = "def strategy(data):\n    return [1] * len(data)"
ALWAYS_FLAT = "def strategy(data):\n    return [0] * len(data)"
ALWAYS_SHORT = "def strategy(data):\n    return [-1] * len(data)"


def make_bars(n=5):
    return pd.DataFrame(
        {
            "Open": [10.0] * n, "High": [11.0] * n, "Low": [9.0] * n,
            "Close": [10.0 + i for i in range(n)], "Volume": [1000] * n,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="D"),
    )


def fixed_fetch(df):
    return lambda symbol, start, end: df


def make_record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cerebral.trading.forward_record._DB_PATH", tmp_path / "forward_fills.db"
    )
    return ForwardRecord()


def make_store(tmp_path):
    return StrategyStore(db_path=tmp_path / "specs.db")


class ScriptedPriceBroker(StubBrokerClient):
    """StubBrokerClient with a scripted price path, so a close can realize a
    genuine gain instead of always filling at the same per-symbol quote."""

    def __init__(self, prices):
        super().__init__()
        self._scripted = list(prices)

    def _simulated_price(self, symbol):
        if self._scripted:
            return self._scripted.pop(0)
        return super()._simulated_price(symbol)


# ── signal evaluation ──────────────────────────────────────────────────────

def test_evaluate_signal_reads_the_last_bar():
    assert evaluate_signal(lambda d: [0, 0, 1], make_bars()) == 1
    assert evaluate_signal(lambda d: [1, 1, -1], make_bars()) == -1


def test_evaluate_signal_shorter_than_data_is_fine():
    """Warm-up: a 100-bar indicator on 120 bars emits fewer signals."""
    assert evaluate_signal(lambda d: [1], make_bars(50)) == 1


def test_evaluate_signal_empty_means_hold():
    assert evaluate_signal(lambda d: [], make_bars()) == 0
    assert evaluate_signal(lambda d: None, make_bars()) == 0


def test_evaluate_signal_rejects_garbage_rather_than_trading_on_it():
    assert evaluate_signal(lambda d: ["buy!"], make_bars()) == 0
    assert evaluate_signal(lambda d: [7], make_bars()) == 0


def test_evaluate_signal_accepts_a_real_compiled_strategy():
    from cerebral.trading_ideas import _compile_strategy as compile_strategy

    assert evaluate_signal(compile_strategy(ALWAYS_LONG), make_bars()) == 1


# ── position state ─────────────────────────────────────────────────────────

def _pos(symbol="AAPL", qty=1.0, side="buy", entry=10.0):
    return Position(symbol=symbol, qty=qty, avg_entry_price=entry, side=side,
                    market_value=qty * entry, unrealized_pl=0.0, current_price=entry)


def test_position_direction_from_signed_qty():
    assert position_direction(_pos(qty=5)) == 1
    assert position_direction(_pos(qty=-5, side="sell")) == -1
    assert position_direction(None) == 0


def test_position_direction_falls_back_to_alpacas_long_short_wording():
    # Alpaca says side="long"/"short"; StubBrokerClient says "buy"/"sell".
    assert position_direction(_pos(qty=0.0, side="short")) == -1
    assert position_direction(_pos(qty=0.0, side="long")) == 1


def test_find_position_treats_a_zero_qty_row_as_flat():
    assert find_position([_pos(qty=0.0)], "AAPL") is None
    assert find_position([_pos(qty=2.0)], "AAPL") is not None
    assert find_position([_pos(symbol="MSFT")], "AAPL") is None


# ── order decision ─────────────────────────────────────────────────────────

def test_decide_action_opens_when_flat():
    assert decide_action(1, None, 4.0) == ("buy", 4.0, False)
    assert decide_action(-1, None, 4.0) == ("sell", 4.0, False)


def test_decide_action_holds_when_already_in_the_target_state():
    assert decide_action(1, _pos(qty=3), 4.0) is None
    assert decide_action(0, None, 4.0) is None


def test_decide_action_closes_the_whole_position_not_the_default_size():
    # qty comes from the broker's real position (3), not the spec's size (4).
    assert decide_action(0, _pos(qty=3), 4.0) == ("sell", 3.0, True)
    assert decide_action(0, _pos(qty=-3, side="sell"), 4.0) == ("buy", 3.0, True)


def test_decide_action_flip_closes_first_and_does_not_reverse_in_one_order():
    assert decide_action(-1, _pos(qty=3), 4.0) == ("sell", 3.0, True)


# ── P&L ────────────────────────────────────────────────────────────────────

def test_realized_pnl_long_and_short():
    assert realized_pnl(10.0, 12.0, 3.0, 1) == pytest.approx(6.0)
    assert realized_pnl(10.0, 8.0, 3.0, 1) == pytest.approx(-6.0)
    assert realized_pnl(10.0, 8.0, 3.0, -1) == pytest.approx(6.0)
    assert realized_pnl(10.0, 12.0, 3.0, -1) == pytest.approx(-6.0)


def test_realized_pnl_subtracts_the_closing_fee():
    assert realized_pnl(10.0, 12.0, 3.0, 1, fees=0.5) == pytest.approx(5.5)


# ── full tick ──────────────────────────────────────────────────────────────

def test_tick_opens_a_position_on_a_long_signal(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=2.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()))

    assert result["status"] == "opened"
    assert result["side"] == "buy"
    assert result["qty"] == 2.0
    assert result["price"] > 0
    assert result["pnl"] == 0.0  # an open realizes nothing yet
    fills = record.get_fills(strategy_id="s1")
    assert len(fills) == 1 and fills[0]["symbol"] == "AAPL"
    assert find_position(broker.list_positions(), "AAPL").qty == 2.0


def test_tick_holds_when_the_broker_already_shows_the_target_position(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=2.0)
    fetch = fixed_fetch(make_bars())

    run_strategy_tick("s1", spec, broker, record, fetch=fetch)
    second = run_strategy_tick("s1", spec, broker, record, fetch=fetch)

    assert second["status"] == "hold"
    assert record.trade_count(strategy_id="s1") == 1  # no duplicate entry


def test_tick_closes_and_records_a_real_realized_pnl(tmp_path, monkeypatch):
    """The gap this whole slice exists to close: pnl was hardcoded 0.0
    everywhere, so no strategy could mathematically ever graduate."""
    record = make_record(tmp_path, monkeypatch)
    broker = ScriptedPriceBroker([10.0, 12.0])  # buy at 10, sell at 12
    long_spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=3.0)
    flat_spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=3.0)
    fetch = fixed_fetch(make_bars())

    opened = run_strategy_tick("s1", long_spec, broker, record, fetch=fetch)
    closed = run_strategy_tick("s1", flat_spec, broker, record, fetch=fetch)

    assert opened["price"] == 10.0 and opened["pnl"] == 0.0
    assert closed["status"] == "closed"
    assert closed["side"] == "sell"
    assert closed["price"] == 12.0
    exit_fees = round(3.0 * 12.0 * 0.001, 2)  # StubBrokerClient's 0.1% sim fee
    assert closed["pnl"] == pytest.approx((12.0 - 10.0) * 3.0 - exit_fees)
    assert find_position(broker.list_positions(), "AAPL") is None  # flat again

    pnls = [f["pnl"] for f in record.get_fills(strategy_id="s1")]
    assert sorted(pnls) == pytest.approx(sorted([0.0, 6.0 - exit_fees]))


def test_tick_closes_a_short_at_a_profit_when_price_falls(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = ScriptedPriceBroker([20.0, 15.0])  # short at 20, cover at 15
    fetch = fixed_fetch(make_bars())

    run_strategy_tick("s1", StrategySpec("s1", "AAPL", ALWAYS_SHORT, qty=2.0),
                      broker, record, fetch=fetch)
    closed = run_strategy_tick("s1", StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=2.0),
                               broker, record, fetch=fetch)

    assert closed["status"] == "closed" and closed["side"] == "buy"
    exit_fees = round(2.0 * 15.0 * 0.001, 2)
    assert closed["pnl"] == pytest.approx((20.0 - 15.0) * 2.0 - exit_fees)


def test_tick_on_a_flat_signal_while_flat_places_no_order(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=1.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()))

    assert result["status"] == "hold"
    assert broker.list_positions() == []
    assert record.trade_count(strategy_id="s1") == 0


def test_tick_asks_for_the_documented_lookback_window():
    seen = {}

    def spy_fetch(symbol, start, end):
        seen["symbol"], seen["start"], seen["end"] = symbol, start, end
        return make_bars()

    from datetime import date
    run_strategy_tick(
        "s1", StrategySpec("s1", "PENNY", ALWAYS_FLAT), StubBrokerClient(),
        _NullRecord(), fetch=spy_fetch, today=date(2026, 7, 1),
    )
    assert seen["symbol"] == "PENNY"
    assert seen["end"] == "2026-07-01"
    assert seen["start"] == "2026-01-02"  # 180 calendar days back


class _NullRecord:
    def add_fill(self, **kwargs):  # pragma: no cover - never reached on a hold
        raise AssertionError("no fill expected")


# ── dispatcher + lifecycle wiring ──────────────────────────────────────────

class FakeScheduler:
    """Duck-typed stand-in for SchedulerPlugin's dispatcher surface."""

    def __init__(self, events, tick_result=None):
        self.events = events
        self.marked = []
        self.tick_result = tick_result or {"status": "opened"}
        self.ran = []
        self.brokers = []   # the `broker` dispatch_due_events actually passed, per call
        self.phases = []    # the `phase` dispatch_due_events actually passed, per call

    def list_due_events(self):
        return self.events

    def mark_event_run(self, event_id):
        self.marked.append(event_id)

    def _run_paper_strategy(self, name, broker, record, config, store=None, fetch=None, phase="paper"):
        self.ran.append(name)
        self.brokers.append(broker)
        self.phases.append(phase)
        return dict(self.tick_result)


def test_dispatch_runs_and_marks_each_due_event(tmp_path, monkeypatch):
    sched = FakeScheduler([{"id": 1, "title": "s1"}, {"id": 2, "title": "s2"}])

    results = dispatch_due_events(sched, StubBrokerClient(),
                                  make_record(tmp_path, monkeypatch),
                                  lifecycle=StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite"))

    assert sched.ran == ["s1", "s2"]
    assert sched.marked == [1, 2]
    assert [r["strategy"] for r in results] == ["s1", "s2"]


def test_dispatch_skips_a_halted_strategy_but_still_marks_it(tmp_path, monkeypatch):
    sched = FakeScheduler([{"id": 1, "title": "s1"}])
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "halted"

    results = dispatch_due_events(sched, StubBrokerClient(),
                                  make_record(tmp_path, monkeypatch), lifecycle=lifecycle)

    assert sched.ran == []                 # a retired strategy places no trades
    assert sched.marked == [1]             # but doesn't re-check every tick
    assert results[0]["status"] == "halted"


def test_dispatch_graduates_a_strategy_whose_paper_pnl_clears_the_bar(tmp_path, monkeypatch):
    """Pre-#S10 this was mathematically impossible: every recorded pnl was
    hardcoded 0.0, so check_graduation's `lower > 0` never held."""
    record = make_record(tmp_path, monkeypatch)
    for _ in range(30):
        record.add_fill("AAPL", "sell", 1.0, 12.0, pnl=2.0, strategy_id="s1")
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    results = dispatch_due_events(sched, StubBrokerClient(), record, lifecycle=lifecycle)

    assert results[0].get("graduated") is True
    assert lifecycle.get_state("s1").status == "live"
    assert lifecycle.get_state("s1").position_size_pct == 0.25  # ramp starts at 25%


def test_dispatch_does_not_graduate_on_all_zero_pnl(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    for _ in range(30):
        record.add_fill("AAPL", "buy", 1.0, 12.0, pnl=0.0, strategy_id="s1")
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")

    dispatch_due_events(FakeScheduler([{"id": 1, "title": "s1"}]),
                        StubBrokerClient(), record, lifecycle=lifecycle)

    assert lifecycle.get_state("s1").status == "paper"


# ── S11 Part 2/4: arm toggle + live/paper branching ─────────────────────────
# Money-adjacent: a live AlpacaBrokerClient must only ever be constructed
# when BOTH the arm toggle is on AND the strategy has graduated. Every other
# combination must keep using the paper broker that was passed in, with
# phase="paper". AlpacaBrokerClient's __init__ never connects (that happens
# lazily in _connect(), called only from methods like place_order/get_account
# that these tests never call), so isinstance-checking a constructed instance
# here never touches real Alpaca infrastructure.

def test_dispatch_stays_paper_when_disarmed_even_if_graduated(tmp_path, monkeypatch):
    paper_broker = StubBrokerClient()
    record = make_record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "live"
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, paper_broker, record, lifecycle=lifecycle, arm=False)

    assert sched.brokers == [paper_broker]
    assert sched.phases == ["paper"]


def test_dispatch_stays_paper_when_armed_but_not_graduated(tmp_path, monkeypatch):
    paper_broker = StubBrokerClient()
    record = make_record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")  # default status is "paper", not "live"
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, paper_broker, record, lifecycle=lifecycle, arm=True)

    assert sched.brokers == [paper_broker]
    assert sched.phases == ["paper"]


def test_dispatch_goes_live_only_when_armed_and_graduated(tmp_path, monkeypatch):
    from cerebral.trading.broker import AlpacaBrokerClient

    paper_broker = StubBrokerClient()
    record = make_record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "live"
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, paper_broker, record, lifecycle=lifecycle, arm=True)

    assert len(sched.brokers) == 1
    assert isinstance(sched.brokers[0], AlpacaBrokerClient)
    assert sched.brokers[0].env == "live"
    assert sched.phases == ["live"]


def test_dispatch_arm_defaults_off(tmp_path, monkeypatch):
    """No arm= passed at all -- the pre-S11 call shape -- must behave exactly
    like arm=False, never touching AlpacaBrokerClient."""
    paper_broker = StubBrokerClient()
    record = make_record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "live"
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, paper_broker, record, lifecycle=lifecycle)

    assert sched.brokers == [paper_broker]
    assert sched.phases == ["paper"]


def test_strategy_store_round_trip(tmp_path):
    store = make_store(tmp_path)
    assert store.get("nope") is None

    store.save(StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=3.0))
    got = store.get("s1")
    assert (got.symbol, got.code, got.qty) == ("AAPL", ALWAYS_LONG, 3.0)

    store.save(StrategySpec("s1", "MSFT", ALWAYS_FLAT, qty=1.0))  # re-validation
    assert store.get("s1").symbol == "MSFT"
    assert len(store.list_all()) == 1
