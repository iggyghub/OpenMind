import unittest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
from cerebral.trading_ideas import Idea, judge_idea
from cerebral.trading.discovery import (
    _run_discovery_loop,
    _discovery_watchlist,
    extract_ticker_from_idea,
    _dispatch_to_gauntlet,
)


class FakeRouter:
    def __init__(self, accept=True, reason="Accepted"):
        self.accept = accept
        self.reason = reason
        self.calls = []

    async def complete(self, prompt: str, task_type: str) -> str:
        self.calls.append((prompt, task_type))
        import json
        return json.dumps({"accepted": self.accept, "reason": self.reason})


class TestJudgeIdea(unittest.IsolatedAsyncioTestCase):
    def test_accepts_specific_testable_claim(self):
        idea = Idea(claim_text="When RSI < 30 and volume > 200k, buy AAPL.")
        router = FakeRouter(accept=True)
        accepted, reason = self.loop.run_until_complete(judge_idea(idea, router))
        self.assertTrue(accepted)
        self.assertIn("Accept", reason)

    def test_rejects_vague_claim(self):
        idea = Idea(claim_text="The market will probably go up tomorrow maybe.")
        router = FakeRouter(accept=False, reason="Too vague")
        accepted, reason = self.loop.run_until_complete(judge_idea(idea, router))
        self.assertFalse(accepted)
        self.assertIn("vague", reason.lower())

    def test_heuristic_fallback_no_router(self):
        idea = Idea(claim_text="Buy when price is above moving average.")
        accepted, reason = self.loop.run_until_complete(judge_idea(idea))
        self.assertTrue(accepted)
        self.assertEqual(reason, "Heuristic check passed")


class TestDiscoveryLoop(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _discovery_watchlist._entries.clear()

    @patch("cerebral.trading.discovery._run_gauntlet")
    @patch("cerebral.trading.discovery._discover_ideas_from_web")
    @patch("cerebral.trading.discovery.judge_idea")
    def test_ticker_specific_skips_screening(self, mock_judge, mock_discover, mock_gauntlet):
        idea = Idea(source_url="http://news.com/aapl-strategy", claim_text="AAPL breaks resistance at 150.")
        mock_discover.return_value = [idea]
        mock_gauntlet.return_value = {"status": "success"}

        results = _run_discovery_loop()

        mock_judge.assert_not_called()
        mock_gauntlet.assert_called_once()
        call_kwargs = mock_gauntlet.call_args.kwargs
        self.assertEqual(call_kwargs.get("origin"), "discovered")
        self.assertEqual(call_kwargs.get("ticker"), "AAPL")
        self.assertIn("AAPL", _discovery_watchlist.symbols())

    @patch("cerebral.trading.discovery._run_gauntlet")
    @patch("cerebral.trading.discovery._discover_ideas_from_web")
    @patch("cerebral.trading.discovery.judge_idea")
    def test_pattern_general_runs_screening(self, mock_judge, mock_discover, mock_gauntlet):
        idea = Idea(source_url="http://news.com/mean-reversion", claim_text="Mean reversion strategies work in high volatility.")
        mock_discover.return_value = [idea]
        _discovery_watchlist.upsert("MSFT", source="seed")

        _run_discovery_loop()

        mock_judge.assert_called_once()
        self.assertEqual(mock_gauntlet.call_count, 1)
        call_kwargs = mock_gauntlet.call_args.kwargs
        self.assertEqual(call_kwargs.get("ticker"), "MSFT")

    @patch("cerebral.trading.discovery._run_gauntlet")
    @patch("cerebral.trading.discovery._discover_ideas_from_web")
    @patch("cerebral.trading.discovery.judge_idea")
    def test_rejected_idea_never_reaches_gauntlet(self, mock_judge, mock_discover, mock_gauntlet):
        mock_judge.return_value = (False, "Rejected: vague")
        idea = Idea(claim_text="Crypto is good.")
        mock_discover.return_value = [idea]
        
        _run_discovery_loop()
        mock_gauntlet.assert_not_called()

    @patch("cerebral.trading.discovery._log_activity")
    @patch("cerebral.trading.discovery._discover_ideas_from_web")
    @patch("cerebral.trading.discovery.judge_idea")
    def test_activity_log_entries_produced(self, mock_judge, mock_discover, mock_log):
        mock_discover.return_value = [
            Idea(claim_text="Accepted idea", source_url="http://a.com"),
            Idea(claim_text="Rejected idea", source_url="http://b.com"),
        ]
        mock_judge.side_effect = [(True, "Ok"), (False, "Bad")]
        
        _run_discovery_loop()
        self.assertEqual(mock_log.call_count, 2)


if __name__ == "__main__":
    unittest.main()
