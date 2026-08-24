"""Tests for cerebral/trading/discovery.py (S27/#880).

Pure and duck-typed -- every external call (run_gauntlet, judge_idea,
Activity Log) is injected. No network, no real LLM, no real sandbox.
"""
import pytest

from cerebral.trading.discovery import (
    DiscoveryWatchlist,
    extract_ticker,
    process_idea,
    run_discovery_pass,
)
from cerebral.trading_ideas import Idea, from_prose


def _watchlist(tmp_path):
    return DiscoveryWatchlist(db_path=tmp_path / "watchlist.db")


def _ticker_idea(symbol="AAPL"):
    return Idea(
        source_url=f"https://example.com/{symbol.lower()}-news",
        page_title=f"{symbol} beats earnings",
        claim_text=f"{symbol} tends to rally after strong earnings beats.",
        provenance=f"url: https://example.com/{symbol.lower()}-news",
    )


def _pattern_idea():
    return from_prose("Mean reversion after a 3-day losing streak.")


class RecordingGauntlet:
    """Fake run_gauntlet_fn that records every call."""
    def __init__(self):
        self.calls = []

    async def __call__(self, idea: Idea, ticker: str) -> dict:
        self.calls.append((idea, ticker))
        return {"ticker": ticker, "verdict": "VALIDATED"}


class FixedJudge:
    def __init__(self, accepted: bool, reason: str = "test reason"):
        self._accepted = accepted
        self._reason = reason
        self.calls = []

    async def __call__(self, idea: Idea):
        self.calls.append(idea)
        return self._accepted, self._reason


class RecordingActivity:
    def __init__(self):
        self.calls = []

    async def __call__(self, kind, content):
        self.calls.append((kind, content))


# ── DiscoveryWatchlist ───────────────────────────────────────────────────

def test_watchlist_upsert_is_idempotent_on_symbol(tmp_path):
    wl = _watchlist(tmp_path)
    wl.upsert("AAPL", source="url: x")
    wl.upsert("AAPL", source="url: y")

    assert wl.symbols() == ["AAPL"]


def test_watchlist_prefilter_candidates_caps_at_limit(tmp_path):
    wl = _watchlist(tmp_path)
    for sym in ["AAPL", "MSFT", "GOOGL", "TSLA"]:
        wl.upsert(sym)

    candidates = wl.prefilter_candidates(_pattern_idea(), limit=2)

    assert len(candidates) == 2


# ── extract_ticker ────────────────────────────────────────────────────────

def test_extract_ticker_recognizes_a_known_symbol():
    idea = _ticker_idea("TSLA")
    assert extract_ticker(idea) == "TSLA"


def test_extract_ticker_returns_none_for_pattern_general_idea():
    idea = _pattern_idea()
    assert extract_ticker(idea) is None


def test_extract_ticker_does_not_guess_an_unknown_all_caps_word():
    """Conservative by design: an unrecognized all-caps token (e.g. an
    acronym) must not be mistaken for a ticker -- screen by default."""
    idea = Idea(claim_text="RSI and MACD both signal a reversal soon.")
    assert extract_ticker(idea) is None


# ── process_idea: ticker-specific path (decision #36) ────────────────────

async def test_ticker_specific_idea_skips_the_judge_entirely(tmp_path):
    wl = _watchlist(tmp_path)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=False)  # would reject everything, if ever called

    results = await process_idea(_ticker_idea("AAPL"), wl, gauntlet, judge_idea_fn=judge)

    assert len(results) == 1
    assert results[0]["ticker"] == "AAPL"
    assert judge.calls == []  # never consulted
    assert len(gauntlet.calls) == 1


async def test_ticker_specific_idea_registers_on_the_watchlist(tmp_path):
    wl = _watchlist(tmp_path)
    await process_idea(_ticker_idea("AAPL"), wl, RecordingGauntlet())

    assert "AAPL" in wl.symbols()


async def test_ticker_specific_idea_logs_to_the_activity_log(tmp_path):
    wl = _watchlist(tmp_path)
    activity = RecordingActivity()

    await process_idea(_ticker_idea("AAPL"), wl, RecordingGauntlet(), record_activity_fn=activity)

    assert len(activity.calls) == 1
    kind, content = activity.calls[0]
    assert content["source"] == "discovery"
    assert content["status"] == "dispatched"
    assert content["ticker"] == "AAPL"


# ── process_idea: pattern-general path (decisions #44, #36) ─────────────

async def test_pattern_general_idea_is_judged_first(tmp_path):
    wl = _watchlist(tmp_path)
    judge = FixedJudge(accepted=True)

    await process_idea(_pattern_idea(), wl, RecordingGauntlet(), judge_idea_fn=judge)

    assert len(judge.calls) == 1


async def test_rejected_idea_never_reaches_run_gauntlet(tmp_path):
    """The acceptance test #880 names explicitly."""
    wl = _watchlist(tmp_path)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=False, reason="too vague")

    results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge)

    assert results == []
    assert gauntlet.calls == []


async def test_rejected_idea_logs_to_the_activity_log(tmp_path):
    wl = _watchlist(tmp_path)
    activity = RecordingActivity()
    judge = FixedJudge(accepted=False, reason="too vague")

    await process_idea(_pattern_idea(), wl, RecordingGauntlet(),
                        judge_idea_fn=judge, record_activity_fn=activity)

    assert len(activity.calls) == 1
    kind, content = activity.calls[0]
    assert content["status"] == "rejected"
    assert content["reason"] == "too vague"


async def test_accepted_pattern_idea_only_dispatches_the_prefiltered_candidates(tmp_path):
    """Only the watchlist's pre-filtered candidates reach run_gauntlet --
    not an unbounded universe sweep."""
    wl = _watchlist(tmp_path)
    for sym in ["AAPL", "MSFT"]:
        wl.upsert(sym)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge)

    dispatched_tickers = {c[1] for c in gauntlet.calls}
    assert dispatched_tickers == {"AAPL", "MSFT"}
    assert len(results) == 2


async def test_accepted_idea_with_empty_watchlist_dispatches_nothing(tmp_path):
    wl = _watchlist(tmp_path)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge)

    assert results == []


async def test_no_judge_configured_accepts_by_default(tmp_path):
    wl = _watchlist(tmp_path)
    wl.upsert("AAPL")
    gauntlet = RecordingGauntlet()

    results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=None)

    assert len(results) == 1


# ── run_discovery_pass ────────────────────────────────────────────────────

async def test_run_discovery_pass_processes_every_idea(tmp_path):
    wl = _watchlist(tmp_path)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)
    ideas = [_ticker_idea("AAPL"), _ticker_idea("TSLA")]

    results = await run_discovery_pass(ideas, wl, gauntlet, judge_idea_fn=judge)

    assert len(results) == 2
    assert {c[1] for c in gauntlet.calls} == {"AAPL", "TSLA"}
