"""cerebral/trading/bear_case.py: assess() -- no network, no real LLM call.
complete_fn is a fake/stub, matching test_trading_sentiment.py's DI
convention."""
from cerebral.trading.bear_case import assess


async def test_proceed_response_does_not_veto():
    async def complete_fn(prompt):
        return "PROCEED"

    veto, reason = await assess("AAPL", "def strategy(data): return [1]*len(data)", 1, complete_fn)

    assert veto is False
    assert reason == ""


async def test_veto_response_blocks_with_its_reason():
    async def complete_fn(prompt):
        assert "AAPL" in prompt
        return "VETO: earnings report due before market open tomorrow"

    veto, reason = await assess("AAPL", "def strategy(data): return [1]*len(data)", 1, complete_fn)

    assert veto is True
    assert reason == "earnings report due before market open tomorrow"


async def test_llm_failure_fails_open_never_vetoes():
    async def complete_fn(prompt):
        raise RuntimeError("model unavailable")

    veto, reason = await assess("AAPL", "def strategy(data): return [1]*len(data)", 1, complete_fn)

    assert veto is False


async def test_unrecognized_output_holds_proceed_rather_than_guessing():
    async def complete_fn(prompt):
        return "maybe? hard to say"

    veto, reason = await assess("AAPL", "def strategy(data): return [1]*len(data)", 1, complete_fn)

    assert veto is False


async def test_veto_without_a_colon_still_blocks_with_a_default_reason():
    async def complete_fn(prompt):
        return "VETO"

    veto, reason = await assess("AAPL", "def strategy(data): return [1]*len(data)", 1, complete_fn)

    assert veto is True
    assert reason  # some non-empty default reason, not blank
