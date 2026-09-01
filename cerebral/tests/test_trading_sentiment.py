"""cerebral/trading/sentiment.py: MarketSentimentGate.refresh() -- no
network, no feedparser, no real LLM call. rss_plugin and complete_fn are
both fakes/stubs, matching every other trading test file's DI convention."""
import json
from datetime import datetime, timedelta, timezone

from cerebral.trading.sentiment import MarketSentimentGate, SentimentReading, StockSentimentGate


class _FakeToolResult:
    def __init__(self, content, is_error=False):
        self.content = content
        self.is_error = is_error


class _FakeRSSPlugin:
    def __init__(self, results=None, raises=False):
        self._results = results if results is not None else []
        self._raises = raises
        self.call_count = 0

    async def call_tool(self, tool_name, args):
        self.call_count += 1
        if self._raises:
            raise ConnectionError("feed unreachable")
        return _FakeToolResult(json.dumps({"results": self._results}))


def _headline(title, summary=""):
    return {"title": title, "url": "https://example.com", "summary": summary, "published": "", "id": title}


async def test_no_new_headlines_keeps_cached_reading_no_llm_call():
    gate = MarketSentimentGate()
    rss = _FakeRSSPlugin(results=[{"name": "wsj", "new": []}])
    llm_calls = []

    async def complete_fn(prompt):
        llm_calls.append(prompt)
        return "BULLISH"

    reading = await gate.refresh(rss, complete_fn)

    assert reading.label == "NEUTRAL"  # unchanged default
    assert llm_calls == []


async def test_new_headlines_score_via_llm_and_update_reading():
    gate = MarketSentimentGate()
    rss = _FakeRSSPlugin(results=[
        {"name": "wsj", "new": [_headline("Stocks rally on strong earnings", "Broad gains across sectors")]},
    ])

    async def complete_fn(prompt):
        assert "Stocks rally on strong earnings" in prompt
        return "BULLISH"

    reading = await gate.refresh(rss, complete_fn)

    assert reading.label == "BULLISH"
    assert reading.updated_at is not None


async def test_bearish_response_carries_its_reason():
    gate = MarketSentimentGate()
    rss = _FakeRSSPlugin(results=[{"name": "wsj", "new": [_headline("Markets tumble on inflation fears")]}])

    async def complete_fn(prompt):
        return "BEARISH: inflation data spooked investors"

    reading = await gate.refresh(rss, complete_fn)

    assert reading.label == "BEARISH"
    assert reading.reason == "inflation data spooked investors"


async def test_rss_fetch_failure_fails_open_keeps_previous_reading():
    gate = MarketSentimentGate()
    gate._reading = SentimentReading(label="BULLISH", reason="prior read")
    rss = _FakeRSSPlugin(raises=True)

    async def complete_fn(prompt):
        return "BEARISH"  # must not even be reachable -- rss fetch fails first

    reading = await gate.refresh(rss, complete_fn)

    assert reading.label == "BULLISH"  # unchanged, not raised


async def test_llm_failure_fails_open_keeps_previous_reading():
    gate = MarketSentimentGate()
    gate._reading = SentimentReading(label="NEUTRAL", reason="prior read")
    rss = _FakeRSSPlugin(results=[{"name": "wsj", "new": [_headline("Some headline")]}])

    async def complete_fn(prompt):
        raise RuntimeError("model unavailable")

    reading = await gate.refresh(rss, complete_fn)

    assert reading.label == "NEUTRAL"
    assert reading.reason == "prior read"


async def test_unrecognized_model_output_holds_neutral_rather_than_guessing():
    gate = MarketSentimentGate()
    rss = _FakeRSSPlugin(results=[{"name": "wsj", "new": [_headline("Some headline")]}])

    async def complete_fn(prompt):
        return "I'm not sure, maybe up?"

    reading = await gate.refresh(rss, complete_fn)

    assert reading.label == "NEUTRAL"


# ── StockSentimentGate ──────────────────────────────────────────────────

def _hit(title, snippet=""):
    return {"title": title, "snippet": snippet, "url": "https://example.com"}


async def test_stock_new_symbol_fetches_and_scores_via_llm():
    gate = StockSentimentGate()
    search_calls = []

    async def web_search_fn(query):
        search_calls.append(query)
        return [_hit("PENNY surges on strong earnings", "beat estimates")]

    async def complete_fn(prompt):
        assert "PENNY" in prompt
        return "BULLISH"

    reading = await gate.refresh("PENNY", web_search_fn, complete_fn)

    assert reading.label == "BULLISH"
    assert search_calls == ["PENNY stock news"]
    assert gate.current("PENNY").label == "BULLISH"


async def test_stock_within_ttl_returns_cached_reading_no_new_search():
    gate = StockSentimentGate(ttl_minutes=60.0)
    gate._readings["PENNY"] = SentimentReading(
        label="BEARISH", reason="prior read", updated_at=datetime.now(timezone.utc)
    )
    search_calls = []

    async def web_search_fn(query):
        search_calls.append(query)
        return [_hit("should not be reached")]

    async def complete_fn(prompt):
        return "BULLISH"  # must not be reachable -- cache hit short-circuits first

    reading = await gate.refresh("PENNY", web_search_fn, complete_fn)

    assert reading.label == "BEARISH"
    assert search_calls == []


async def test_stock_expired_ttl_refreshes():
    gate = StockSentimentGate(ttl_minutes=60.0)
    stale = datetime.now(timezone.utc) - timedelta(minutes=61)
    gate._readings["PENNY"] = SentimentReading(label="BEARISH", reason="stale", updated_at=stale)

    async def web_search_fn(query):
        return [_hit("PENNY announces new contract")]

    async def complete_fn(prompt):
        return "BULLISH"

    reading = await gate.refresh("PENNY", web_search_fn, complete_fn)

    assert reading.label == "BULLISH"


async def test_stock_no_hits_keeps_previous_reading_no_llm_call():
    gate = StockSentimentGate()
    llm_calls = []

    async def web_search_fn(query):
        return []

    async def complete_fn(prompt):
        llm_calls.append(prompt)
        return "BULLISH"

    reading = await gate.refresh("PENNY", web_search_fn, complete_fn)

    assert reading.label == "NEUTRAL"  # unchanged default
    assert llm_calls == []


async def test_stock_web_search_failure_fails_open_keeps_previous_reading():
    gate = StockSentimentGate()
    gate._readings["PENNY"] = SentimentReading(
        label="BULLISH", reason="prior read", updated_at=datetime.now(timezone.utc) - timedelta(minutes=61)
    )

    async def web_search_fn(query):
        raise ConnectionError("search unreachable")

    async def complete_fn(prompt):
        return "BEARISH"  # must not even be reachable -- search fails first

    reading = await gate.refresh("PENNY", web_search_fn, complete_fn)

    assert reading.label == "BULLISH"  # unchanged, not raised


async def test_stock_llm_failure_fails_open_keeps_previous_reading():
    gate = StockSentimentGate()
    gate._readings["PENNY"] = SentimentReading(
        label="NEUTRAL", reason="prior read", updated_at=datetime.now(timezone.utc) - timedelta(minutes=61)
    )

    async def web_search_fn(query):
        return [_hit("PENNY headline")]

    async def complete_fn(prompt):
        raise RuntimeError("model unavailable")

    reading = await gate.refresh("PENNY", web_search_fn, complete_fn)

    assert reading.label == "NEUTRAL"
    assert reading.reason == "prior read"


async def test_stock_symbols_are_cached_independently():
    gate = StockSentimentGate()

    async def web_search_fn(query):
        if "AAA" in query:
            return [_hit("AAA rallies")]
        return [_hit("BBB slumps")]

    async def complete_fn(prompt):
        return "BULLISH" if "AAA" in prompt else "BEARISH: bad news"

    await gate.refresh("AAA", web_search_fn, complete_fn)
    await gate.refresh("BBB", web_search_fn, complete_fn)

    assert gate.current("AAA").label == "BULLISH"
    assert gate.current("BBB").label == "BEARISH"


def test_stock_current_defaults_to_neutral_for_unknown_symbol():
    gate = StockSentimentGate()
    assert gate.current("NEVERSEEN").label == "NEUTRAL"
