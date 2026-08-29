"""Tests for cerebral/trading/discovery.py (S27/#880).

Pure and duck-typed -- every external call (run_gauntlet, judge_idea,
Activity Log) is injected. No network, no real LLM, no real sandbox.
"""
import pytest
from unittest.mock import patch

from cerebral.trading.discovery import (
    DiscoveryAttempts,
    DiscoveryWatchlist,
    VettedTickers,
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


class RecordingAttempt:
    """Fake record_attempt_fn (S30/#894) -- records every call verbatim."""
    def __init__(self):
        self.calls = []

    async def __call__(self, entry: dict) -> None:
        self.calls.append(entry)


class UnvalidatedGauntlet:
    """Fake run_gauntlet_fn returning the flat test shape with a failed gate."""
    async def __call__(self, idea: Idea, ticker: str) -> dict:
        return {
            "ticker": ticker, "verdict": "UNVALIDATED",
            "gates": [
                {"name": "vs_benchmark", "passed": False, "details": "underperformed by 3.2%"},
            ],
        }


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


def test_watchlist_prefilter_candidates_falls_back_to_known_tickers_when_empty(tmp_path):
    """Fix (2026-08-25): an empty watchlist used to return [] here forever
    -- the watchlist only grows via a dispatch, and a pattern-general idea
    only dispatches via this method's return value, so a cold start could
    never bootstrap itself. Confirmed live: the first real discovery pass
    against the real web_search wiring sourced 6 ideas, dispatched 0."""
    from cerebral.trading.discovery import _KNOWN_TICKERS
    wl = _watchlist(tmp_path)

    candidates = wl.prefilter_candidates(_pattern_idea(), limit=3)

    assert len(candidates) == 3
    assert all(c in _KNOWN_TICKERS for c in candidates)


def test_watchlist_prefilter_candidates_puts_real_watchlist_entries_first(tmp_path):
    """A screened ticker still comes before the known-liquid overflow --
    but (fix, 2026-08-26) no longer excludes it entirely. The old
    behavior (fallback never applies once the watchlist has anything on
    it) is exactly what left a real watchlist stuck at 3 symbols for over
    a day live -- see prefilter_candidates' own docstring."""
    from cerebral.trading.discovery import _KNOWN_TICKERS
    wl = _watchlist(tmp_path)
    wl.upsert("PLTR")  # deliberately NOT in _KNOWN_TICKERS

    candidates = wl.prefilter_candidates(_pattern_idea(), limit=3)

    assert candidates[0] == "PLTR"
    assert len(candidates) == 3
    assert all(c in _KNOWN_TICKERS for c in candidates[1:])


def test_watchlist_prefilter_candidates_never_duplicates_a_watchlist_symbol_already_in_known_tickers(tmp_path):
    """A watchlist symbol that's ALSO in _KNOWN_TICKERS (e.g. AAPL, seeded
    by an earlier ticker-specific dispatch) must appear once, not twice,
    in the combined universe."""
    wl = _watchlist(tmp_path)
    wl.upsert("AAPL")  # IS in _KNOWN_TICKERS

    candidates = wl.prefilter_candidates(_pattern_idea(), limit=100)

    assert candidates.count("AAPL") == 1


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

    results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge, candidate_limit=2)

    dispatched_tickers = {c[1] for c in gauntlet.calls}
    assert dispatched_tickers == {"AAPL", "MSFT"}
    assert len(results) == 2


async def test_accepted_idea_with_empty_watchlist_dispatches_the_known_ticker_fallback(tmp_path):
    """Updated (2026-08-25): this used to assert dispatches nothing --
    that was the real, live cold-start deadlock (see
    test_watchlist_prefilter_candidates_falls_back_to_known_tickers_when_empty).
    An accepted pattern-general idea now reaches the gauntlet even before
    anything has ever been screened, via _KNOWN_TICKERS."""
    from cerebral.trading.discovery import _KNOWN_TICKERS
    wl = _watchlist(tmp_path)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge)

    assert len(results) > 0
    dispatched_tickers = {c[1] for c in gauntlet.calls}
    assert dispatched_tickers.issubset(_KNOWN_TICKERS)


async def test_no_judge_configured_accepts_by_default(tmp_path):
    wl = _watchlist(tmp_path)
    wl.upsert("AAPL")
    gauntlet = RecordingGauntlet()

    results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=None, candidate_limit=1)

    assert len(results) == 1


# ── process_idea: S41 Tally candidate_limit bias ────────────────────────
# S41's own PR left _run_tally (imported here from trading_ideas) as a
# permanent stub always returning (False, 0, 0) -- this bias code path was
# dead in production and had zero test coverage. Patch the name as bound
# in THIS module (`from ... import _run_tally` copies the reference at
# import time, so patching trading_ideas._run_tally would not reach here).

def _populate(wl, n):
    symbols = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "META"][:n]
    for sym in symbols:
        wl.upsert(sym)
    return symbols


async def test_high_tally_biases_candidate_limit_up(tmp_path):
    wl = _watchlist(tmp_path)
    _populate(wl, 5)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    with patch("cerebral.trading.discovery._run_tally", return_value=(True, 3, 5)):  # 60% positive
        results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge, candidate_limit=2)

    assert len(results) == 3  # 2 + 1


async def test_low_tally_biases_candidate_limit_down(tmp_path):
    wl = _watchlist(tmp_path)
    _populate(wl, 5)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    with patch("cerebral.trading.discovery._run_tally", return_value=(True, 1, 5)):  # 20% positive
        results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge, candidate_limit=2)

    assert len(results) == 1  # 2 - 1


async def test_low_tally_bias_floors_at_one_not_zero(tmp_path):
    wl = _watchlist(tmp_path)
    _populate(wl, 5)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    with patch("cerebral.trading.discovery._run_tally", return_value=(True, 0, 5)):  # 0% positive
        results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge, candidate_limit=1)

    assert len(results) == 1  # max(1, 1 - 1) == 1, never 0


async def test_mid_tally_does_not_bias_candidate_limit(tmp_path):
    wl = _watchlist(tmp_path)
    _populate(wl, 5)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    with patch("cerebral.trading.discovery._run_tally", return_value=(True, 2, 4)):  # 50% -- between thresholds
        results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge, candidate_limit=2)

    assert len(results) == 2  # unchanged


async def test_unavailable_tally_does_not_bias_candidate_limit(tmp_path):
    wl = _watchlist(tmp_path)
    _populate(wl, 5)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)

    with patch("cerebral.trading.discovery._run_tally", return_value=(False, 0, 0)):
        results = await process_idea(_pattern_idea(), wl, gauntlet, judge_idea_fn=judge, candidate_limit=2)

    assert len(results) == 2  # unchanged, same as the real stub's default behavior


# ── run_discovery_pass ────────────────────────────────────────────────────

async def test_run_discovery_pass_processes_every_idea(tmp_path):
    wl = _watchlist(tmp_path)
    gauntlet = RecordingGauntlet()
    judge = FixedJudge(accepted=True)
    ideas = [_ticker_idea("AAPL"), _ticker_idea("TSLA")]

    results = await run_discovery_pass(ideas, wl, gauntlet, judge_idea_fn=judge)

    assert len(results) == 2
    assert {c[1] for c in gauntlet.calls} == {"AAPL", "TSLA"}


# ── VettedTickers (S28/#881) ──────────────────────────────────────────────

def _vetted(tmp_path):
    return VettedTickers(db_path=tmp_path / "vetted.db")


def test_get_verdict_returns_none_for_a_never_vetted_symbol(tmp_path):
    v = _vetted(tmp_path)
    assert v.get_verdict("XYZ", "0001-24-000001") is None


def test_get_verdict_returns_the_recorded_verdict_for_the_same_accession(tmp_path):
    v = _vetted(tmp_path)
    v.record("XYZ", "0001-24-000001", red_flagged=True)

    assert v.get_verdict("XYZ", "0001-24-000001") is True


def test_get_verdict_returns_none_for_a_different_accession(tmp_path):
    """A new filing (different accession) must not reuse the old verdict --
    this is the whole mechanism that makes a NEW filing re-trigger a scan."""
    v = _vetted(tmp_path)
    v.record("XYZ", "0001-24-000001", red_flagged=False)

    assert v.get_verdict("XYZ", "0001-24-000002") is None


def test_record_replaces_the_prior_verdict_for_the_same_symbol(tmp_path):
    v = _vetted(tmp_path)
    v.record("XYZ", "0001-24-000001", red_flagged=True)
    v.record("XYZ", "0001-24-000002", red_flagged=False)

    assert v.get_verdict("XYZ", "0001-24-000001") is None  # superseded
    assert v.get_verdict("XYZ", "0001-24-000002") is False


def test_vetted_tickers_are_independent_per_symbol(tmp_path):
    v = _vetted(tmp_path)
    v.record("XYZ", "0001-24-000001", red_flagged=True)
    v.record("ABC", "0002-24-000001", red_flagged=False)

    assert v.get_verdict("XYZ", "0001-24-000001") is True
    assert v.get_verdict("ABC", "0002-24-000001") is False


# ── DiscoveryAttempts (S30/#894) ───────────────────────────────────────────

def _attempts(tmp_path):
    return DiscoveryAttempts(db_path=tmp_path / "attempts.db")


def test_get_latest_returns_none_for_a_never_attempted_symbol(tmp_path):
    a = _attempts(tmp_path)
    assert a.get_latest("XYZ") is None


def test_record_then_get_latest_round_trips(tmp_path):
    a = _attempts(tmp_path)
    a.record("XYZ", "UNVALIDATED", reason="vs_benchmark: underperformed", idea_url="https://x")

    latest = a.get_latest("XYZ")
    assert latest["verdict"] == "UNVALIDATED"
    assert latest["reason"] == "vs_benchmark: underperformed"
    assert latest["idea_url"] == "https://x"


def test_record_replaces_the_prior_attempt_for_the_same_symbol(tmp_path):
    """One row per symbol -- only the MOST RECENT attempt matters, same as
    VettedTickers' own replace-wholesale convention."""
    a = _attempts(tmp_path)
    a.record("XYZ", "UNVALIDATED", reason="first try")
    a.record("XYZ", "VALIDATED", reason="")

    assert a.get_latest("XYZ")["verdict"] == "VALIDATED"


def test_attempts_are_independent_per_symbol(tmp_path):
    a = _attempts(tmp_path)
    a.record("XYZ", "UNVALIDATED", reason="noise gate")
    a.record("ABC", "VALIDATED")

    assert a.get_latest("XYZ")["verdict"] == "UNVALIDATED"
    assert a.get_latest("ABC")["verdict"] == "VALIDATED"


# ── process_idea: per-attempt logging (S30/#894) ───────────────────────────

async def test_ticker_specific_dispatch_records_the_attempt(tmp_path):
    wl = _watchlist(tmp_path)
    attempt = RecordingAttempt()

    await process_idea(_ticker_idea("AAPL"), wl, RecordingGauntlet(), record_attempt_fn=attempt)

    assert len(attempt.calls) == 1
    assert attempt.calls[0]["symbol"] == "AAPL"
    assert attempt.calls[0]["verdict"] == "VALIDATED"


async def test_prefiltered_dispatch_records_an_attempt_per_candidate(tmp_path):
    wl = _watchlist(tmp_path)
    for sym in ["AAPL", "MSFT"]:
        wl.upsert(sym)
    attempt = RecordingAttempt()
    judge = FixedJudge(accepted=True)

    await process_idea(_pattern_idea(), wl, RecordingGauntlet(), judge_idea_fn=judge,
                        record_attempt_fn=attempt, candidate_limit=2)

    assert {c["symbol"] for c in attempt.calls} == {"AAPL", "MSFT"}


async def test_unvalidated_dispatch_records_the_failed_gates_reason(tmp_path):
    wl = _watchlist(tmp_path)
    attempt = RecordingAttempt()

    await process_idea(_ticker_idea("AAPL"), wl, UnvalidatedGauntlet(), record_attempt_fn=attempt)

    assert attempt.calls[0]["verdict"] == "UNVALIDATED"
    assert attempt.calls[0]["reason"] == "vs_benchmark: underperformed by 3.2%"


async def test_rejected_pattern_idea_never_records_an_attempt(tmp_path):
    """No candidate ticker was ever chosen -- nothing to key an attempt on,
    same reasoning as #894's issue scope note."""
    wl = _watchlist(tmp_path)
    attempt = RecordingAttempt()
    judge = FixedJudge(accepted=False, reason="too vague")

    await process_idea(_pattern_idea(), wl, RecordingGauntlet(), judge_idea_fn=judge,
                        record_attempt_fn=attempt)

    assert attempt.calls == []


async def test_no_record_attempt_fn_is_a_silent_no_op(tmp_path):
    """Default None must not raise -- matches record_activity_fn's own
    optional convention."""
    wl = _watchlist(tmp_path)
    results = await process_idea(_ticker_idea("AAPL"), wl, RecordingGauntlet())
    assert len(results) == 1


# ── rank_for_day_trading ─────────────────────────────────────────────────

def _bars(price: float, dollar_range_pct: float, volume: float, days: int = 25):
    """A synthetic daily-bars DataFrame with a fixed close, a fixed
    high-low range as a % of close, and a fixed volume every day --
    enough to drive rank_for_day_trading's liquidity/volatility scoring
    without touching real yfinance data."""
    import pandas as pd
    half_range = price * dollar_range_pct / 2
    return pd.DataFrame({
        "Open": [price] * days,
        "High": [price + half_range] * days,
        "Low": [price - half_range] * days,
        "Close": [price] * days,
        "Volume": [volume] * days,
    })


def test_rank_for_day_trading_orders_by_volatility_among_liquid_symbols():
    from cerebral.trading.discovery import rank_for_day_trading
    bars = {
        "CALM": _bars(price=100, dollar_range_pct=0.01, volume=1_000_000),   # $100M/day, low range
        "WILD": _bars(price=100, dollar_range_pct=0.08, volume=1_000_000),   # $100M/day, high range
    }

    ranked = rank_for_day_trading(list(bars), lambda sym, *a, **kw: bars[sym])

    assert ranked == ["WILD", "CALM"]


def test_rank_for_day_trading_drops_illiquid_symbols_outright():
    from cerebral.trading.discovery import rank_for_day_trading
    bars = {
        "THIN": _bars(price=100, dollar_range_pct=0.20, volume=1_000),  # huge range, $100K/day -- illiquid
        "SOLID": _bars(price=100, dollar_range_pct=0.02, volume=1_000_000),
    }

    ranked = rank_for_day_trading(list(bars), lambda sym, *a, **kw: bars[sym])

    assert ranked == ["SOLID"]


def test_rank_for_day_trading_drops_penny_stocks_below_min_price():
    from cerebral.trading.discovery import rank_for_day_trading
    bars = {"PENNY": _bars(price=1.5, dollar_range_pct=0.20, volume=10_000_000)}

    ranked = rank_for_day_trading(list(bars), lambda sym, *a, **kw: bars[sym])

    assert ranked == []


def test_rank_for_day_trading_skips_symbols_whose_fetch_fails():
    from cerebral.trading.discovery import rank_for_day_trading

    def flaky_fetch(symbol, *a, **kw):
        if symbol == "BROKEN":
            raise RuntimeError("network down")
        return _bars(price=50, dollar_range_pct=0.03, volume=1_000_000)

    ranked = rank_for_day_trading(["BROKEN", "OK"], flaky_fetch)

    assert ranked == ["OK"]


def test_prefilter_candidates_uses_rank_fn_when_given(tmp_path):
    """rank_fn (day-trade fitness) beats plain recency ordering when
    supplied -- the whole point of adding it."""
    wl = _watchlist(tmp_path)
    for sym in ["AAPL", "MSFT", "TSLA"]:
        wl.upsert(sym)

    ranked_order = ["TSLA", "AAPL", "MSFT"]
    candidates = wl.prefilter_candidates(
        _pattern_idea(), limit=2, rank_fn=lambda symbols: ranked_order,
    )

    assert candidates == ["TSLA", "AAPL"]


def test_prefilter_candidates_falls_back_to_universe_if_rank_fn_returns_nothing(tmp_path):
    """Every candidate failing its liquidity floor shouldn't mean zero
    candidates to try -- fall back to the unranked universe (watchlist
    entries first, known-liquid overflow after)."""
    wl = _watchlist(tmp_path)
    wl.upsert("AAPL")

    candidates = wl.prefilter_candidates(_pattern_idea(), limit=2, rank_fn=lambda symbols: [])

    assert candidates[0] == "AAPL"
    assert len(candidates) == 2
