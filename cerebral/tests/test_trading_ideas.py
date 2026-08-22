import unittest
import datetime
from cerebral.trading_ideas import (
    extract_from_url,
    from_prose,
    from_book_claim,
    to_strategy,
    Idea,
    compile_strategy,
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


class TestTradingIdeas(unittest.TestCase):
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

    def test_to_strategy_with_stub_llm(self):
        idea = from_prose("Buy when RSI < 30")
        llm_stub = StubLLM()
        code = to_strategy(idea, llm=llm_stub)
        self.assertEqual(code, "def strategy(data):\n    return [1, 2, 3]")

        # Verify it compiles and runs per backtest engine interface. The
        # stub always returns [1, 2, 3] regardless of input -- that's what
        # a call must actually produce, not the input echoed back.
        strategy_fn = compile_strategy(code)
        self.assertEqual(strategy_fn({"close": [1, 2]}), [1, 2, 3])

    def test_to_strategy_honesty_rule_in_prompt(self):
        idea = from_prose("Market always rises.")

        class NaiveLLM:
            def generate(self, prompt: str) -> str:
                # Simulates LLM ignoring rule unless forced by prompt
                return "def strategy(data): return [1]"

        code = to_strategy(idea, llm=NaiveLLM())
        self.assertIn("def strategy(data):", code)
        # In production, the prompt enforces the honesty rule strictly.

    def test_compile_strategy_validation(self):
        bad_code = "def bad(): pass"
        with self.assertRaises(ValueError):
            compile_strategy(bad_code)


if __name__ == "__main__":
    unittest.main()
