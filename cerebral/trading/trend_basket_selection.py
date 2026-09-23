from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd


@dataclass
class BreadthResult:
    breadth: float
    candidates: list[str]
    above_ma: list[str]
    below_ma: list[str]


def compute_breadth(
    candidates: list[str],
    fetch_bars: Callable[[str, int, str], pd.DataFrame],
    min_history: int = 50,
) -> BreadthResult:
    """Compute the share of candidates trading above their own 50-day MA.
    
    Symbols with insufficient history are excluded from both numerator and denominator.
    MA is computed using only historical closes (prior day's close), avoiding look-ahead.
    """
    above: list[str] = []
    below: list[str] = []

    for sym in candidates:
        try:
            bars = fetch_bars(sym, min_history + 1, "d")
            if bars.empty or len(bars) < min_history:
                continue

            closes = bars["close"].astype(float)
            historical_closes = closes.iloc[:-1]
            if len(historical_closes) < min_history:
                continue

            ma_50 = historical_closes.rolling(min_history).mean().iloc[-1]
            if closes.iloc[-1] > ma_50:
                above.append(sym)
            else:
                below.append(sym)
        except Exception:
            continue

    total = len(above) + len(below)
    breadth = len(above) / total if total > 0 else 0.0

    return BreadthResult(
        breadth=breadth,
        candidates=candidates,
        above_ma=above,
        below_ma=below,
    )


def rank_by_momentum(
    candidates: list[str],
    fetch_bars: Callable[[str, int, str], pd.DataFrame],
    horizon: int = 20,
) -> list[str]:
    """Rank candidates by `horizon`-day return, descending.
    
    Expects `candidates` to already be filtered by liquidity/price screens 
    (e.g., via `discovery.rank_for_day_trading`).
    """
    scores: dict[str, float] = {}
    for sym in candidates:
        try:
            bars = fetch_bars(sym, horizon + 1, "d")
            if bars.empty or len(bars) < horizon + 1:
                continue

            closes = bars["close"].astype(float)
            if closes.iloc[0] == 0:
                continue

            ret = (closes.iloc[-1] / closes.iloc[0]) - 1.0
            scores[sym] = ret
        except Exception:
            continue

    return [sym for sym, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]


class RisingEdgeGate:
    """Caches one breadth reading per calendar day and fires on a rising edge above threshold.
    
    Mirrors `MarketTrendGate` shape with fail-open-on-fetch-error behavior.
    """

    def __init__(self, threshold: float = 0.6) -> None:
        self.threshold = threshold
        self._last_date: date | None = None
        self._last_breadth: float | None = None
        self._current_edge: bool = True  # Fail-open by default

    def refresh(self, breadth: float | None, current_date: date) -> bool:
        """Update state with a new breadth reading. Returns True if today is a rising edge."""
        if breadth is None:
            return self._current_edge

        try:
            if current_date != self._last_date:
                is_above = breadth > self.threshold
                was_above = self._last_breadth is not None and self._last_breadth > self.threshold

                if is_above and not was_above:
                    self._current_edge = True
                else:
                    self._current_edge = False

                self._last_date = current_date
                self._last_breadth = breadth
        except Exception:
            pass  # Fail-open: retain current edge state on error

        return self._current_edge

    @property
    def current(self) -> bool:
        return self._current_edge

    @property
    def last_reading(self) -> dict:
        """Status snapshot for display (2026-09-23, trend-basket UI visibility) -- today's
        breadth reading, the threshold it's compared against, whether today counted as a rising
        edge, and when it was last checked. `breadth` only updates once per calendar day by
        design (see `refresh` -- later same-day calls return the cached decision without
        recomputing), so this is "today's reading," not a live-every-tick value; a caller
        rendering this should say so rather than imply it updates every scheduler tick."""
        return {
            "breadth": self._last_breadth,
            "threshold": self.threshold,
            "is_rising_edge": self._current_edge,
            "last_checked": self._last_date.isoformat() if self._last_date else None,
        }
