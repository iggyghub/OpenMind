from __future__ import annotations

import keyring
from typing import Protocol, List, Optional, Literal, Dict, Any, runtime_checkable, Tuple
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
    def list_positions(self, strategy_id: Optional[str] = None) -> List[Position]: ...
    def place_order(
        self, symbol: str, qty: float, side: Side, type: OrderType,
        limit_price: Optional[float] = None,
        strategy_id: Optional[str] = None,
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
        # In-memory ledger for per-(strategy_id, symbol) position isolation.
        # Alpaca itself has no per-strategy position concept, so this lives
        # here, mirroring StubBrokerClient's own ledger. Resets on restart --
        # same accepted limitation the stub already has.
        self._positions: Dict[Tuple[str, str], Position] = {}

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

    def preflight(self) -> tuple[bool, str]:
        """Checks the live path can actually work before ever routing an
        order to it: package installed, credentials present for `env`,
        account reachable and ACTIVE. Never raises -- a caller always gets
        (False, reason) instead of an exception, so it can fall back to
        paper instead of error-looping (S21/#874)."""
        try:
            import alpaca.trading.client  # noqa: F401
        except ImportError:
            return False, "alpaca-py is not installed"
        try:
            _get_alpaca_credentials(self.env)
        except EnvironmentError as exc:
            return False, str(exc)
        try:
            acc = self.get_account()
        except Exception as exc:
            return False, f"account unreachable: {exc}"
        if acc.status != "ACTIVE":
            return False, f"account status is {acc.status!r}, not ACTIVE"
        return True, "ok"

    def get_account(self) -> Account:
        self._connect()
        acc = self._client.get_account()
        # Pre-existing bug: alpaca-py's TradeAccount has no
        # day_trades_remaining attribute (this always raised) -- the real
        # field is daytrade_count, a used-count toward the PDT limit, not a
        # remaining count. ponytail: assumes the standard 3-per-5-trading-
        # day PDT limit (not the >$25k-equity exemption); nothing in this
        # codebase gates on day_trades_remaining today, so this is display
        # only -- revisit if a risk check ever starts reading it.
        # Pre-existing bug: cash/equity/buying_power are typed str on
        # TradeAccount (Alpaca's REST API returns account financials as
        # strings), not float -- passed through unconverted, this made
        # every arithmetic use downstream (e.g. RiskManager.check_order's
        # account_equity * pct) raise "can't multiply sequence by
        # non-int of type 'float'". Live-observed against the real paper
        # account on the first day trading_paper_enabled was turned on.
        return Account(
            cash=float(acc.cash),
            equity=float(acc.equity),
            status=acc.status.value,
            buying_power=float(acc.buying_power),
            day_trades_remaining=max(0, 3 - (acc.daytrade_count or 0)),
        )

    def list_positions(self, strategy_id: Optional[str] = None) -> List[Position]:
        self._connect()
        if strategy_id is not None:
            # Local ledger is in-memory only and resets on process restart.
            # That's the same accepted limitation StubBrokerClient has.
            return [p for (sid, sym), p in self._positions.items() if sid == strategy_id]
        
        # Pre-existing bug: TradingClient has no list_positions method --
        # this always raised AttributeError. Real name: get_all_positions.
        positions = self._client.get_all_positions()
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
        strategy_id: Optional[str] = None,
    ) -> Order:
        self._connect()
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        # Pre-existing bug: the installed alpaca-py names this enum
        # OrderSide, not Side -- this import has always raised ImportError,
        # so place_order has never actually worked against a real account.
        from alpaca.trading.enums import OrderSide as AlpacaSide

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

        submitted = self._client.submit_order(req)
        # Alpaca fills market orders asynchronously -- the object submit_order
        # returns is a "new"/"accepted" snapshot, never a fill. A caller
        # (run_strategy_tick) reads status/filled_qty/price off the object
        # place_order returns and does not itself poll get_order, so without
        # this the paper-trade dispatcher would treat every real order as
        # unfilled and never record it. Regular-hours market orders on Alpaca
        # paper fill in well under a second in practice.
        order = self._poll_until_terminal(submitted.id)
        
        # Mirror StubBrokerClient's per-(strategy_id, symbol) ledger.
        key = (strategy_id, symbol)
        prev = self._positions.get(key)
        prev_qty = float(prev.qty) if prev else 0.0
        filled = float(order.filled_qty) or float(order.qty)
        net_qty = prev_qty + (filled if side == "buy" else -filled)
        if abs(net_qty) < 1e-9:
            self._positions.pop(key, None)
        else:
            if prev is None or prev_qty == 0.0 or (prev_qty > 0) != (net_qty > 0):
                avg_entry = float(order.price)
            elif abs(net_qty) > abs(prev_qty):
                avg_entry = (abs(prev_qty) * prev.avg_entry_price + filled * float(order.price)) / abs(net_qty)
            else:
                avg_entry = prev.avg_entry_price
            self._positions[key] = Position(
                symbol=symbol, qty=net_qty, avg_entry_price=avg_entry,
                side="buy" if net_qty > 0 else "sell",
                market_value=net_qty * float(order.price),
                unrealized_pl=(float(order.price) - avg_entry) * net_qty,
                current_price=float(order.price),
            )
        return order

    _TERMINAL_ORDER_STATUSES = {
        "FILLED", "PARTIALLY_FILLED", "CANCELED", "REJECTED", "EXPIRED", "DONE_FOR_DAY",
    }

    def _poll_until_terminal(self, order_id: str, timeout: float = 10.0, interval: float = 0.5) -> Order:
        # ponytail: fixed poll ceiling, not a websocket fill stream -- bump
        # timeout/interval if paper fills ever run slower than this during
        # regular market hours.
        import time
        deadline = time.monotonic() + timeout
        order = self.get_order(order_id)
        while order.status not in self._TERMINAL_ORDER_STATUSES and time.monotonic() < deadline:
            time.sleep(interval)
            order = self.get_order(order_id)
        return order

    def cancel_order(self, order_id: str) -> None:
        self._connect()
        # Pre-existing bug: TradingClient has no cancel_order method --
        # this always raised AttributeError. Real name: cancel_order_by_id.
        self._client.cancel_order_by_id(order_id)

    def get_order(self, order_id: str) -> Order:
        self._connect()
        o = self._client.get_order_by_id(order_id)
        # Pre-existing bugs, both raised AttributeError (caught upstream by
        # _run_paper_strategy's broad except, so silent until traced): the
        # field is filled_avg_price, not fill_avg_price; and Order has no
        # fees field at all -- Alpaca is commission-free, there's nothing
        # to read (matches StubBrokerClient's own "no fee to simulate").
        fill_price = float(o.filled_avg_price) if o.filled_avg_price else 0.0
        return Order(
            id=str(o.id),
            symbol=o.symbol,
            qty=float(o.qty),
            filled_qty=float(o.filled_qty),
            side=o.side.value,
            type=o.type.value,
            # Uppercased to match StubBrokerClient's contract ("FILLED", not
            # alpaca-py's lowercase "filled") -- every caller (run_strategy_
            # tick's status check, the broker Protocol's implicit contract)
            # compares against the Stub's casing.
            status=o.status.value.upper(),
            price=fill_price,
            fees=0.0,
        )


class StubBrokerClient:
    """Test double that simulates fills, partial fills, and rejects."""
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self._orders: Dict[str, Order] = {}
        self._positions: Dict[tuple[str, str], Position] = {}  # (strategy_id, symbol)
        self._reject_order_id: Optional[str] = None
        self._partial_fill_symbol: Optional[str] = None
        self._partial_fill_ratio: float = 1.0
        # S34 (#901): configurable simulated starting capital -- was
        # hardcoded to 10000.0 in all three fields, ignoring self.config.
        starting_cash = float(self.config.get("starting_cash", 10000.0))
        self._account = Account(
            cash=starting_cash, equity=starting_cash, status="ACTIVE",
            buying_power=starting_cash, day_trades_remaining=3
        )

    def reset(self) -> None:
        """Clears every simulated position/order and resets the account
        back to its configured starting capital -- the paper-trading
        equivalent of "restart the simulation." Does not touch
        self.config, so a later reset uses the same starting_cash as the
        original construction."""
        self._orders = {}
        self._positions = {}
        starting_cash = float(self.config.get("starting_cash", 10000.0))
        self._account = Account(
            cash=starting_cash, equity=starting_cash, status="ACTIVE",
            buying_power=starting_cash, day_trades_remaining=3
        )

    def get_account(self) -> Account:
        return self._account

    def _simulated_price(self, symbol: str) -> float:
        """Deterministic-per-process pseudo-quote. Its own method so a test
        can script a price path (needed to exercise a non-zero realized P&L
        -- the default is constant per symbol, so entry always equals exit)."""
        return 100.0 + (abs(hash(symbol)) % 500) / 10.0

    def list_positions(self, strategy_id: Optional[str] = None) -> List[Position]:
        """`strategy_id` given: only that strategy's own rows. Omitted
        (default): the aggregate whole-book view, one Position per symbol
        summing qty across every strategy holding it -- what RiskManager's
        concurrent-position-count and correlation checks want (#961: the
        risk gates are deliberately one shared account-level budget, not
        per-strategy)."""
        if strategy_id is not None:
            return [p for (sid, sym), p in self._positions.items() if sid == strategy_id]
        by_symbol: Dict[str, List[Position]] = {}
        for (sid, sym), p in self._positions.items():
            by_symbol.setdefault(sym, []).append(p)
        aggregated: List[Position] = []
        for sym, rows in by_symbol.items():
            net_qty = sum(r.qty for r in rows)
            if abs(net_qty) < 1e-9:
                continue
            avg_entry = (
                sum(abs(r.qty) * r.avg_entry_price for r in rows)
                / sum(abs(r.qty) for r in rows)
            )
            current_price = rows[0].current_price  # deterministic per symbol
            aggregated.append(Position(
                symbol=sym, qty=net_qty, avg_entry_price=avg_entry,
                side="buy" if net_qty > 0 else "sell",
                market_value=net_qty * current_price,
                unrealized_pl=(current_price - avg_entry) * net_qty,
                current_price=current_price,
            ))
        return aggregated

    def place_order(
        self, symbol: str, qty: float, side: Side, type: OrderType,
        limit_price: Optional[float] = None,
        strategy_id: Optional[str] = None,
    ) -> Order:
        if self._reject_order_id and symbol == self._reject_order_id:
            raise RuntimeError(f"Rejected order for {symbol}")
        if type == "limit" and limit_price is None:
            raise ValueError("limit_price is required for a limit order")

        order_id = f"stub_{symbol}_{len(self._orders)}"
        # A FILLED order whose filled_qty is 0.0 is self-contradictory -- it
        # used to report exactly that, so every caller reading filled_qty saw
        # nothing filled on a full fill.
        filled_qty = float(qty)
        status = "FILLED"

        if self._partial_fill_symbol == symbol:
            filled_qty = float(qty) * self._partial_fill_ratio
            status = "PARTIALLY_FILLED" if filled_qty < float(qty) else "FILLED"

        # Simulated quote/price model for test fills
        simulated_price = limit_price if limit_price else self._simulated_price(symbol)

        order = Order(
            id=order_id, symbol=symbol, qty=qty,
            filled_qty=filled_qty, side=side, type=type, status=status,
            price=simulated_price,
            fees=0.0,  # commission-free (Alpaca), no fee to simulate
        )
        self._orders[order_id] = order

        # Update the simulated position. This is the only source of truth a
        # caller has for "am I in this name, and at what entry?" (TRADING.md:
        # numbers from the broker, never re-derived), so the entry price has
        # to be the price this stub actually filled at -- it used to be a
        # hardcoded 100.0 while the fill happened at simulated_price, making
        # any P&L computed from it wrong by the whole hash-derived offset.
        # Keyed by (strategy_id, symbol), not symbol alone (#961) -- two
        # strategies trading the same symbol used to read/write the SAME
        # row, so one strategy's close could silently net against another
        # strategy's open. strategy_id=None (a caller that doesn't pass it)
        # behaves exactly like the old symbol-only keying.
        key = (strategy_id, symbol)
        prev = self._positions.get(key)
        prev_qty = float(prev.qty) if prev else 0.0
        net_qty = prev_qty + (float(qty) if side == "buy" else -float(qty))

        if abs(net_qty) < 1e-9:
            # Flat: drop the row rather than leave a qty=0 ghost every caller
            # then has to special-case.
            self._positions.pop(key, None)
        else:
            if prev is None or prev_qty == 0.0 or (prev_qty > 0) != (net_qty > 0):
                avg_entry = simulated_price          # opening, or flipping through flat
            elif abs(net_qty) > abs(prev_qty):
                avg_entry = (                         # adding: size-weighted average
                    abs(prev_qty) * prev.avg_entry_price + float(qty) * simulated_price
                ) / abs(net_qty)
            else:
                avg_entry = prev.avg_entry_price      # partial reduction keeps the entry
            self._positions[key] = Position(
                symbol=symbol, qty=net_qty, avg_entry_price=avg_entry,
                side="buy" if net_qty > 0 else "sell",
                market_value=net_qty * simulated_price,
                unrealized_pl=(simulated_price - avg_entry) * net_qty,
                current_price=simulated_price,
            )

        # #929: cash/equity/buying_power used to stay frozen at starting
        # capital forever -- a buy/sell never touched them. No margin
        # modeling (this is the paper-simulation fallback, not the real
        # Alpaca account): buying_power just mirrors cash.
        trade_cost = filled_qty * simulated_price
        self._account.cash += -trade_cost if side == "buy" else trade_cost
        self._account.equity = self._account.cash + sum(
            p.market_value for p in self._positions.values()
        )
        self._account.buying_power = self._account.cash
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


class AlpacaMarketDataClient:
    """Alpaca historical market data client. Uses the same credentials as AlpacaBrokerClient
    to keep backtest/live data vendor aligned (decision #39). Falls back to yfinance in
    cerebral.trading_data if this is unavailable or misconfigured."""
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
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockBarsRequest
            self._client = StockHistoricalDataClient(api_key, api_secret)
            self._request_cls = StockBarsRequest
        except ImportError:
            raise RuntimeError("alpaca-py is not installed. Install with: pip install alpaca-py")
        self._connected = True

    def get_bars(self, symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
        self._connect()
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from datetime import datetime

        # Map yfinance-compatible interval strings to a real Alpaca TimeFrame.
        # TimeFrame.Minute/.Hour are themselves fixed 1-unit constants (e.g.
        # TimeFrame.Minute == "1Min") -- using them directly for "5m"/"30m"/
        # "4h" would silently request 1-minute/1-hour bars regardless of what
        # interval was actually asked for. TimeFrame(amount, unit) is the
        # real constructor for anything other than 1 unit.
        tf_map = {
            "1m": (1, TimeFrameUnit.Minute),
            "5m": (5, TimeFrameUnit.Minute),
            "15m": (15, TimeFrameUnit.Minute),
            "30m": (30, TimeFrameUnit.Minute),
            "1h": (1, TimeFrameUnit.Hour),
            "4h": (4, TimeFrameUnit.Hour),
            "1d": (1, TimeFrameUnit.Day),
            "1w": (1, TimeFrameUnit.Week),
            "1M": (1, TimeFrameUnit.Month),
        }
        amount, unit = tf_map.get(interval, (1, TimeFrameUnit.Day))
        timeframe = TimeFrame(amount, unit)

        start_dt = datetime.fromisoformat(start) if isinstance(start, str) else start
        end_dt = datetime.fromisoformat(end) if isinstance(end, str) else end

        req = self._request_cls(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start_dt,
            end=end_dt,
        )
        bars = self._client.get_stock_bars(req)
        df = bars.df

        required_cols = ["open", "high", "low", "close", "volume"]
        if not all(c in df.columns for c in required_cols):
            raise ValueError(f"Missing columns in Alpaca response for {symbol}")

        df = df[required_cols]
        # Pre-existing bug: get_stock_bars always returns a MultiIndex
        # (symbol, timestamp), even for one symbol -- the df.index.name =
        # "Date" below silently no-ops on a MultiIndex (nothing raises,
        # but the index keeps its (symbol, timestamp) shape), so every
        # caller relying on fetch_ohlcv's documented contract (a single
        # DatetimeIndex named "Date", matching yfinance's shape) got a
        # raw MultiIndex instead. Drop the symbol level to match it.
        if df.index.nlevels > 1:
            df = df.droplevel("symbol")
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        df = df.sort_index()
        df.index.name = "Date"
        df = df.dropna(how="all")
        return df
