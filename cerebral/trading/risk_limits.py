from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from cerebral.trading.alerts import AlertDispatcher, StructuredAlert

logger = logging.getLogger(__name__)

@dataclass
class RiskConfig:
    max_per_trade_risk_pct: float = 2.0
    max_daily_loss_pct: float = 6.0
    max_concurrent_positions: int = 10

@dataclass
class RiskEvaluation:
    allowed: bool
    reason: Optional[str] = None
    blocked_by: Optional[str] = None

class RiskManager:
    """Evaluates order risk against configurable limits.
    
    Integrates with cerebral.settings.SettingsStore for live config,
    but falls back to RiskConfig defaults. Emits structured alerts on breaches.
    """
    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        settings_store: Optional[Any] = None,
        alert_dispatcher: Optional[AlertDispatcher] = None,
    ) -> None:
        # An explicit RiskConfig always wins over settings_store -- the two
        # were being conflated (settings_store.get(...) was called on
        # whatever object was passed positionally, including a RiskConfig
        # itself, which has no .get()). Distinct params now.
        self._explicit_config = config
        self._settings = settings_store
        self._alert_dispatcher = alert_dispatcher
        self._config = self._load_config()
        self._daily_loss_accrued: float = 0.0

    def _load_config(self) -> RiskConfig:
        if self._explicit_config is not None:
            return self._explicit_config
        if self._settings:
            return RiskConfig(
                max_per_trade_risk_pct=self._settings.get("max_per_trade_risk_pct") or 2.0,
                max_daily_loss_pct=self._settings.get("max_daily_loss_pct") or 6.0,
                max_concurrent_positions=self._settings.get("max_concurrent_positions") or 10,
            )
        return RiskConfig()

    def check_order(
        self,
        account_equity: float,
        current_positions_count: int,
        current_daily_loss: float,
        trade_value: float,
        symbol: str,
        qty: float,
    ) -> RiskEvaluation:
        # Refresh config in case settings changed
        self._config = self._load_config()

        # 1. Max concurrent positions
        if current_positions_count >= self._config.max_concurrent_positions:
            reason = f"Max concurrent positions ({self._config.max_concurrent_positions}) reached"
            logger.warning(f"[risk] Blocked order for {symbol}: {reason}")
            self._emit_alert("warning", "order_blocked", reason, {"blocked_by": "max_concurrent_positions"})
            return RiskEvaluation(allowed=False, reason=reason, blocked_by="max_concurrent_positions")

        # 2. Per-trade risk (max loss = % of account)
        max_per_trade_loss = account_equity * (self._config.max_per_trade_risk_pct / 100.0)
        if trade_value > max_per_trade_loss:
            reason = f"Per-trade risk {trade_value:.2f} exceeds {self._config.max_per_trade_risk_pct}% of account ({max_per_trade_loss:.2f})"
            logger.warning(f"[risk] Blocked order for {symbol}: {reason}")
            self._emit_alert("warning", "order_blocked", reason, {"blocked_by": "per_trade_risk"})
            return RiskEvaluation(allowed=False, reason=reason, blocked_by="per_trade_risk")

        # 3. Daily loss limit
        max_daily_loss = account_equity * (self._config.max_daily_loss_pct / 100.0)
        if current_daily_loss >= max_daily_loss:
            reason = f"Daily loss limit {current_daily_loss:.2f} reached {self._config.max_daily_loss_pct}% of account ({max_daily_loss:.2f})"
            logger.warning(f"[risk] Blocked new orders: {reason}")
            self._emit_alert("critical", "order_blocked", reason, {"blocked_by": "daily_loss_limit"})
            return RiskEvaluation(allowed=False, reason=reason, blocked_by="daily_loss_limit")

        return RiskEvaluation(allowed=True)

    def check_correlation_limit(
        self,
        new_symbol: str,
        existing_symbols: List[str],
        correlation_matrix: Dict[str, Dict[str, float]],
        max_correlation: float = 0.7,
    ) -> RiskEvaluation:
        """Blocks entry if new position correlates > threshold with existing positions."""
        for existing in existing_symbols:
            corr = correlation_matrix.get(new_symbol, {}).get(existing, 0.0)
            if corr > max_correlation:
                reason = f"Correlation {corr:.2f} between {new_symbol} and {existing} exceeds limit {max_correlation}"
                logger.warning(f"[risk] Blocked {new_symbol}: {reason}")
                self._emit_alert("warning", "correlation_block", reason, {"blocked_by": "correlation", "pair": f"{new_symbol}-{existing}"})
                return RiskEvaluation(allowed=False, reason=reason, blocked_by="correlation")
        return RiskEvaluation(allowed=True)

    def check_sentiment(self, symbol: str, sentiment_label: Optional[str]) -> RiskEvaluation:
        """Blocks a new open only on a market-wide BEARISH reading.
        `None` (gate off / no reading yet) and NEUTRAL/BULLISH all pass --
        this is deliberately a coarse market-wide gate (decision: general
        market feeds, not per-symbol), not a per-symbol veto."""
        if sentiment_label == "BEARISH":
            reason = f"Market sentiment is BEARISH -- blocking new open for {symbol}"
            logger.warning(f"[risk] Blocked {symbol}: {reason}")
            self._emit_alert("warning", "sentiment_block", reason, {"blocked_by": "market_sentiment", "symbol": symbol})
            return RiskEvaluation(allowed=False, reason=reason, blocked_by="market_sentiment")
        return RiskEvaluation(allowed=True)

    def check_symbol_claim(self, symbol: str, claimed_this_tick: set) -> RiskEvaluation:
        """Scoped-down portfolio-manager arbitration (2026-08-31):
        first-come-first-served within one dispatch pass, not confidence-
        ranked reconciliation -- blocks a second strategy's open on a
        symbol another strategy already opened THIS tick. Added after a
        real collision (`insufficient qty available for order`, UBER) --
        the real Alpaca account has one position per symbol, nothing
        stopped two strategies from both trying to claim it the same
        tick. `claimed_this_tick` is the caller's set, mutated by the
        caller on a successful open -- this method only reads it."""
        if symbol in claimed_this_tick:
            reason = f"{symbol} already claimed by another strategy this tick"
            logger.warning(f"[risk] Blocked {symbol}: {reason}")
            self._emit_alert("warning", "symbol_claim_block", reason, {"blocked_by": "symbol_claimed", "symbol": symbol})
            return RiskEvaluation(allowed=False, reason=reason, blocked_by="symbol_claimed")
        return RiskEvaluation(allowed=True)

    def record_daily_loss(self, loss: float) -> None:
        if loss > 0:
            self._daily_loss_accrued += loss

    def _emit_alert(self, severity: str, event_type: str, message: str, context: Optional[dict] = None) -> None:
        if self._alert_dispatcher:
            self._alert_dispatcher.emit(StructuredAlert(
                severity=severity, event_type=event_type, message=message, context=context or {}
            ))
