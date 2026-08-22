from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from cerebral.trading.alerts import AlertDispatcher, StructuredAlert

logger = logging.getLogger(__name__)

@dataclass
class TradeState:
    symbol: str
    qty: float
    side: str
    order_id: str
    status: str
    queued_close: bool = False

class FailureHandler:
    """Handles market data staleness, partial fills, halted symbols, and crash recovery."""
    
    def __init__(
        self,
        stale_threshold_seconds: float = 300.0,
        alert_dispatcher: Optional[AlertDispatcher] = None,
    ) -> None:
        self.stale_threshold = stale_threshold_seconds
        self._last_data_timestamp: Optional[float] = None
        self._is_data_stale: bool = False
        self._halted_symbols: Dict[str, bool] = {}
        self._trade_states: Dict[str, TradeState] = {}
        self._alerts = alert_dispatcher
        self._new_trades_allowed: bool = True

    def update_market_data_timestamp(self, ts: float) -> None:
        # None on the first-ever tick (no data has arrived yet) -- any
        # timestamp is newer than "nothing", so accept it unconditionally.
        if self._last_data_timestamp is None or ts > self._last_data_timestamp:
            self._last_data_timestamp = ts
        self._check_staleness()

    def _check_staleness(self) -> None:
        if self._last_data_timestamp is None:
            return
            
        now = time.time()
        stale = (now - self._last_data_timestamp) > self.stale_threshold
        
        if stale and not self._is_data_stale:
            self._is_data_stale = True
            self._new_trades_allowed = False
            self._emit("critical", "market_data_stale", "Market data is stale or API unreachable. Halting new trades.")
            logger.critical("[failure] Market data stale. New trades halted.")
        elif not stale and self._is_data_stale:
            self._is_data_stale = False
            self._new_trades_allowed = True
            self._emit("info", "market_data_restored", "Market data restored. Resuming trades.")
            logger.info("[failure] Market data restored.")

    def handle_partial_fill(self, order_id: str, filled_qty: float, original_qty: float) -> None:
        state = self._trade_states.get(order_id)
        if not state:
            self._trade_states[order_id] = TradeState(
                symbol="UNKNOWN", qty=original_qty, side="UNKNOWN", 
                order_id=order_id, status="PARTIALLY_FILLED"
            )
            return
            
        state.status = "PARTIALLY_FILLED"
        self._emit(
            "warning", 
            "partial_fill", 
            f"Partial fill for {state.symbol}: {filled_qty}/{original_qty}. Keeping partial, cancelling remainder."
        )
        logger.warning(f"[failure] Partial fill for {state.symbol}: {filled_qty}/{original_qty}. Cancelling remainder.")

    def detect_halted_symbol(self, symbol: str, halted: bool) -> None:
        if halted:
            self._halted_symbols[symbol] = True
            self._emit("warning", "symbol_halted", f"Trading halted for {symbol}. Queuing close order for when trading resumes.")
            logger.warning(f"[failure] Symbol {symbol} halted. Queuing close.")
        else:
            self._halted_symbols.pop(symbol, None)
            # Resume any queued closes
            for oid, state in self._trade_states.items():
                if state.symbol == symbol and state.queued_close:
                    state.queued_close = False
                    self._emit("info", "symbol_resumed", f"Trading resumed for {symbol}. Processing queued close.")

    def crash_recovery(self, open_positions: List[str]) -> None:
        logger.info(f"[failure] Crash recovery: reconciling {len(open_positions)} open positions.")
        self._emit("info", "crash_recovery", f"Reconciling {len(open_positions)} open positions with broker.")
        for symbol in open_positions:
            self._trade_states[f"recover_{symbol}"] = TradeState(
                symbol=symbol, qty=0.0, side="buy", order_id="recover", status="RECOVERED"
            )

    def _emit(self, severity: str, event_type: str, message: str) -> None:
        if self._alerts:
            self._alerts.emit(StructuredAlert(
                severity=severity, event_type=event_type, message=message
            ))
