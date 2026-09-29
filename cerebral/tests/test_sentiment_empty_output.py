"""Empty/truncated model output must not be cached as a fake NEUTRAL."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cerebral.trading.sentiment import (
    MarketSentimentGate, SentimentReading, StockSentimentGate,
)


class _Rss:
    async def call_tool(self, name, args):
        body = {"results": [{"new": [{"title": "Stocks rally", "summary": ""}]}]}
        return SimpleNamespace(is_error=False, content=json.dumps(body))


async def test_market_empty_output_keeps_prior_reading():
    gate = MarketSentimentGate()
    gate._reading = SentimentReading(label="BULLISH", reason="r", updated_at=datetime.now(timezone.utc))
    stamp = gate._reading.updated_at

    async def empty(_p):
        return ""

    got = await gate.refresh(_Rss(), empty)
    assert got.label == "BULLISH" and got.updated_at == stamp


async def test_stock_empty_output_not_cached_and_retried():
    gate = StockSentimentGate()
    calls = []

    async def search(_q):
        return [{"title": "AAA soars"}]

    async def empty(_p):
        calls.append(1)
        return "  "

    r = await gate.refresh("AAA", search, empty)
    assert r.updated_at is None and "AAA" not in gate._readings
    await gate.refresh("AAA", search, empty)
    assert len(calls) == 2  # not held for the TTL


async def test_stock_empty_output_keeps_prior_bullish():
    gate = StockSentimentGate(ttl_minutes=1)
    old = SentimentReading(label="BULLISH", reason="x", updated_at=datetime.now(timezone.utc) - timedelta(hours=1))
    gate._readings["AAA"] = old

    async def search(_q):
        return [{"title": "AAA"}]

    async def empty(_p):
        return ""

    assert (await gate.refresh("AAA", search, empty)) is old
