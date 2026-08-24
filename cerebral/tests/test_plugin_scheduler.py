"""Unit tests for SchedulerPlugin's paper-trade execution + dispatch (issue #848)."""
import json
import pytest
import pandas as pd
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
    exit_fees = round(4.0 * 55.0 * 0.001, 2)          # the broker's own 0.1% sim fee
    expected_pnl = (55.0 - 50.0) * 4.0 - exit_fees    # 20.00 gross - 0.22 = 19.78
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
