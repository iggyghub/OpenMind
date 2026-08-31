"""cerebral/trading/sentiment.py: MarketSentimentGate.refresh() -- no
network, no feedparser, no real LLM call. rss_plugin and complete_fn are
both fakes/stubs, matching every other trading test file's DI convention."""
import json

from cerebral.trading.sentiment import MarketSentimentGate, SentimentReading


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
