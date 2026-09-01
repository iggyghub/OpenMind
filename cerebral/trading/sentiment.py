"""Sentiment gates: market-wide (RSS-sourced) and per-symbol (web-search
sourced).

MarketSentimentGate reuses plugins/rss_monitor.py's RSSMonitorPlugin
(subscribe once, rss_check returns only entries new since the last check)
and the same "trusted internal code calls a plugin directly, then
LLM-scores the result" shape main.py's _fundamentals_red_flag_scan already
uses. Deliberately market-wide, not per-symbol -- the simplest version
that's actually verifiable end to end in one day.

StockSentimentGate (2026-09-01 follow-up) is the per-symbol counterpart --
penny stocks in particular move on stock-specific news/hype more than
broad market mood. Sourced from a symbol-scoped web search (RSS isn't
naturally per-ticker) with a time-based cache instead of RSS's own
"nothing new" cheapness check.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

CompleteFn = Callable[[str], Awaitable[str]]

_VALID_LABELS = ("BULLISH", "NEUTRAL", "BEARISH")

# Capped so one refresh is one bounded-size prompt regardless of how many
# feeds fired between ticks -- most recent headlines matter most for "what's
# the market's mood right now."
_MAX_HEADLINES = 20


@dataclass
class SentimentReading:
    label: str = "NEUTRAL"
    reason: str = "no reading yet"
    updated_at: Optional[datetime] = field(default=None)


def _parse_verdict(raw: str) -> "tuple[str, str]":
    """"BULLISH" / "NEUTRAL" / "BEARISH: <reason>" -> (label, reason).

    Unrecognized/garbage output holds NEUTRAL rather than guessing --
    same "anything the model can't express cleanly is 'no opinion', not a
    fabricated verdict" principle as live_tick.evaluate_signal.
    """
    raw = (raw or "").strip()
    label, _, rest = raw.partition(":")
    label = label.strip().upper()
    if label not in _VALID_LABELS:
        return "NEUTRAL", f"unrecognized model output: {raw!r}"
    reason = rest.strip() or ("overall market tone" if label != "NEUTRAL" else "no strong signal")
    return label, reason


class MarketSentimentGate:
    """Holds the last market-wide sentiment reading, refreshed on demand.

    Fails open on any RSS/LLM error -- this is paper money, a hiccup in
    news fetching must not stall trading, matching the conservative-
    continue failure philosophy used everywhere else in cerebral/trading
    (e.g. live_tick.py's live-preflight fallback to paper)."""

    def __init__(self) -> None:
        self._reading = SentimentReading()

    @property
    def current(self) -> SentimentReading:
        return self._reading

    async def refresh(self, rss_plugin: Any, complete_fn: CompleteFn) -> SentimentReading:
        try:
            result = await rss_plugin.call_tool("rss_check", {})
            if result.is_error:
                raise RuntimeError(result.content)
            import json
            data = json.loads(result.content)
        except Exception:
            logger.warning("[sentiment] rss_check failed, keeping last reading", exc_info=True)
            return self._reading

        headlines: list[str] = []
        for feed_result in data.get("results", []):
            for entry in feed_result.get("new", []):
                title = (entry.get("title") or "").strip()
                summary = (entry.get("summary") or "").strip()
                if title:
                    headlines.append(f"{title} -- {summary}" if summary else title)

        if not headlines:
            # Nothing new since the last check -- no LLM call, keep the
            # existing reading. This is what keeps refresh() cheap enough
            # to call every scheduler tick.
            return self._reading

        headlines = headlines[:_MAX_HEADLINES]
        prompt = (
            "You are a market analyst. Based ONLY on these recent market-news "
            "headlines, what is the overall market sentiment right now?\n\n"
            + "\n".join(f"- {h}" for h in headlines)
            + "\n\nRespond with exactly one line: 'BULLISH', 'NEUTRAL', or "
            "'BEARISH: <one-sentence reason>'."
        )
        try:
            raw = await complete_fn(prompt)
        except Exception:
            logger.warning("[sentiment] LLM scoring failed, keeping last reading", exc_info=True)
            return self._reading

        label, reason = _parse_verdict(raw)
        self._reading = SentimentReading(label=label, reason=reason, updated_at=datetime.now(timezone.utc))
        logger.info("[sentiment] refreshed: %s (%s)", label, reason)
        return self._reading


WebSearchFn = Callable[[str], "Awaitable[list[dict]]"]

# Per-symbol news is genuinely per-tick-worthy noise, not "check once an
# hour and reuse" like the market-wide feed -- but there's no free "nothing
# new since last check" signal for a symbol-scoped web search the way
# RSSMonitorPlugin gives MarketSentimentGate.refresh() above, so a
# time-based cache is what keeps this affordable to call every dispatch
# tick without a fresh web_search + LLM call per symbol every 5 minutes.
_STOCK_SENTIMENT_TTL_MINUTES = 60.0


class StockSentimentGate:
    """Per-symbol sentiment (BULLISH/NEUTRAL/BEARISH), sourced from a
    symbol-scoped web search rather than general market-news RSS -- penny
    stocks in particular move on stock-specific news/hype, not broad
    market mood (which MarketSentimentGate above already covers).

    Same fail-open philosophy as MarketSentimentGate: a bad web_search or
    LLM call keeps the last reading (or the flat default) rather than
    stalling a dispatch tick over one symbol's sentiment lookup.
    """

    def __init__(self, ttl_minutes: float = _STOCK_SENTIMENT_TTL_MINUTES) -> None:
        self._readings: dict[str, SentimentReading] = {}
        self._ttl = timedelta(minutes=ttl_minutes)

    def current(self, symbol: str) -> SentimentReading:
        return self._readings.get(symbol, SentimentReading())

    async def refresh(
        self, symbol: str, web_search_fn: WebSearchFn, complete_fn: CompleteFn
    ) -> SentimentReading:
        existing = self._readings.get(symbol)
        now = datetime.now(timezone.utc)
        if existing is not None and existing.updated_at is not None and now - existing.updated_at < self._ttl:
            return existing

        try:
            hits = await web_search_fn(f"{symbol} stock news")
        except Exception:
            logger.warning("[stock_sentiment] web_search failed for %s, keeping last reading", symbol, exc_info=True)
            return existing or SentimentReading()

        headlines: list[str] = []
        for hit in (hits or [])[:_MAX_HEADLINES]:
            title = (hit.get("title") or hit.get("page_title") or "").strip()
            summary = (hit.get("snippet") or hit.get("text") or "").strip()
            if title:
                headlines.append(f"{title} -- {summary}" if summary else title)

        if not headlines:
            # No results this refresh -- keep the last reading rather than
            # overwrite a real prior verdict with a fabricated "no opinion".
            return existing or SentimentReading()

        prompt = (
            f"You are a market analyst. Based ONLY on these recent news "
            f"headlines about {symbol}, what is the sentiment right now?\n\n"
            + "\n".join(f"- {h}" for h in headlines)
            + "\n\nRespond with exactly one line: 'BULLISH', 'NEUTRAL', or "
            "'BEARISH: <one-sentence reason>'."
        )
        try:
            raw = await complete_fn(prompt)
        except Exception:
            logger.warning("[stock_sentiment] LLM scoring failed for %s, keeping last reading", symbol, exc_info=True)
            return existing or SentimentReading()

        label, reason = _parse_verdict(raw)
        reading = SentimentReading(label=label, reason=reason, updated_at=now)
        self._readings[symbol] = reading
        logger.info("[stock_sentiment] refreshed %s: %s (%s)", symbol, label, reason)
        return reading
