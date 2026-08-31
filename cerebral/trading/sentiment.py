"""Market-wide sentiment gate, sourced from general market-news RSS feeds.

Reuses plugins/rss_monitor.py's RSSMonitorPlugin (subscribe once, rss_check
returns only entries new since the last check) and the same "trusted
internal code calls a plugin directly, then LLM-scores the result" shape
main.py's _fundamentals_red_flag_scan already uses. Deliberately
market-wide, not per-symbol -- the simplest version that's actually
verifiable end to end in one day.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
