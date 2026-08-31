"""Tests for the live/paper dispatch bridge (cerebral/trading/live_tick.py).

Covers the four pieces that never existed before: evaluating a real strategy
signal, deciding an order against the broker's real position state, computing
real realized P&L on a close, and the graduation/retirement wiring.

No network: every test injects `fetch`.
"""
import numpy as np
import pandas as pd
import pytest

from cerebral.trading.alerts import AlertDispatcher
from cerebral.trading.broker import Position, StubBrokerClient
from cerebral.trading.forward_record import ForwardRecord
from cerebral.trading.lifecycle import StrategyLifecycle
from cerebral.trading.risk_limits import RiskConfig, RiskManager
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
    return lambda symbol, start, end, interval="1d": df


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
    # StubBrokerClient is commission-free (Alpaca) -- no fee to subtract.
    assert closed["pnl"] == pytest.approx((12.0 - 10.0) * 3.0)
    assert find_position(broker.list_positions(), "AAPL") is None  # flat again

    pnls = [f["pnl"] for f in record.get_fills(strategy_id="s1")]
    assert sorted(pnls) == pytest.approx(sorted([0.0, 6.0]))


def test_tick_closes_a_short_at_a_profit_when_price_falls(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = ScriptedPriceBroker([20.0, 15.0])  # short at 20, cover at 15
    fetch = fixed_fetch(make_bars())

    run_strategy_tick("s1", StrategySpec("s1", "AAPL", ALWAYS_SHORT, qty=2.0),
                      broker, record, fetch=fetch)
    closed = run_strategy_tick("s1", StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=2.0),
                               broker, record, fetch=fetch)

    assert closed["status"] == "closed" and closed["side"] == "buy"
    # StubBrokerClient is commission-free (Alpaca) -- no fee to subtract.
    assert closed["pnl"] == pytest.approx((20.0 - 15.0) * 2.0)


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

    def spy_fetch(symbol, start, end, interval="1d"):
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
        self.dispatch_ids = []  # S17: the `dispatch_id` dispatch_due_events actually passed, per call
        self.size_pcts = []  # S20: the `size_pct` dispatch_due_events actually passed, per call
        self.sentiment_labels = []  # 2026-08-31: the `sentiment_label` dispatch_due_events actually passed, per call
        self.claimed_symbols_seen = []  # 2026-08-31: the `claimed_symbols` object passed, per call
        self.bear_case_fns = []  # 2026-08-31: the `bear_case_fn` passed, per call

    def list_due_events(self):
        return self.events

    def mark_event_run(self, event_id):
        self.marked.append(event_id)

    def _run_paper_strategy(self, name, broker, record, config, store=None, fetch=None, phase="paper",
                             dispatch_id=None, risk=None, size_pct=1.0, sentiment_label=None,
                             claimed_symbols=None, bear_case_fn=None):
        self.ran.append(name)
        self.brokers.append(broker)
        self.phases.append(phase)
        self.dispatch_ids.append(dispatch_id)
        self.size_pcts.append(size_pct)
        self.sentiment_labels.append(sentiment_label)
        self.claimed_symbols_seen.append(claimed_symbols)
        self.bear_case_fns.append(bear_case_fn)
        return dict(self.tick_result)


def test_dispatch_runs_and_marks_each_due_event(tmp_path, monkeypatch):
    sched = FakeScheduler([{"id": 1, "title": "s1"}, {"id": 2, "title": "s2"}])

    results = dispatch_due_events(sched, StubBrokerClient(),
                                  make_record(tmp_path, monkeypatch),
                                  lifecycle=StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite"))

    assert sched.ran == ["s1", "s2"]
    assert sched.marked == [1, 2]
    assert [r["strategy"] for r in results] == ["s1", "s2"]


def test_dispatch_threads_sentiment_label_through_to_each_strategy(tmp_path, monkeypatch):
    sched = FakeScheduler([{"id": 1, "title": "s1"}, {"id": 2, "title": "s2"}])

    dispatch_due_events(sched, StubBrokerClient(), make_record(tmp_path, monkeypatch),
                         lifecycle=StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite"),
                         sentiment_label="BEARISH")

    assert sched.sentiment_labels == ["BEARISH", "BEARISH"]


def test_dispatch_skips_a_halted_strategy_but_still_marks_it(tmp_path, monkeypatch):
    sched = FakeScheduler([{"id": 1, "title": "s1"}])
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "halted"

    results = dispatch_due_events(sched, StubBrokerClient(),
                                  make_record(tmp_path, monkeypatch), lifecycle=lifecycle)

    assert sched.ran == []                 # a retired strategy places no trades
    assert sched.marked == [1]             # but doesn't re-check every tick
    assert results[0]["status"] == "halted"


def _add_fill_on_day(record, day_iso, symbol, side, qty, price, pnl, strategy_id="global"):
    """Insert a fill with a controlled timestamp -- add_fill() always stamps
    "now" (S23/#876's distinct-days floor means 30 same-instant fills no
    longer clear the CI bar; real tests of graduation now need real
    distinct calendar days, not a monkeypatched datetime class)."""
    record._con.execute(
        "INSERT INTO forward_fills (timestamp, phase, symbol, side, qty, price, fees, pnl, strategy_id) "
        "VALUES (?, 'paper', ?, ?, ?, ?, 0.0, ?, ?)",
        (f"{day_iso}T12:00:00+00:00", symbol, side, qty, price, pnl, strategy_id),
    )
    record._con.commit()


def test_dispatch_graduates_a_strategy_whose_paper_pnl_clears_the_bar(tmp_path, monkeypatch):
    """Pre-#S10 this was mathematically impossible: every recorded pnl was
    hardcoded 0.0, so check_graduation's `lower > 0` never held."""
    record = make_record(tmp_path, monkeypatch)
    for i in range(30):
        _add_fill_on_day(record, f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "AAPL", "sell", 1.0,
                          12.0, pnl=2.0, strategy_id="s1")
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    results = dispatch_due_events(sched, StubBrokerClient(), record, lifecycle=lifecycle)

    assert results[0].get("graduated") is True
    assert lifecycle.get_state("s1").status == "live"
    assert lifecycle.get_state("s1").position_size_pct == 0.25  # ramp starts at 25%


def test_dispatch_refuses_graduation_on_a_red_flagged_filing(tmp_path, monkeypatch):
    """S28 (#881), exercised through the full dispatch chain (not just
    check_graduation in isolation): dispatch_due_events threads
    symbol/latest_accession_fn/fundamentals_scan_fn/vetted_tickers all the
    way through _apply_lifecycle into check_graduation."""
    record = make_record(tmp_path, monkeypatch)
    for i in range(30):
        _add_fill_on_day(record, f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "AAPL", "sell", 1.0,
                          12.0, pnl=2.0, strategy_id="s1")
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    sched = FakeScheduler([{"id": 1, "title": "s1"}], tick_result={"status": "opened", "symbol": "AAPL"})

    results = dispatch_due_events(
        sched, StubBrokerClient(), record, lifecycle=lifecycle,
        latest_accession_fn=lambda s: "0001-24-000001",
        fundamentals_scan_fn=lambda s: (True, "going concern warning"),
    )

    assert results[0].get("graduated") is not True
    assert lifecycle.get_state("s1").status == "paper"


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


class FakePreflightBroker:
    """Injected via live_broker_factory= so a test never constructs a real
    AlpacaBrokerClient -- preflight() would otherwise make a genuine
    credential/network check even inside a pure unit test (S21/#874)."""

    def __init__(self, ok: bool = True, reason: str = "ok"):
        self.env = "live"
        self._ok = ok
        self._reason = reason

    def preflight(self):
        return self._ok, self._reason


def test_dispatch_goes_live_only_when_armed_and_graduated(tmp_path, monkeypatch):
    paper_broker = StubBrokerClient()
    record = make_record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "live"
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, paper_broker, record, lifecycle=lifecycle, arm=True,
                         live_broker_factory=lambda: FakePreflightBroker(ok=True))

    assert len(sched.brokers) == 1
    assert isinstance(sched.brokers[0], FakePreflightBroker)
    assert sched.brokers[0].env == "live"
    assert sched.phases == ["live"]


def test_dispatch_stays_paper_and_alerts_when_preflight_fails(tmp_path, monkeypatch):
    """The acceptance test #874 names: preflight failure keeps the strategy
    on paper (phase="paper") and emits a critical alert instead of silently
    error-looping -- conservative-continue, per TRADING.md failure behaviour."""
    paper_broker = StubBrokerClient()
    record = make_record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "live"
    sched = FakeScheduler([{"id": 1, "title": "s1"}])
    dispatcher = AlertDispatcher()

    dispatch_due_events(sched, paper_broker, record, lifecycle=lifecycle, arm=True,
                         alert_dispatcher=dispatcher,
                         live_broker_factory=lambda: FakePreflightBroker(ok=False, reason="no creds"))

    assert sched.brokers == [paper_broker]
    assert sched.phases == ["paper"]
    alerts = dispatcher.get_pending()
    assert len(alerts) == 1
    assert alerts[0].event_type == "live_preflight_failed"
    assert alerts[0].severity == "critical"
    assert "no creds" in alerts[0].message


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


# ── S17 (#862): dispatch reads/writes the versioned forward-record key ──────

def test_dispatch_scopes_the_forward_record_to_the_current_version(tmp_path, monkeypatch):
    """A strategy with real prior history under v1 gets edited to v2 (a
    second store.save() -- exactly what edit_strategy's auto-promote does).
    The next dispatch must read/write v2's own, empty forward record --
    not the v1 history -- proving decision #27's "restarts clean" for real,
    through dispatch_due_events itself rather than a unit-level check."""
    store = make_store(tmp_path)
    record = make_record(tmp_path, monkeypatch)
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")

    store.save(StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=1.0))  # v1
    record.add_fill("AAPL", "sell", 1.0, 12.0, pnl=5.0, strategy_id="s1@v1")
    store.save(StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=1.0))  # v2 (an edit)

    sched = FakeScheduler([{"id": 1, "title": "s1"}])
    dispatch_due_events(sched, StubBrokerClient(), record, lifecycle=lifecycle, store=store)

    # The dispatcher used the CURRENT version's key, not the bare title.
    assert sched.dispatch_ids == ["s1@v2"]
    # v1's real history is untouched and still there under its own key...
    assert record.trade_count(strategy_id="s1@v1") == 1
    # ...but v2 (the current, dispatched version) starts clean.
    assert lifecycle.get_state("s1@v2").status == "paper"
    assert record.compute_expectancy_ci(strategy_id="s1@v2")[3] is False  # insufficient (0 trades)


def test_dispatch_falls_back_to_the_bare_id_with_no_lineage(tmp_path, monkeypatch):
    """A strategy dispatched with no store (or no matching lineage row) keeps
    the pre-S17 behavior exactly -- the bare title is the key, unchanged."""
    record = make_record(tmp_path, monkeypatch)
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, StubBrokerClient(), record)

    assert sched.ran == ["s1"]
    assert sched.dispatch_ids == ["s1"]


# ── S20 (#873): risk gate ────────────────────────────────────────────────

def test_tick_blocks_an_over_limit_order_and_places_nothing(tmp_path, monkeypatch):
    """The acceptance test S20/#873 names: an over-limit order is blocked by
    the real dispatch path and broker.place_order is never called."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()  # equity 10000; 2% per-trade cap = 200
    risk = RiskManager(RiskConfig(max_per_trade_risk_pct=2.0))
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=100.0)  # ~100*10 = 1000, way over 200

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()), risk=risk)

    assert result == {"status": "blocked", "blocked_by": "per_trade_risk"}
    assert broker._orders == {}  # nothing was ever placed
    assert record.trade_count(strategy_id="s1") == 0


def test_tick_allows_a_within_limit_order_with_risk_gate(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    risk = RiskManager(RiskConfig(max_per_trade_risk_pct=50.0))  # generous cap
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=2.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()), risk=risk)

    assert result["status"] == "opened"
    assert len(broker._orders) == 1


def test_tick_daily_loss_halt_reads_real_accrued_pnl_not_a_fabricated_zero(tmp_path, monkeypatch):
    """The daily-loss halt reads real accrued P&L from forward_record --
    hardcoding 0.0 (the original wiring) makes the halt permanently inert,
    the same 'documented rails are decorative' bug #873 exists to fix."""
    record = make_record(tmp_path, monkeypatch)
    record.add_fill("MSFT", "sell", 1.0, 100.0, pnl=-700.0, strategy_id="other")  # today, another strategy
    broker = StubBrokerClient()  # equity 10000; 6% daily cap = 600
    risk = RiskManager(RiskConfig(max_daily_loss_pct=6.0))
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=1.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()), risk=risk)

    assert result == {"status": "blocked", "blocked_by": "daily_loss_limit"}
    assert broker._orders == {}


def test_dispatch_applies_live_ramp_to_the_open_qty(tmp_path, monkeypatch):
    """apply_position_ramp's returned pct must actually shrink the qty an
    order opens at -- it used to be computed and logged only, never applied,
    so a '25% ramp' opened at full size."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    lifecycle = StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite")
    lifecycle.get_state("s1").status = "live"  # fresh graduation: count=0 -> ramp 0.25
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, broker, record, lifecycle=lifecycle, arm=True,
                         live_broker_factory=lambda: FakePreflightBroker(ok=True))

    assert sched.size_pcts == [0.25]


def test_tick_ramp_shrinks_the_open_qty_without_mutating_the_frozen_spec(tmp_path, monkeypatch):
    """StrategySpec is frozen -- ramping must not try to assign spec.qty."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=4.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()), size_pct=0.25)

    assert result["status"] == "opened"
    assert result["qty"] == 1.0  # 4.0 * 0.25
    assert spec.qty == 4.0  # the registered spec itself is untouched


def test_blocked_order_and_graduation_alerts_share_one_dispatcher(tmp_path, monkeypatch):
    """Both a blocked-order alert (RiskManager) and a graduation alert
    (StrategyLifecycle) must land in the SAME dispatcher's history -- before
    #873, no dispatcher was ever constructed in production, so
    get_alert_history() returned [] forever regardless of what happened."""
    dispatcher = AlertDispatcher()
    lifecycle = StrategyLifecycle(alert_dispatcher=dispatcher, db_path=tmp_path / "lifecycle.sqlite")
    risk = RiskManager(RiskConfig(max_per_trade_risk_pct=2.0), alert_dispatcher=dispatcher)
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()

    # Trigger a blocked-order alert.
    run_strategy_tick("s1", StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=100.0),
                       broker, record, fetch=fixed_fetch(make_bars()), risk=risk)

    # Trigger a graduation alert: 30 winning fills across 30 distinct days
    # clears both the trade-count and distinct-days bars (S23/#876).
    for i in range(30):
        _add_fill_on_day(record, f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "MSFT", "sell", 1.0,
                          100.0, pnl=10.0, strategy_id="s2")
    lifecycle.check_graduation("s2", record)

    event_types = {a.event_type for a in lifecycle.get_alert_history()}
    assert event_types == {"order_blocked", "paper_to_live_graduation"}


def test_risk_settings_keys_readable_and_writable(tmp_path):
    """S20's three new SettingsStore keys must round-trip without raising --
    they used to not exist at all, so SettingsStore.set() raised ValueError."""
    from cerebral.settings import SettingsStore

    store = SettingsStore(tmp_path / "s.json")
    assert store.get("max_per_trade_risk_pct") == 2.0
    assert store.get("max_daily_loss_pct") == 6.0
    assert store.get("max_concurrent_positions") == 10

    store.set("max_per_trade_risk_pct", 1.5)
    store.set("max_daily_loss_pct", 4.0)
    store.set("max_concurrent_positions", 5)
    assert store.get("max_per_trade_risk_pct") == 1.5
    assert store.get("max_daily_loss_pct") == 4.0
    assert store.get("max_concurrent_positions") == 5


def test_tick_strategy_id_isolation(tmp_path, monkeypatch):
    """Two calls to run_strategy_tick with different strategy_ids on the same
    spec.symbol should each maintain their own position, without closing the
    other's position."""
    record = make_record(tmp_path, monkeypatch)
    broker = ScriptedPriceBroker([10.0, 20.0, 10.0, 5.0])  # A open@10, A close@20, B open@10, B close@5
    long_spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=2.0)
    flat_spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=2.0)
    short_spec = StrategySpec("s2", "AAPL", ALWAYS_SHORT, qty=2.0)
    fetch = fixed_fetch(make_bars())

    # Strategy A opens long
    res_a_open = run_strategy_tick("strat_a", long_spec, broker, record, fetch=fetch)
    assert res_a_open["status"] == "opened"
    assert find_position(broker.list_positions(strategy_id="strat_a"), "AAPL").qty == 2.0
    assert find_position(broker.list_positions(strategy_id="strat_b"), "AAPL") is None

    # Strategy B opens short (should not interfere with A)
    res_b_open = run_strategy_tick("strat_b", short_spec, broker, record, fetch=fetch)
    assert res_b_open["status"] == "opened"
    assert find_position(broker.list_positions(strategy_id="strat_a"), "AAPL").qty == 2.0
    assert find_position(broker.list_positions(strategy_id="strat_b"), "AAPL").qty == -2.0

    # Strategy A closes (flat signal)
    res_a_close = run_strategy_tick("strat_a", flat_spec, broker, record, fetch=fetch)
    assert res_a_close["status"] == "closed"
    # A should be flat now
    assert find_position(broker.list_positions(strategy_id="strat_a"), "AAPL") is None
    # B should still be short
    assert find_position(broker.list_positions(strategy_id="strat_b"), "AAPL").qty == -2.0


def test_risk_manager_reads_live_settings_store_values(tmp_path):
    """main.py wires RiskManager with settings_store=_settings so a user's
    Settings-panel change actually changes live risk behavior -- constructing
    it with only an alert_dispatcher would silently freeze the defaults."""
    from cerebral.settings import SettingsStore

    store = SettingsStore(tmp_path / "s.json")
    store.set("max_per_trade_risk_pct", 50.0)  # generous
    risk = RiskManager(settings_store=store)

    res = risk.check_order(10000.0, 0, 0.0, 4000.0, "AAPL", 40.0)  # 40% of equity

    assert res.allowed  # would be blocked at the 2.0% default


# ── S21b (#874): correlation gate ──────────────────────────────────────────

def _make_correlated_bars(corr=0.8):
    """Create two symbols with high correlation for fixture data."""
    base = pd.DataFrame(
        {"Close": [100.0 + i for i in range(65)]},
        index=pd.date_range("2026-05-01", periods=65, freq="D"),
    )
    # CORX moves 0.8 * AAPL + noise
    noise = np.random.RandomState(42).randn(len(base)) * 2
    corx = base.copy()
    corx["Close"] = (base["Close"] * 0.8 + noise * 2).values
    return base, corx


def test_tick_blocks_high_correlation_open(tmp_path, monkeypatch):
    """A new position that would push correlated exposure over the configured
    max_correlation threshold is blocked and broker.place_order is never called."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    # Pre-fill AAPL position under some other strategy -- the correlation
    # check reads the aggregate whole-book view (#961), so any strategy_id works.
    broker._positions[(None, "AAPL")] = Position(symbol="AAPL", qty=10.0, avg_entry_price=100.0, side="buy", market_value=1000.0, unrealized_pl=0.0, current_price=100.0)

    aapl_df, corx_df = _make_correlated_bars()

    def fixture_fetch(symbol, start, end, interval="1d"):
        if symbol == "AAPL":
            return aapl_df
        return corx_df

    risk = RiskManager(RiskConfig(max_per_trade_risk_pct=50.0))  # isolate correlation, not per-trade risk
    spec = StrategySpec("s1", "CORX", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixture_fetch, risk=risk)

    assert result == {"status": "blocked", "blocked_by": "correlation"}
    assert broker._orders == {}


def test_tick_does_not_block_closing_trade_due_to_correlation(tmp_path, monkeypatch):
    """A closing trade is never blocked by correlation (only opens are checked)."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    # Pre-fill AAPL position under strategy "s1" -- run_strategy_tick("s1", ...)
    # below looks up ITS OWN position by strategy_id (#961), so the key must match.
    broker._positions[("s1", "AAPL")] = Position(symbol="AAPL", qty=10.0, avg_entry_price=100.0, side="buy", market_value=1000.0, unrealized_pl=0.0, current_price=100.0)

    aapl_df, corx_df = _make_correlated_bars()

    def fixture_fetch(symbol, start, end, interval="1d"):
        if symbol == "AAPL":
            return aapl_df
        return corx_df

    risk = RiskManager()
    # Signal to close AAPL (flat)
    spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=10.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixture_fetch, risk=risk)

    assert result["status"] == "closed"
    assert len(broker._orders) == 1


# ── 2026-08-31: market-sentiment gate ───────────────────────────────────────

def test_tick_blocks_new_open_on_bearish_sentiment(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    risk = RiskManager()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()),
                                risk=risk, sentiment_label="BEARISH")

    assert result == {"status": "blocked", "blocked_by": "market_sentiment"}
    assert broker._orders == {}


def test_tick_does_not_block_closing_trade_on_bearish_sentiment(tmp_path, monkeypatch):
    """Only opens are gated -- a close must never be trapped by sentiment."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    broker._positions[("s1", "AAPL")] = Position(symbol="AAPL", qty=10.0, avg_entry_price=100.0, side="buy", market_value=1000.0, unrealized_pl=0.0, current_price=100.0)
    risk = RiskManager()
    spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=10.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()),
                                risk=risk, sentiment_label="BEARISH")

    assert result["status"] == "closed"


def test_tick_not_blocked_when_sentiment_label_is_none(tmp_path, monkeypatch):
    """None (gate off, or no reading yet) never blocks -- default behavior
    is unchanged for every existing caller that doesn't pass sentiment_label."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    risk = RiskManager()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()), risk=risk)

    assert result["status"] == "opened"


# ── 2026-08-31: confidence-scaled sizing ────────────────────────────────────

class _ScriptedConfidenceRecord:
    """Wraps a real ForwardRecord (so add_fill/get_daily_pnl still work)
    but returns a scripted compute_confidence_weight, isolating the
    sizing-multiplier math from needing real seeded fill history."""
    def __init__(self, real_record, confidence):
        self._real = real_record
        self._confidence = confidence

    def compute_confidence_weight(self, strategy_id="global"):
        return self._confidence

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_zero_confidence_sizes_at_the_baseline_qty(tmp_path, monkeypatch):
    record = _ScriptedConfidenceRecord(make_record(tmp_path, monkeypatch), confidence=0.0)
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()))

    assert result["qty"] == 5.0  # 1.0x multiplier, unchanged behavior


def test_positive_confidence_scales_qty_up_clamped_at_1_5x(tmp_path, monkeypatch):
    record = _ScriptedConfidenceRecord(make_record(tmp_path, monkeypatch), confidence=10.0)  # huge, must clamp
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()))

    assert result["qty"] == 7.5  # 5.0 * 1.5


def test_negative_confidence_scales_qty_down_clamped_at_0_5x(tmp_path, monkeypatch):
    record = _ScriptedConfidenceRecord(make_record(tmp_path, monkeypatch), confidence=-10.0)  # huge, must clamp
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()))

    assert result["qty"] == 2.5  # 5.0 * 0.5


def test_confidence_weight_not_queried_on_a_hold(tmp_path, monkeypatch):
    """Only opens pay for compute_confidence_weight -- a hold must never
    call it (see the live regression this guards: it used to run
    unconditionally and broke a fake record with no such method)."""
    class _ExplodingRecord:
        def compute_confidence_weight(self, strategy_id="global"):
            raise AssertionError("must not be called on a hold")

    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, _ExplodingRecord(), fetch=fixed_fetch(make_bars()))

    assert result["status"] == "hold"


# ── 2026-08-31: symbol-claim arbitration ────────────────────────────────────

def test_symbol_claim_blocks_a_second_strategys_open_on_the_same_symbol(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    risk = RiskManager()
    claimed: set = {"AAPL"}
    spec = StrategySpec("s2", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s2", spec, broker, record, fetch=fixed_fetch(make_bars()),
                                risk=risk, claimed_symbols=claimed)

    assert result == {"status": "blocked", "blocked_by": "symbol_claimed"}


def test_symbol_claim_does_not_block_a_closing_trade(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    broker._positions[("s1", "AAPL")] = Position(symbol="AAPL", qty=10.0, avg_entry_price=100.0, side="buy", market_value=1000.0, unrealized_pl=0.0, current_price=100.0)
    risk = RiskManager()
    claimed: set = {"AAPL"}
    spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=10.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()),
                                risk=risk, claimed_symbols=claimed)

    assert result["status"] == "closed"


def test_successful_open_adds_its_symbol_to_the_claimed_set(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    risk = RiskManager()
    claimed: set = set()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()),
                                risk=risk, claimed_symbols=claimed)

    assert result["status"] == "opened"
    assert claimed == {"AAPL"}


def test_claimed_symbols_none_is_a_no_op(tmp_path, monkeypatch):
    """Default behavior for every existing caller that doesn't pass
    claimed_symbols is unchanged."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    risk = RiskManager()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()), risk=risk)

    assert result["status"] == "opened"


def test_dispatch_does_not_carry_claims_between_separate_calls(tmp_path, monkeypatch):
    """dispatch_due_events owns a fresh claim set per call -- a symbol
    claimed on one pass must not still be blocked on the next."""
    sched = FakeScheduler([{"id": 1, "title": "s1"}])

    dispatch_due_events(sched, StubBrokerClient(), make_record(tmp_path, monkeypatch),
                         lifecycle=StrategyLifecycle(db_path=tmp_path / "lifecycle.sqlite"))
    first_claims = sched.claimed_symbols_seen[0]

    dispatch_due_events(sched, StubBrokerClient(), make_record(tmp_path, monkeypatch),
                         lifecycle=StrategyLifecycle(db_path=tmp_path / "lifecycle2.sqlite"))
    second_claims = sched.claimed_symbols_seen[1]

    assert first_claims is not second_claims


# ── 2026-08-31: bear-case veto gate ──────────────────────────────────────────

def test_bear_case_veto_blocks_a_new_open(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    def bear_case_fn(symbol, code, signal):
        return True, "earnings report due tomorrow"

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()),
                                bear_case_fn=bear_case_fn)

    assert result == {"status": "blocked", "blocked_by": "bear_case", "reason": "earnings report due tomorrow"}
    assert broker._orders == {}


def test_bear_case_never_blocks_a_closing_trade(tmp_path, monkeypatch):
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    broker._positions[("s1", "AAPL")] = Position(symbol="AAPL", qty=10.0, avg_entry_price=100.0, side="buy", market_value=1000.0, unrealized_pl=0.0, current_price=100.0)
    spec = StrategySpec("s1", "AAPL", ALWAYS_FLAT, qty=10.0)

    def bear_case_fn(symbol, code, signal):
        raise AssertionError("must not be called for a close")

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()),
                                bear_case_fn=bear_case_fn)

    assert result["status"] == "closed"


def test_bear_case_fn_none_is_a_no_op(tmp_path, monkeypatch):
    """Default (bear_case_fn=None, matches trading_bear_case_gate_enabled's
    default-off) -- unchanged behavior for every existing caller."""
    record = make_record(tmp_path, monkeypatch)
    broker = StubBrokerClient()
    spec = StrategySpec("s1", "AAPL", ALWAYS_LONG, qty=5.0)

    result = run_strategy_tick("s1", spec, broker, record, fetch=fixed_fetch(make_bars()))

    assert result["status"] == "opened"
