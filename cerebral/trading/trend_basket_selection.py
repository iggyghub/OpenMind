from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd


@dataclass
class BreadthResult:
    breadth: float | None  # None = too few symbols measured to be a real reading
    candidates: list[str]
    above_ma: list[str]
    below_ma: list[str]


def compute_breadth(
    candidates: list[str],
    fetch_bars: Callable[[str, int, str], pd.DataFrame],
    min_history: int = 50,
    min_measured: int = 20,
) -> BreadthResult:
    """Compute the share of candidates trading above their own 50-day MA.

    Symbols with insufficient history are excluded from both numerator and denominator.
    MA is computed using only historical closes (prior day's close), avoiding look-ahead.
    Fewer than `min_measured` symbols measured (empty pool, failed fetches) gives breadth=None,
    not a number: 2026-09-24, an empty overnight pool read 0.0%, and RisingEdgeGate's
    once-per-day cache locked that in as the whole day's reading.
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
    breadth = len(above) / total if total >= max(1, min_measured) else None

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
    """Rank candidates by `horizon`-day return x `horizon`-day daily-return stdev, descending --
    ADR-0038's locked momentum x volatility score (pure momentum until 2026-09-24).

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
            vol = float(closes.pct_change().std())
            if vol != vol:  # NaN: too few bars to measure volatility
                continue
            scores[sym] = ret * vol
        except Exception:
            continue

    return [sym for sym, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]


class RisingEdgeGate:
    """Caches one breadth reading per calendar day and fires on a rising edge above threshold.

    With `sustain` set (2026-09-24, trigger/sustain), `refresh` instead returns whether the regime
    is ACTIVE: it turns on when breadth crosses above `threshold` and stays on while breadth stays
    above `sustain`, so empty basket slots get refilled for as long as the uptrend holds -- not
    only on the crossing day. `sustain=None` keeps the original edge-only behavior.

    Mirrors `MarketTrendGate` shape with fail-open-on-fetch-error behavior.
    """

    _STATE_KEY = "trend_basket_gate_state"

    def __init__(self, threshold: float = 0.6, store=None, sustain: float | None = None) -> None:
        self.threshold = threshold
        self.sustain = sustain
        self._last_date: date | None = None
        self._last_breadth: float | None = None
        self._current_edge: bool = True  # Fail-open by default
        self._active: bool = False
        # Persisted (2026-09-23) so a restart remembers yesterday's reading -- otherwise the first
        # reading after boot has no "was above" and a day already above threshold fires a false edge.
        self._store = store
        state = (store.get(self._STATE_KEY) if store is not None else None) or {}
        if state.get("date"):
            self._last_date = date.fromisoformat(state["date"])
            self._last_breadth = state.get("breadth")
            self._current_edge = bool(state.get("edge"))
            self._active = bool(state.get("active"))

    def refresh(self, breadth: float | None, current_date: date) -> bool:
        """Update state with a new breadth reading. Returns True if today is a rising edge (or,
        with `sustain` set, if the regime is active today)."""
        if breadth is None:
            return self._current_edge if self.sustain is None else self._active

        try:
            if current_date != self._last_date:
                is_above = breadth > self.threshold
                was_above = self._last_breadth is not None and self._last_breadth > self.threshold
                self._current_edge = is_above and not was_above
                if self.sustain is not None:
                    self._active = self._current_edge or (self._active and breadth > self.sustain)

                self._last_date = current_date
                self._last_breadth = breadth
                if self._store is not None:
                    self._store.set(self._STATE_KEY, {
                        "date": current_date.isoformat(), "breadth": breadth,
                        "edge": self._current_edge, "active": self._active,
                    })
        except Exception:
            pass  # Fail-open: retain current edge state on error

        return self._current_edge if self.sustain is None else self._active

    @property
    def current(self) -> bool:
        return self._current_edge

    @property
    def last_reading(self) -> dict:
        """Status snapshot for display (2026-09-23, trend-basket UI visibility) -- today's
        breadth reading, the thresholds it's compared against, whether today counted as a rising
        edge / an active refill day, and when it was last checked. `breadth` only updates once per
        calendar day by design (see `refresh` -- later same-day calls return the cached decision
        without recomputing), so this is "today's reading," not a live-every-tick value; a caller
        rendering this should say so rather than imply it updates every scheduler tick."""
        return {
            "breadth": self._last_breadth,
            "threshold": self.threshold,
            "sustain": self.sustain,
            "is_rising_edge": self._current_edge,
            "active": self._active,
            "last_checked": self._last_date.isoformat() if self._last_date else None,
        }
