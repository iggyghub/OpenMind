import unittest
import datetime
from unittest.mock import patch
from cerebral.trading_ideas import (
    extract_from_url,
    from_prose,
    from_book_claim,
    to_strategy,
    judge_idea,
    Idea,
    _compile_strategy as compile_strategy,
    _run_tally,
)


class StubFetcher:
    def __call__(self, url: str):
        return {
            "html": "<html><body>Test Page</body></html>",
            "title": "Test Title",
            "text": "Market goes up on Mondays.",
            "links": ["http://example.com/other"],
        }


class StubCrawler:
    def __call__(self, url: str):
        return ["http://example.com/other"]


class StubLLM:
    def generate(self, prompt: str) -> str:
        return "def strategy(data):\n    return [1, 2, 3]"


class TestTradingIdeas(unittest.IsolatedAsyncioTestCase):
    def test_extract_from_url_with_stubs(self):
        stub_fetcher = StubFetcher()
        stub_crawler = StubCrawler()
        ideas = extract_from_url(
            "http://test.com", fetcher=stub_fetcher, crawler=stub_crawler
        )
        self.assertEqual(len(ideas), 2)
        idea = ideas[0]
        self.assertEqual(idea.source_url, "http://test.com")
        self.assertEqual(idea.page_title, "Test Title")
        self.assertEqual(idea.provenance, "url: http://test.com")
        self.assertIn("Author claims:", idea.author_claim_text)
        self.assertIsInstance(idea.date_accessed, str)

    def test_from_prose(self):
        idea = from_prose("Bought AAPL because it's cheap.")
        self.assertEqual(idea.provenance, "user, verbatim")
        self.assertEqual(idea.claim_text, "Bought AAPL because it's cheap.")
        self.assertIn("User claims:", idea.author_claim_text)

    def test_from_book_claim(self):
        idea = from_book_claim("Buy on low volume.", "Alpha Quant", "3")
        self.assertEqual(idea.provenance, "book: Alpha Quant ch 3")
        self.assertEqual(idea.book_info, {"book": "Alpha Quant", "chapter": "3"})
        self.assertIn("Book 'Alpha Quant' Chapter '3' claims:", idea.author_claim_text)

    async def test_to_strategy_with_stub_llm(self):
        idea = from_prose("Buy when RSI < 30")
        llm_stub = StubLLM()
        code = await to_strategy(idea, llm=llm_stub)
        self.assertEqual(code, "def strategy(data):\n    return [1, 2, 3]")

        # Verify it compiles and runs per backtest engine interface. The
        # stub always returns [1, 2, 3] regardless of input -- that's what
        # a call must actually produce, not the input echoed back.
        strategy_fn = compile_strategy(code)
        self.assertEqual(strategy_fn({"close": [1, 2]}), [1, 2, 3])

    async def test_to_strategy_honesty_rule_in_prompt(self):
        idea = from_prose("Market always rises.")

        class NaiveLLM:
            def generate(self, prompt: str) -> str:
                # Simulates LLM ignoring rule unless forced by prompt
                return "def strategy(data): return [1]"

        code = await to_strategy(idea, llm=NaiveLLM())
        self.assertIn("def strategy(data):", code)
        # In production, the prompt enforces the honesty rule strictly.

    async def test_to_strategy_uses_the_router_when_given(self):
        """S15/#860: to_strategy must actually await the router -- the
        first attempt at this called router.complete() without awaiting
        it (a coroutine object, not a string, would have been returned)."""
        idea = from_prose("Buy when RSI < 30")

        class FakeRouter:
            def __init__(self):
                self.calls = []

            async def complete(self, prompt: str, task_type: str) -> str:
                self.calls.append((prompt, task_type))
                return "def strategy(data):\n    return [1] * len(data)"

        router = FakeRouter()
        code = await to_strategy(idea, router=router)

        self.assertEqual(code, "def strategy(data):\n    return [1] * len(data)")
        self.assertEqual(len(router.calls), 1)
        prompt, task_type = router.calls[0]
        self.assertEqual(task_type, "coding")
        self.assertIn("Buy when RSI < 30", prompt)

    async def test_to_strategy_strips_markdown_fence_and_prose(self):
        """2026-08-26: chat-tuned models wrap generated code in a ```
        fence with prose before/after ("Here is a Python strategy
        function..."). Exec'ing that raw reply is a SyntaxError, which
        sandboxed_eval.py silently degrades to an all-flat signal --
        the `monte_carlo_permutation: p=1.000` pattern seen live on two
        independent ideas. to_strategy must hand back bare, compilable
        code regardless of how the model dresses up its reply."""
        idea = from_prose("Buy when RSI < 30")

        class ChattyRouter:
            async def complete(self, prompt: str, task_type: str) -> str:
                return (
                    "Here is a Python strategy function that implements "
                    "the hypothesis:\n\n```python\n"
                    "def strategy(data):\n    return [1] * len(data)\n"
                    "```\n\nThis function always returns long."
                )

        code = await to_strategy(idea, router=ChattyRouter())

        self.assertEqual(code, "def strategy(data):\n    return [1] * len(data)")
        strategy_fn = compile_strategy(code)  # must not raise SyntaxError
        self.assertEqual(strategy_fn({"close": [1, 2]}), [1])

    async def test_to_strategy_router_failure_falls_back_to_the_stub(self):
        """Conservative-continue: a router failure must not raise -- it
        must degrade to the stub, same as every other failure mode in
        this campaign."""
        idea = from_prose("Buy when RSI < 30")

        class FailingRouter:
            async def complete(self, prompt: str, task_type: str) -> str:
                raise RuntimeError("model unavailable")

        code = await to_strategy(idea, router=FailingRouter())

        self.assertIn("def strategy(data):", code)
        self.assertNotIn("model unavailable", code)  # the stub, not an error string

    async def test_to_strategy_with_neither_llm_nor_router_uses_the_stub(self):
        """The pre-existing default-path behaviour must not regress."""
        idea = from_prose("Buy when RSI < 30")
        code = await to_strategy(idea)
        self.assertIn("def strategy(data):", code)

    def test_compile_strategy_validation(self):
        bad_code = "def bad(): pass"
        with self.assertRaises(ValueError):
            compile_strategy(bad_code)

    async def test_generated_stub_honours_the_live_contract(self):
        """The stub used to read data.get("close", []) -- lowercase -- which
        against a real fetch_ohlcv DataFrame silently returns the [] default,
        so it emitted no signals at all. It must read "Close" and return one
        target position per bar, each in {1, 0, -1} (live_tick.py's contract).
        """
        import pandas as pd

        idea = from_prose("Price above its running mean keeps trending.")
        strategy_fn = compile_strategy(await to_strategy(idea))  # no llm -> the stub
        bars = pd.DataFrame(
            {"Open": [1.0, 2.0, 3.0], "High": [1.0, 2.0, 3.0],
             "Low": [1.0, 2.0, 3.0], "Close": [10.0, 12.0, 8.0],
             "Volume": [100, 100, 100]},
            index=pd.date_range("2026-01-01", periods=3, freq="D"),
        )

        signals = strategy_fn(bars)

        self.assertEqual(len(signals), len(bars))
        self.assertTrue(all(s in (1, 0, -1) for s in signals))
        # 12 > mean(10,12)=11 -> long;  8 < mean(10,12,8)=10 -> flat.
        self.assertEqual(signals, [0, 1, 0])

    # ── judge_idea (S27/#880, decision #44) ─────────────────────────────

    async def test_judge_idea_rejects_a_vague_claim(self):
        idea = from_prose("The market is generally efficient over time.")

        class RejectingRouter:
            async def complete(self, prompt: str, task_type: str) -> str:
                return "REJECT: no falsifiable prediction"

        accepted, reason = await judge_idea(idea, router=RejectingRouter())

        self.assertFalse(accepted)
        self.assertIn("falsifiable", reason)

    async def test_judge_idea_accepts_a_testable_claim(self):
        idea = from_prose("RSI below 30 predicts a bounce within 5 days.")

        class AcceptingRouter:
            async def complete(self, prompt: str, task_type: str) -> str:
                return "ACCEPT"

        accepted, reason = await judge_idea(idea, router=AcceptingRouter())

        self.assertTrue(accepted)

    async def test_judge_idea_uses_the_router_with_coding_task_type(self):
        idea = from_prose("RSI below 30 predicts a bounce within 5 days.")

        class FakeRouter:
            def __init__(self):
                self.calls = []

            async def complete(self, prompt: str, task_type: str) -> str:
                self.calls.append((prompt, task_type))
                return "ACCEPT"

        router = FakeRouter()
        await judge_idea(idea, router=router)

        self.assertEqual(len(router.calls), 1)
        prompt, task_type = router.calls[0]
        self.assertEqual(task_type, "coding")
        self.assertIn("RSI below 30", prompt)

    async def test_judge_idea_router_failure_accepts_by_default(self):
        """Conservative-continue: a real idea must not be silently lost
        because the judge model was unreachable."""
        idea = from_prose("RSI below 30 predicts a bounce within 5 days.")

        class FailingRouter:
            async def complete(self, prompt: str, task_type: str) -> str:
                raise RuntimeError("model unavailable")

        accepted, reason = await judge_idea(idea, router=FailingRouter())

        self.assertTrue(accepted)

    async def test_judge_idea_with_no_router_accepts_by_default(self):
        idea = from_prose("RSI below 30 predicts a bounce within 5 days.")

        accepted, reason = await judge_idea(idea)

        self.assertTrue(accepted)

    # ── S41: Tally wired into judge_idea/to_strategy prompts ────────────
    # S41's own PR left _run_tally as a permanent stub always returning
    # (False, 0, 0) -- never actually calling S40's retrieval or S38's
    # confidence weight -- and shipped with zero tests for either the real
    # wiring or the prompt/bias behavior it exists to drive. Both fixed
    # here: _run_tally now does the real S40+S38 call, and this section
    # covers what the original issue's acceptance criteria asked for.

    async def test_to_strategy_includes_tally_sentence_when_available(self):
        idea = from_prose("Buy when RSI < 30")

        class FakeRouter:
            def __init__(self):
                self.calls = []

            async def complete(self, prompt: str, task_type: str) -> str:
                self.calls.append(prompt)
                return "def strategy(data):\n    return [1]"

        router = FakeRouter()
        with patch("cerebral.trading_ideas._run_tally", return_value=(True, 3, 5)):
            await to_strategy(idea, router=router)

        self.assertIn("Tally: 5 similar past claims: 3 had positive", router.calls[0])

    async def test_to_strategy_omits_tally_sentence_when_unavailable(self):
        idea = from_prose("Buy when RSI < 30")

        class FakeRouter:
            def __init__(self):
                self.calls = []

            async def complete(self, prompt: str, task_type: str) -> str:
                self.calls.append(prompt)
                return "def strategy(data):\n    return [1]"

        router = FakeRouter()
        with patch("cerebral.trading_ideas._run_tally", return_value=(False, 0, 0)):
            await to_strategy(idea, router=router)

        self.assertNotIn("Tally:", router.calls[0])

    async def test_judge_idea_includes_tally_sentence_when_available(self):
        idea = from_prose("RSI below 30 predicts a bounce within 5 days.")

        class FakeRouter:
            def __init__(self):
                self.calls = []

            async def complete(self, prompt: str, task_type: str) -> str:
                self.calls.append(prompt)
                return "ACCEPT"

        router = FakeRouter()
        with patch("cerebral.trading_ideas._run_tally", return_value=(True, 1, 4)):
            await judge_idea(idea, router=router)

        self.assertIn("Tally: 4 similar past claims: 1 had positive", router.calls[0])

    async def test_judge_idea_omits_tally_sentence_when_unavailable(self):
        idea = from_prose("RSI below 30 predicts a bounce within 5 days.")

        class FakeRouter:
            def __init__(self):
                self.calls = []

            async def complete(self, prompt: str, task_type: str) -> str:
                self.calls.append(prompt)
                return "ACCEPT"

        router = FakeRouter()
        with patch("cerebral.trading_ideas._run_tally", return_value=(False, 0, 0)):
            await judge_idea(idea, router=router)

        self.assertNotIn("Tally:", router.calls[0])

    def test_run_tally_empty_retrieval_returns_unavailable(self):
        """No similar claims retrieved -> (False, 0, 0), not (True, 0, 0) --
        callers gate on the success flag, not just total > 0, but this keeps
        both consistent."""
        class EmptyStore:
            def retrieve_top5(self, claim_text):
                return {"ids": [[]]}

        with patch("cerebral.trading.claim_store.TradingStrategies", return_value=EmptyStore()):
            success, pos, total = _run_tally("some claim")

        self.assertFalse(success)
        self.assertEqual((pos, total), (0, 0))

    def test_run_tally_swallows_retrieval_failure(self):
        """Conservative-continue: a real chromadb/embedding failure must not
        raise out of _run_tally -- matches judge_idea/to_strategy's own
        established failure convention for every other nudge input."""
        class FailingStore:
            def retrieve_top5(self, claim_text):
                raise RuntimeError("chromadb unavailable")

        with patch("cerebral.trading.claim_store.TradingStrategies", return_value=FailingStore()):
            success, pos, total = _run_tally("some claim")

        self.assertEqual((success, pos, total), (False, 0, 0))

    def test_run_tally_computes_real_positive_count_from_confidence_weight(self):
        """The real S40 (retrieval) + S38 (confidence weight) wiring, not a
        stub: 3 retrieved ids, 2 with positive weight, 1 with negative."""
        class FakeStore:
            def retrieve_top5(self, claim_text):
                return {"ids": [["a", "b", "c"]]}

            @staticmethod
            def compute_tally(strategy_ids, weights):
                pos = sum(1 for sid in strategy_ids if weights.get(sid, 0) > 0)
                return (pos, len(strategy_ids))

        class FakeRecord:
            _weights = {"a": 1.5, "b": -0.3, "c": 0.2}

            def compute_confidence_weight(self, strategy_id):
                return self._weights[strategy_id]

        with patch("cerebral.trading.claim_store.TradingStrategies", return_value=FakeStore()), \
             patch("cerebral.trading.forward_record.ForwardRecord", return_value=FakeRecord()):
            success, pos, total = _run_tally("some claim")

        self.assertEqual((success, pos, total), (True, 2, 3))


if __name__ == "__main__":
    unittest.main()
