"""Purely informational daily market-trend reading for the tray dashboard.

Not a risk gate -- unlike sentiment.py's MarketSentimentGate (which blocks
new opens on a BEARISH reading), this only answers "how is the broad
market doing today" for display. Sourced from a benchmark ETF's own daily
bar via trading_data.fetch_ohlcv (already cached there), pure price math,
no LLM call needed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

FetchOhlcvFn = Callable[[str, str, str, str], Any]

# ponytail: a flat +/-0.3% band around zero avoids flapping UP/DOWN on
# sub-noise moves; not calibrated against real intraday volatility, revisit
# if the band ever looks visibly wrong on a real trading day.
_UP_THRESHOLD = 0.3
_DOWN_THRESHOLD = -0.3


@dataclass
class MarketTrendReading:
    label: str = "FLAT"  # UP / DOWN / FLAT
    pct_change: float = 0.0
    symbol: str = "SPY"
    updated_at: Optional[datetime] = field(default=None)


def _label_for(pct_change: float) -> str:
    if pct_change >= _UP_THRESHOLD:
        return "UP"
    if pct_change <= _DOWN_THRESHOLD:
        return "DOWN"
    return "FLAT"


class MarketTrendGate:
    """Caches one reading per calendar day -- refresh() is a no-op once a
    reading exists for today, same "cheap enough to call every broadcast
    tick" posture as sentiment.py's gates use their own TTL for."""

    def __init__(self, symbol: str = "SPY") -> None:
        self._symbol = symbol
        self._reading = MarketTrendReading(symbol=symbol)

    @property
    def current(self) -> MarketTrendReading:
        return self._reading

    def refresh(self, fetch_ohlcv_fn: FetchOhlcvFn) -> MarketTrendReading:
        """Fails open on any fetch error -- a bad/slow data pull just means
        the dashboard keeps showing the last-known reading, same
        conservative-continue philosophy as the rest of cerebral/trading."""
        now = datetime.now(timezone.utc)
        if self._reading.updated_at is not None and self._reading.updated_at.date() == now.date():
            return self._reading
        try:
            end = now.strftime("%Y-%m-%d")
            start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            df = fetch_ohlcv_fn(self._symbol, start, end, "1d")
            closes = df["Close"]
            if len(closes) < 2:
                return self._reading
            last_close = float(closes.iloc[-1])
            prev_close = float(closes.iloc[-2])
            pct_change = ((last_close - prev_close) / prev_close) * 100.0 if prev_close else 0.0
        except Exception:
            logger.warning("[market_trend] fetch failed, keeping last reading", exc_info=True)
            return self._reading
        self._reading = MarketTrendReading(
            label=_label_for(pct_change), pct_change=pct_change,
            symbol=self._symbol, updated_at=now,
        )
        logger.info("[market_trend] refreshed: %s %s %.2f%%", self._symbol, self._reading.label, pct_change)
        return self._reading
