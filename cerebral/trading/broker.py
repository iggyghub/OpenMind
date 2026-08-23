from __future__ import annotations

import keyring
from typing import Protocol, List, Optional, Literal, Dict, Any, runtime_checkable
from dataclasses import dataclass

# Public types
Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]

@dataclass
class Account:
    cash: float
    equity: float
    status: str
    buying_power: float
    day_trades_remaining: int

@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    side: str
    market_value: float
    unrealized_pl: float
    current_price: float

@dataclass
class Order:
    id: str
    symbol: str
    qty: float
    filled_qty: float
    side: str
    type: str
    status: str
    price: float = 0.0
    fees: float = 0.0


@runtime_checkable
class BrokerClient(Protocol):
    def get_account(self) -> Account: ...
    def list_positions(self) -> List[Position]: ...
    def place_order(
        self, symbol: str, qty: float, side: Side, type: OrderType,
        limit_price: Optional[float] = None,
    ) -> Order: ...
    def cancel_order(self, order_id: str) -> None: ...
    def get_order(self, order_id: str) -> Order: ...


def _get_alpaca_credentials(env: str) -> tuple[str, str]:
    """Reads alpaca_paper_key/alpaca_paper_secret or alpaca_live_key/alpaca_live_secret from keyring."""
    service = "cerebral_alpaca"
    key = keyring.get_password(service, f"alpaca_{env}_key")
    secret = keyring.get_password(service, f"alpaca_{env}_secret")
    if not key or not secret:
        raise EnvironmentError(f"Missing Alpaca credentials for env: {env}")
    return key, secret


class AlpacaBrokerClient:
    def __init__(self, env: str = "paper") -> None:
        if env not in ("paper", "live"):
            raise ValueError("env must be 'paper' or 'live'")
        self.env = env
        self._client = None
        self._connected = False

    def _connect(self) -> None:
        if self._connected:
            return
        api_key, api_secret = _get_alpaca_credentials(self.env)
        try:
            import alpaca.trading.client as trading_client
            self._client = trading_client.TradingClient(
                api_key, api_secret, paper=(self.env == "paper")
            )
        except ImportError:
            raise RuntimeError("alpaca-py is not installed. Install with: pip install alpaca-py")
        self._connected = True

    def get_account(self) -> Account:
        self._connect()
        acc = self._client.get_account()
        return Account(
            cash=acc.cash,
            equity=acc.equity,
            status=acc.status.value,
            buying_power=acc.buying_power,
            day_trades_remaining=acc.day_trades_remaining,
        )

    def list_positions(self) -> List[Position]:
        self._connect()
        positions = self._client.list_positions()
        result = []
        for p in positions:
            result.append(Position(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                side=p.side.value,
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
                current_price=float(p.current_price),
            ))
        return result

    def place_order(
        self, symbol: str, qty: float, side: Side, type: OrderType,
        limit_price: Optional[float] = None,
    ) -> Order:
        self._connect()
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import Side as AlpacaSide

        qty_val = float(qty)
        side_enum = AlpacaSide.BUY if side == "buy" else AlpacaSide.SELL

        if type == "market":
            req = MarketOrderRequest(symbol=symbol, qty=qty_val, side=side_enum, time_in_force="day")
        elif type == "limit":
            # A real limit price is required -- there is no meaningful
            # default. The prior "0.01" placeholder would have silently
            # submitted every limit order at one cent.
            if limit_price is None:
                raise ValueError("limit_price is required for a limit order")
            req = LimitOrderRequest(
                symbol=symbol, qty=qty_val, side=side_enum, time_in_force="day",
                limit_price=str(limit_price),
            )
        else:
            raise ValueError(f"Unsupported order type: {type}")

        filled_order = self._client.submit_order(req)
        return Order(
            id=filled_order.id,
            symbol=filled_order.symbol,
            qty=filled_order.qty,
            filled_qty=0.0,
            side=filled_order.side.value,
            type=filled_order.type.value,
            status=filled_order.status.value,
            price=0.0,  # Live fill price populated on status update
            fees=0.0,
        )

    def cancel_order(self, order_id: str) -> None:
        self._connect()
        self._client.cancel_order(order_id)

    def get_order(self, order_id: str) -> Order:
        self._connect()
        o = self._client.get_order_by_id(order_id)
        # Populate fill price from Alpaca's confirmed fill_avg_price
        fill_price = float(o.fill_avg_price) if o.fill_avg_price else 0.0
        return Order(
            id=o.id,
            symbol=o.symbol,
            qty=o.qty,
            filled_qty=float(o.filled_qty),
            side=o.side.value,
            type=o.type.value,
            status=o.status.value,
            price=fill_price,
            fees=float(o.fees) if o.fees else 0.0,
        )


class StubBrokerClient:
    """Test double that simulates fills, partial fills, and rejects."""
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self._orders: Dict[str, Order] = {}
        self._positions: Dict[str, Position] = {}
        self._reject_order_id: Optional[str] = None
        self._partial_fill_symbol: Optional[str] = None
        self._partial_fill_ratio: float = 1.0
        self._account = Account(
            cash=10000.0, equity=10000.0, status="ACTIVE",
            buying_power=10000.0, day_trades_remaining=3
        )

    def get_account(self) -> Account:
        return self._account

    def list_positions(self) -> List[Position]:
        return list(self._positions.values())

    def place_order(
        self, symbol: str, qty: float, side: Side, type: OrderType,
        limit_price: Optional[float] = None,
    ) -> Order:
        if self._reject_order_id and symbol == self._reject_order_id:
            raise RuntimeError(f"Rejected order for {symbol}")
        if type == "limit" and limit_price is None:
            raise ValueError("limit_price is required for a limit order")

        order_id = f"stub_{symbol}_{len(self._orders)}"
        filled_qty = 0.0
        status = "FILLED"

        if self._partial_fill_symbol == symbol:
            filled_qty = float(qty) * self._partial_fill_ratio
            status = "PARTIALLY_FILLED" if filled_qty < float(qty) else "FILLED"

        # Simulated quote/price model for test fills
        simulated_price = limit_price if limit_price else 100.0 + (abs(hash(symbol)) % 500) / 10.0

        order = Order(
            id=order_id, symbol=symbol, qty=qty,
            filled_qty=filled_qty, side=side, type=type, status=status,
            price=simulated_price,
            fees=round(qty * simulated_price * 0.001, 2),  # 0.1% simulated fee
        )
        self._orders[order_id] = order

        # Update simulated position
        if symbol in self._positions:
            pos = self._positions[symbol]
            net_qty = float(pos.qty) + (float(qty) if side == "buy" else -float(qty))
            self._positions[symbol] = Position(
                symbol=symbol, qty=net_qty, avg_entry_price=pos.avg_entry_price,
                side="buy" if net_qty > 0 else "sell",
                market_value=net_qty * pos.current_price,
                unrealized_pl=0.0, current_price=pos.current_price,
            )
        else:
            self._positions[symbol] = Position(
                symbol=symbol,
                qty=float(qty) if side == "buy" else -float(qty),
                avg_entry_price=100.0, side=side,
                market_value=simulated_price * (float(qty) if side == "buy" else -float(qty)),
                unrealized_pl=0.0, current_price=simulated_price,
            )
        return order

    def cancel_order(self, order_id: str) -> None:
        # Order is a plain (non-frozen) dataclass -- mutate in place rather
        # than Order(**self._orders[order_id], ...), which tried to ** an
        # Order instance itself instead of a dict and always raised TypeError.
        if order_id in self._orders:
            self._orders[order_id].status = "CANCELED"

    def get_order(self, order_id: str) -> Order:
        return self._orders[order_id]

    def reject_next_order_for(self, symbol: str) -> None:
        self._reject_order_id = symbol

    def enable_partial_fills_for(self, symbol: str, ratio: float) -> None:
        self._partial_fill_symbol = symbol
        self._partial_fill_ratio = ratio
