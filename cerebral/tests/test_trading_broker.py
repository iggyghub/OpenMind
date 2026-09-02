import pytest
from cerebral.trading.broker import StubBrokerClient, AlpacaBrokerClient, BrokerClient, Side, OrderType
from cerebral.trading.live_tick import find_position


class _FakeEnumVal:
    """Mimics alpaca-py's enum fields (o.status.value) without depending on
    the real package -- the broker only ever reads .value off these."""
    def __init__(self, value):
        self.value = value


class _FakeAlpacaOrder:
    """__slots__ deliberately excludes any field not on the real (pydantic)
    Order model -- e.g. no `fees` -- so a broker.py access to a field that
    doesn't actually exist raises AttributeError here too, the same way it
    does against the real alpaca-py model. Field types (str qty/prices)
    match what the real API returns, not what would be convenient."""
    __slots__ = ("id", "symbol", "qty", "filled_qty", "side", "type", "status", "filled_avg_price")

    def __init__(self, id="o1", symbol="AAPL", qty="10", filled_qty="0",
                 side="buy", type="market", status="new", filled_avg_price=None):
        self.id = id
        self.symbol = symbol
        self.qty = qty
        self.filled_qty = filled_qty
        self.side = _FakeEnumVal(side)
        self.type = _FakeEnumVal(type)
        self.status = _FakeEnumVal(status)
        self.filled_avg_price = filled_avg_price


class _FakeAlpacaClient:
    """Stands in for alpaca.trading.client.TradingClient: submit_order
    returns the just-submitted (unfilled) snapshot; get_order_by_id replays
    a scripted status sequence, one call each -- the real fill's async
    delay compressed to "next call sees the next state"."""
    def __init__(self, statuses_after_submit):
        self._statuses = list(statuses_after_submit)
        self.get_order_by_id_calls = 0

    def submit_order(self, req):
        return _FakeAlpacaOrder(status="new")

    def get_order_by_id(self, order_id):
        self.get_order_by_id_calls += 1
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return _FakeAlpacaOrder(
            id=order_id, status=status,
            filled_qty="10" if status in ("filled", "partially_filled") else "0",
            filled_avg_price="101.5" if status in ("filled", "partially_filled") else None,
        )


def _connected_alpaca_client(statuses_after_submit):
    broker = AlpacaBrokerClient(env="paper")
    broker._connected = True
    broker._client = _FakeAlpacaClient(statuses_after_submit)
    return broker


def test_alpaca_get_order_uppercases_status():
    """alpaca-py reports lowercase status values ("filled"); the broker
    Protocol's contract (and every caller, e.g. run_strategy_tick's status
    check) uses StubBrokerClient's uppercase convention."""
    broker = _connected_alpaca_client(["filled"])
    order = broker.get_order("o1")
    assert order.status == "FILLED"


def test_alpaca_place_order_returns_immediately_once_already_filled(monkeypatch):
    """The common case: a regular-hours market order is already filled by
    the first poll -- no sleep needed."""
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))
    broker = _connected_alpaca_client(["filled"])
    order = broker.place_order("AAPL", 10, "buy", "market")
    assert order.status == "FILLED"
    assert order.price == 101.5
    assert sleep_calls == []


def test_alpaca_place_order_polls_until_filled(monkeypatch):
    """An order that isn't filled on the first read gets polled again
    rather than being reported back to the caller as unfilled."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    broker = _connected_alpaca_client(["new", "new", "filled"])
    order = broker.place_order("AAPL", 10, "buy", "market")
    assert order.status == "FILLED"
    assert broker._client.get_order_by_id_calls == 3


class _FakeAlpacaAccount:
    """alpaca-py's TradeAccount -- day-trade info is daytrade_count (a used
    count), there is no day_trades_remaining attribute. cash/equity/
    buying_power are strings on the real API -- not floats -- so these
    are too, to actually exercise get_account()'s float() casts."""
    def __init__(self, daytrade_count=0, status="ACTIVE"):
        self.cash = "1000.0"
        self.equity = "1000.0"
        self.buying_power = "1000.0"
        self.status = _FakeEnumVal(status)
        self.daytrade_count = daytrade_count


def test_alpaca_get_account_maps_daytrade_count_to_remaining():
    broker = AlpacaBrokerClient(env="paper")
    broker._connected = True
    broker._client = type("C", (), {"get_account": lambda self: _FakeAlpacaAccount(daytrade_count=1)})()
    acc = broker.get_account()
    assert acc.day_trades_remaining == 2
    assert acc.status == "ACTIVE"


def test_alpaca_get_account_casts_financials_to_float():
    """Real bug, live-observed: cash/equity/buying_power come back as
    strings from Alpaca's API. Passed through unconverted, RiskManager's
    account_equity * pct math raised "can't multiply sequence by
    non-int of type 'float'" on every strategy that reached a risk
    check, the first day paper trading actually ran for real."""
    broker = AlpacaBrokerClient(env="paper")
    broker._connected = True
    broker._client = type("C", (), {"get_account": lambda self: _FakeAlpacaAccount()})()
    acc = broker.get_account()
    assert acc.equity == 1000.0 and isinstance(acc.equity, float)
    assert acc.equity * 0.02 == 20.0  # would raise a TypeError before the fix


def test_alpaca_get_account_day_trades_remaining_floors_at_zero():
    broker = AlpacaBrokerClient(env="paper")
    broker._connected = True
    broker._client = type("C", (), {"get_account": lambda self: _FakeAlpacaAccount(daytrade_count=5)})()
    acc = broker.get_account()
    assert acc.day_trades_remaining == 0


def test_alpaca_get_account_handles_none_daytrade_count():
    """A fresh paper account with zero recorded day trades reports
    daytrade_count as None, not 0 -- observed live against a real new
    Alpaca paper account, not just guessed."""
    broker = AlpacaBrokerClient(env="paper")
    broker._connected = True
    broker._client = type("C", (), {"get_account": lambda self: _FakeAlpacaAccount(daytrade_count=None)})()
    acc = broker.get_account()
    assert acc.day_trades_remaining == 3


def test_stub_place_order_buy():
    stub = StubBrokerClient()
    order = stub.place_order("AAPL", 10, "buy", "market")
    assert order.symbol == "AAPL"
    assert order.qty == 10
    assert order.status == "FILLED"
    assert order.side == "buy"


def test_stub_place_order_sell():
    stub = StubBrokerClient()
    # First buy to establish position
    stub.place_order("MSFT", 5, "buy", "market")
    order = stub.place_order("MSFT", 3, "sell", "limit", limit_price=310.50)
    assert order.symbol == "MSFT"
    assert order.qty == 3
    assert order.status == "FILLED"


def test_stub_limit_order_requires_a_price():
    """A limit order with no price used to silently place at a hardcoded
    $0.01 in the real Alpaca client -- both implementations now refuse
    instead."""
    stub = StubBrokerClient()
    with pytest.raises(ValueError, match="limit_price is required"):
        stub.place_order("MSFT", 3, "sell", "limit")


def test_stub_partial_fills():
    stub = StubBrokerClient()
    stub.enable_partial_fills_for("TSLA", ratio=0.5)
    order = stub.place_order("TSLA", 100, "buy", "market")
    assert order.status == "PARTIALLY_FILLED"
    assert order.filled_qty == 50.0


def test_stub_rejects_order():
    stub = StubBrokerClient()
    stub.reject_next_order_for("GME")
    with pytest.raises(RuntimeError, match="Rejected order for GME"):
        stub.place_order("GME", 1, "buy", "market")


def test_stub_get_account():
    stub = StubBrokerClient()
    acc = stub.get_account()
    assert acc.status == "ACTIVE"
    assert acc.cash == 10000.0


def test_stub_list_positions():
    stub = StubBrokerClient()
    stub.place_order("NVDA", 2, "buy", "market")
    positions = stub.list_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "NVDA"
    assert positions[0].qty == 2.0


def test_stub_cancel_order():
    stub = StubBrokerClient()
    order = stub.place_order("AMZN", 1, "buy", "market")
    stub.cancel_order(order.id)
    retrieved = stub.get_order(order.id)
    assert retrieved.status == "CANCELED"


def test_stub_compliance_with_protocol():
    assert isinstance(StubBrokerClient(), BrokerClient)


def test_stub_configurable_starting_cash():
    # S34 (#901): honoring config starting_cash
    stub = StubBrokerClient({"starting_cash": 5000.0})
    assert stub.get_account().cash == 5000.0
    # omitting config still defaults to 10000.0 (existing behavior unchanged)
    stub_default = StubBrokerClient()
    assert stub_default.get_account().cash == 10000.0


def test_stub_positions_isolated_by_strategy_id():
    """Two strategies trading the same symbol should not clobber each other's positions."""
    stub = StubBrokerClient()
    # Strategy A opens long 10 AAPL
    stub.place_order("AAPL", 10, "buy", "market", strategy_id="strat_a")
    # Strategy B opens short 5 AAPL
    stub.place_order("AAPL", 5, "sell", "market", strategy_id="strat_b")

    # Each strategy should see only its own position
    pos_a = find_position(stub.list_positions(strategy_id="strat_a"), "AAPL")
    pos_b = find_position(stub.list_positions(strategy_id="strat_b"), "AAPL")
    
    assert pos_a is not None and pos_a.qty == 10.0
    assert pos_b is not None and pos_b.qty == -5.0
    
    # Aggregate view should sum them
    aggregated = stub.list_positions()
    agg_aap = find_position(aggregated, "AAPL")
    assert agg_aap is not None and agg_aap.qty == 5.0
    
    # Closing B's position should not affect A's
    stub.place_order("AAPL", 5, "buy", "market", strategy_id="strat_b")
    pos_a_after = find_position(stub.list_positions(strategy_id="strat_a"), "AAPL")
    pos_b_after = find_position(stub.list_positions(strategy_id="strat_b"), "AAPL")
    assert pos_a_after is not None and pos_a_after.qty == 10.0
    assert pos_b_after is None  # B is flat


def test_stub_commission_free():
    stub = StubBrokerClient()
    order = stub.place_order("AAPL", 10, "buy", "market")
    assert order.fees == 0.0


def test_alpaca_positions_isolated_by_strategy_id(monkeypatch):
    """Two strategies trading the same symbol should not clobber each other's positions."""
    broker = _connected_alpaca_client(["filled"])
    # Mock get_all_positions to avoid real API calls or data leakage from the fake client.
    broker._client.get_all_positions = lambda: []

    # Strategy A opens long 10 AAPL
    broker.place_order("AAPL", 10, "buy", "market", strategy_id="strat_a")
    # Strategy B opens short 5 AAPL
    broker.place_order("AAPL", 5, "sell", "market", strategy_id="strat_b")

    # Each strategy should see only its own position
    pos_a = find_position(broker.list_positions(strategy_id="strat_a"), "AAPL")
    pos_b = find_position(broker.list_positions(strategy_id="strat_b"), "AAPL")
    
    assert pos_a is not None and pos_a.qty == 10.0
    assert pos_b is not None and pos_b.qty == -5.0
    
    # Closing B's position should not affect A's
    broker.place_order("AAPL", 5, "buy", "market", strategy_id="strat_b")
    pos_a_after = find_position(broker.list_positions(strategy_id="strat_a"), "AAPL")
    pos_b_after = find_position(broker.list_positions(strategy_id="strat_b"), "AAPL")
    assert pos_a_after is not None and pos_a_after.qty == 10.0
    assert pos_b_after is None  # B is flat


def test_stub_reset():
    """reset() clears positions/orders and restores starting cash/equity/
    buying_power to the configured starting value (#929 fixed place_order
    to actually move them off that value on a real fill -- see
    test_stub_place_order_updates_cash_equity below)."""
    stub = StubBrokerClient()
    stub.place_order("AAPL", 10, "buy", "market")
    assert len(stub.list_positions()) > 0

    stub.reset()
    assert stub.get_account().cash == 10000.0
    assert stub.list_positions() == []

    # Non-default starting_cash
    stub2 = StubBrokerClient({"starting_cash": 75000.0})
    stub2.place_order("MSFT", 5, "buy", "market")
    stub2.reset()
    assert stub2.get_account().cash == 75000.0
    assert stub2.list_positions() == []


def test_stub_place_order_updates_cash_equity():
    """#929: place_order used to leave cash/equity/buying_power frozen at
    starting capital forever. A buy must reduce cash by the fill cost; a
    later sell must return proceeds; equity must track cash + open
    position market value."""
    stub = StubBrokerClient({"starting_cash": 10000.0})
    price = stub._simulated_price("AAPL")

    order = stub.place_order("AAPL", 10, "buy", "market")
    acc = stub.get_account()
    assert order.filled_qty == 10.0
    assert acc.cash == pytest.approx(10000.0 - 10 * price)
    assert acc.buying_power == acc.cash
    assert acc.equity == pytest.approx(10000.0)  # cash spent == position value gained

    stub.place_order("AAPL", 10, "sell", "market")
    acc = stub.get_account()
    assert acc.cash == pytest.approx(10000.0)
    assert acc.equity == pytest.approx(10000.0)
    assert stub.list_positions() == []
