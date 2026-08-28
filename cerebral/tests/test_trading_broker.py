import pytest
from cerebral.trading.broker import StubBrokerClient, BrokerClient, Side, OrderType


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


def test_stub_commission_free():
    stub = StubBrokerClient()
    order = stub.place_order("AAPL", 10, "buy", "market")
    assert order.fees == 0.0


def test_stub_reset():
    """reset() clears positions/orders and restores starting cash.

    Note: place_order never actually deducts trade cost from cash (a
    separate, pre-existing gap -- StubBrokerClient tracks positions but
    never adjusts cash/equity/buying_power from trading activity), so
    this only asserts on what reset() actually needs to guarantee:
    positions/orders are cleared and cash ends up at the configured
    starting value, not that cash visibly changed beforehand."""
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
