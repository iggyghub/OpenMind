"""Autonomous discovery loop core logic (S27/#880, decisions #32-36, #44).

Pure and duck-typed, like live_tick.py -- cerebral/ must not import
plugins/, so sourcing (web_search/navigate), judging, and dispatching to
run_gauntlet all come in as injected async callables. The real ones are
bound by plugins/scheduler.py, which already sits on the plugins/ side of
that boundary.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional

from cerebral.paths import data_dir
from cerebral.trading_ideas import Idea, _run_tally

logger = logging.getLogger(__name__)

_DB_PATH = data_dir() / "discovery_watchlist.db"

# Recognizes a ticker-SPECIFIC idea (decision #36) -- not a universe sweep
# list. A real ticker missing from this set still counts as pattern-general
# and goes through the pre-filter, which is the safe default (screen first,
# never guess a symbol out of arbitrary text).
_KNOWN_TICKERS = frozenset({
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AMD",
    "NFLX", "INTC", "IBM", "ORCL", "CRM", "ADBE", "PYPL", "UBER", "ABNB",
    "SPY", "QQQ", "DIA",
    # Low-priced/liquid additions (2026-09-01): the original set was
    # entirely $75-580/share mega-caps -- TRADING.md's own first decision
    # was "penny stocks first," never acted on. Fractional-share sizing
    # (#961 follow-up) means price no longer blocks a trade from
    # executing at any capital level, but a genuinely cheap, liquid name
    # still matters for whatever techniques specifically target low-float/
    # low-price behavior. Verified real and liquid (10-60M avg daily
    # volume) against live data the same day this was added, not guessed:
    # F ($13.94), NIO ($4.23), SOFI ($17.88), AAL ($13.43), SNAP ($5.55),
    # PLUG ($2.16), RIOT ($19.00), MARA ($10.77), VALE ($15.09),
    # ITUB ($7.61), GRAB ($3.54), BBD ($3.28), PBR ($19.35), RIG ($5.81),
    # LYFT ($16.90), PARA ($1.10).
    "F", "NIO", "SOFI", "AAL", "SNAP", "PLUG", "RIOT", "MARA", "VALE",
    "ITUB", "GRAB", "BBD", "PBR", "RIG", "LYFT", "PARA",
})


class DiscoveryWatchlist:
    """SQLite-backed growing ticker watchlist (decision #36) -- not a
    periodic full-universe sweep. Same db_path-injection convention as
    StrategyStore/ForwardRecord so tests stay isolated."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path if db_path is not None else _DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS discovery_watchlist (
                symbol         TEXT PRIMARY KEY,
                first_seen     TEXT NOT NULL,
                source         TEXT NOT NULL DEFAULT '',
                last_screened  TEXT NOT NULL
            )
        """)
        self._con.commit()

    def upsert(self, symbol: str, source: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self._con.execute(
            "SELECT symbol FROM discovery_watchlist WHERE symbol = ?", (symbol,)
        ).fetchone()
        if existing is None:
            self._con.execute(
                "INSERT INTO discovery_watchlist (symbol, first_seen, source, last_screened) "
                "VALUES (?, ?, ?, ?)",
                (symbol, now, source, now),
            )
        else:
            self._con.execute(
                "UPDATE discovery_watchlist SET last_screened = ? WHERE symbol = ?",
                (now, symbol),
            )
        self._con.commit()

    def symbols(self) -> List[str]:
        rows = self._con.execute(
            "SELECT symbol FROM discovery_watchlist ORDER BY last_screened DESC"
        ).fetchall()
        return [r["symbol"] for r in rows]

    def prefilter_candidates(
        self, idea: Idea, limit: int = 3, rank_fn: Optional[RankCandidatesFn] = None,
    ) -> List[str]:
        """Cheap, in-process, TRUSTED-code pre-filter -- deliberately not
        the sandbox (S25/#878's own sub-decision 2: the sandbox exists for
        untrusted *generated strategy code*, not for Felix's own logic
        scanning a symbol list, and batching untrusted evaluation through
        argv is mechanically impossible on Windows regardless).

        The candidate universe is the growing watchlist PLUS the ~20-symbol
        known-liquid set extract_ticker() already trusts (never an
        unbounded universe sweep -- decision #36 stands), watchlist entries
        first. Fix (2026-08-26): this used to return ONLY the watchlist
        once it had anything on it at all, permanently excluding the
        known-liquid set from that point on -- confirmed live, a watchlist
        seeded with 3 symbols stayed stuck at exactly those 3 for over a
        day of real passes, since nothing pattern-general ever got a
        chance to introduce a new one. Always including the known-liquid
        set keeps fresh candidates in the running so the pool can actually
        widen over time (each one dispatched here gets upsert()'d into the
        watchlist by process_idea, same as any other candidate).

        `rank_fn` (optional, injected -- see rank_for_day_trading) orders
        that universe by actual day-trading fitness (liquidity + intraday
        range) instead of raw watchlist-then-alphabetical order. Omitted
        (the default, and every pre-existing caller/test) keeps watchlist
        entries first, in their natural recency order, known-liquid
        overflow after. A rank_fn that returns nothing (e.g. every
        candidate failed its liquidity floor) falls back to the unranked
        universe rather than returning zero candidates.

        Fix (2026-08-31): the 2026-08-26 fix above stopped helping the
        moment the watchlist grew past `limit` entries -- `universe[:limit]`
        is just `existing[:limit]` once len(existing) >= limit, so overflow
        became permanently unreachable again, silently. Confirmed live: a
        watchlist that reached 14 entries within days stayed at exactly
        those 14 for the following three, every idea's top candidates
        being whichever 3 watchlist symbols were screened most recently.
        Reserving the LAST slot for the next un-seen known-liquid symbol
        (only once existing has filled every other slot) keeps the pool
        widening a little every pass without diluting the existing
        symbols' own priority -- they still fill every slot but the last.
        """
        existing = self.symbols()
        overflow = sorted(_KNOWN_TICKERS - set(existing))
        universe = existing + overflow
        if rank_fn is not None:
            ranked = rank_fn(universe)
            if ranked:
                return ranked[:limit]
        if overflow and limit > 1 and len(existing) >= limit:
            # limit > 1, not >= 1: at limit=1 there's no way to reserve an
            # overflow slot without dropping the watchlist's own top entry
            # entirely, which would invert "watchlist entries first" rather
            # than just widening around the edges of it.
            return existing[:limit - 1] + overflow[:1]
        return universe[:limit]

    def close(self) -> None:
        self._con.close()


class DiscoveryAttempts:
    """Persisted per-attempt gauntlet log (S30/#894).

    Closes the gap S29 (decision #49) found: `process_idea` used to only
    ever record a "dispatched" activity entry via `record_activity_fn` --
    the actual `run_gauntlet_fn` outcome (VALIDATED/UNVALIDATED + which
    gate failed and why) was computed, returned, and then discarded. This
    is that outcome, persisted. Same one-row-per-symbol/db_path-injection
    convention as VettedTickers: only the MOST RECENT attempt matters for
    "is this ticker currently screened or actually rejected" -- a symbol's
    row is replaced wholesale on each new attempt, not appended.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path if db_path is not None else data_dir() / "discovery_attempts.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS discovery_attempts (
                symbol       TEXT PRIMARY KEY,
                attempted_at TEXT NOT NULL,
                verdict      TEXT NOT NULL,
                reason       TEXT NOT NULL DEFAULT '',
                idea_url     TEXT NOT NULL DEFAULT ''
            )
        """)
        self._con.commit()

    def record(self, symbol: str, verdict: str, reason: str = "", idea_url: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute(
            "INSERT INTO discovery_attempts (symbol, attempted_at, verdict, reason, idea_url) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET "
            "attempted_at=excluded.attempted_at, verdict=excluded.verdict, "
            "reason=excluded.reason, idea_url=excluded.idea_url",
            (symbol, now, verdict, reason, idea_url),
        )
        self._con.commit()

    def get_latest(self, symbol: str) -> Optional[dict]:
        row = self._con.execute(
            "SELECT symbol, attempted_at, verdict, reason, idea_url "
            "FROM discovery_attempts WHERE symbol = ?", (symbol,),
        ).fetchone()
        return dict(row) if row is not None else None

    def close(self) -> None:
        self._con.close()


def _attempt_outcome(result: dict) -> "tuple[str, str]":
    """Extracts (verdict, reason) from one run_gauntlet_fn return value.

    Two real shapes reach this: the production one from
    plugins/scheduler.py's run_gauntlet_fn (`{"ticker", "is_error",
    "result": <StrategyCard JSON string>}`) and the flat test-fake shape
    already used throughout test_trading_discovery.py (`{"ticker",
    "verdict"}`, no nesting). Handling both means the existing fakes don't
    need to change shape just because this function now also reads them.

    On UNVALIDATED, the reason names the first failed gate and its own
    `details` string (e.g. "vs_benchmark: underperformed by 3.2%") -- not
    just "failed", per #894's acceptance criteria.
    """
    if "verdict" in result:
        verdict = result.get("verdict", "")
        gates = result.get("gates", [])
    elif "result" in result:
        try:
            data = json.loads(result["result"])
        except (TypeError, ValueError):
            return "ERROR", str(result.get("result", ""))[:200]
        verdict = data.get("verdict", "")
        gates = data.get("gates", [])
    else:
        return "UNKNOWN", ""

    if verdict != "UNVALIDATED":
        return verdict, ""

    for gate in gates:
        if not gate.get("passed", True):
            name = gate.get("name", "gate")
            details = gate.get("details", "")
            return verdict, f"{name}: {details}" if details else name
    return verdict, ""


def extract_ticker(idea: Idea) -> Optional[str]:
    """A ticker-specific idea (decision #36) names a specific stock in its
    claim or page title. Deliberately conservative: an unrecognized
    all-caps token is NOT assumed to be a ticker -- screen by default
    rather than guess."""
    text = f"{idea.claim_text or ''} {idea.page_title or ''}"
    for match in re.finditer(r"\b[A-Z]{1,5}\b", text):
        if match.group(0) in _KNOWN_TICKERS:
            return match.group(0)
    return None


RunGauntletFn = Callable[[Idea, str], Awaitable[dict]]
JudgeIdeaFn = Callable[[Idea], Awaitable["tuple[bool, str]"]]
RecordActivityFn = Callable[[str, dict], Awaitable[None]]
FetchOhlcvFn = Callable[..., "Any"]
RankCandidatesFn = Callable[[List[str]], List[str]]


def rank_for_day_trading(
    symbols: List[str],
    fetch_ohlcv_fn: FetchOhlcvFn,
    lookback_days: int = 20,
    min_price: float = 5.0,
    min_dollar_volume: float = 5_000_000,
) -> List[str]:
    """Ranks candidate symbols by day-trading fitness: liquid enough to
    enter/exit without moving the price, volatile enough to actually have
    an intraday signal to trade. Neither property was checked anywhere
    before this -- prefilter_candidates just returned the most-recently-
    screened tickers (or an alphabetical static list on cold start), which
    is not a suitability judgement.

    A symbol below `min_dollar_volume` (avg daily $ volume) is dropped
    outright, not ranked last -- illiquid names aren't day-tradeable
    regardless of how much they move. Survivors are ranked by average
    daily range as a % of close (a cheap ATR proxy -- no prior-close true
    range needed for a same-day ranking pass), most volatile first. A
    symbol whose fetch fails or returns no data is silently skipped -- a
    data gap isn't a suitability signal either way."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days * 2)  # padding for weekends/holidays
    scored: List["tuple[str, float]"] = []
    for symbol in symbols:
        try:
            df = fetch_ohlcv_fn(symbol, start.isoformat(), end.isoformat(), interval="1d")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.tail(lookback_days)
        if df["Close"].mean() < min_price:
            continue
        if (df["Close"] * df["Volume"]).mean() < min_dollar_volume:
            continue
        range_pct = ((df["High"] - df["Low"]) / df["Close"]).mean()
        scored.append((symbol, range_pct))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [symbol for symbol, _ in scored]
RecordAttemptFn = Callable[[dict], Awaitable[None]]


async def _record_attempt(
    record_attempt_fn: Optional[RecordAttemptFn], symbol: str, idea: Idea, result: dict,
) -> None:
    if record_attempt_fn is None:
        return
    verdict, reason = _attempt_outcome(result)
    await record_attempt_fn({
        "symbol": symbol, "verdict": verdict, "reason": reason,
        "idea_url": idea.source_url or "",
    })


async def process_idea(
    idea: Idea,
    watchlist: DiscoveryWatchlist,
    run_gauntlet_fn: RunGauntletFn,
    judge_idea_fn: Optional[JudgeIdeaFn] = None,
    record_activity_fn: Optional[RecordActivityFn] = None,
    record_attempt_fn: Optional[RecordAttemptFn] = None,
    rank_fn: Optional[RankCandidatesFn] = None,
    candidate_limit: int = 3,
) -> List[dict]:
    """One sourced idea -> zero or more run_gauntlet dispatches.

    Ticker-specific ideas (decision #36) skip judge_idea and the
    pre-filter entirely and go straight to run_gauntlet_fn -- screening
    exists to narrow an unbounded universe of pattern-general ideas down
    to a few candidates; an idea that already names its own ticker has
    nothing left to narrow.

    `record_attempt_fn` (S30/#894) persists each dispatch's real gauntlet
    outcome via DiscoveryAttempts -- separate from `record_activity_fn`,
    which only ever logs a "dispatched" activity-feed entry before the
    gauntlet has even run and is unrelated to this.
    """
    ticker = extract_ticker(idea)

    if ticker is not None:
        result = await run_gauntlet_fn(idea, ticker)
        watchlist.upsert(ticker, source=idea.provenance)
        if record_activity_fn is not None:
            await record_activity_fn("activity", {
                "source": "discovery", "status": "dispatched",
                "ticker": ticker, "idea_url": idea.source_url,
            })
        await _record_attempt(record_attempt_fn, ticker, idea, result)
        return [result]

    if judge_idea_fn is not None:
        accepted, reason = await judge_idea_fn(idea)
    else:
        accepted, reason = True, "no judge configured"

    if record_activity_fn is not None:
        await record_activity_fn("activity", {
            "source": "discovery",
            "status": "accepted" if accepted else "rejected",
            "reason": reason, "idea_url": idea.source_url,
        })

    if not accepted:
        logger.info("[discovery] rejected idea (%s): %s", idea.source_url, reason)
        return []

    # S41: discovery candidate limit bias
    tally_success, tally_pos, tally_total = _run_tally(idea.claim_text)
    candidate_limit_before = candidate_limit
    if tally_success and tally_total > 0:
        pct = tally_pos / tally_total
        if pct >= 0.6:
            candidate_limit += 1
        elif pct <= 0.4:
            candidate_limit = max(1, candidate_limit - 1)

    if tally_success and record_activity_fn is not None:
        await record_activity_fn("activity", {
            "source": "trading_tally",
            "claim": idea.claim_text,
            "positive": tally_pos,
            "total": tally_total,
            "candidate_limit_before": candidate_limit_before,
            "candidate_limit_after": candidate_limit,
        })

    results: List[dict] = []
    for candidate in watchlist.prefilter_candidates(idea, limit=candidate_limit, rank_fn=rank_fn):
        result = await run_gauntlet_fn(idea, candidate)
        watchlist.upsert(candidate, source=idea.provenance)
        await _record_attempt(record_attempt_fn, candidate, idea, result)
        results.append(result)
    return results


async def run_discovery_pass(
    ideas: List[Idea],
    watchlist: DiscoveryWatchlist,
    run_gauntlet_fn: RunGauntletFn,
    judge_idea_fn: Optional[JudgeIdeaFn] = None,
    record_activity_fn: Optional[RecordActivityFn] = None,
    record_attempt_fn: Optional[RecordAttemptFn] = None,
    rank_fn: Optional[RankCandidatesFn] = None,
    candidate_limit: int = 3,
) -> List[dict]:
    """One discovery-loop tick: every already-sourced idea, processed in
    turn. Sourcing (web_search/navigate) is the caller's job -- kept out
    of this module so the screening/dispatch logic here stays testable
    with plain fixtures, matching run_strategy_tick's own separation of
    concerns in live_tick.py."""
    results: List[dict] = []
    for idea in ideas:
        results.extend(await process_idea(
            idea, watchlist, run_gauntlet_fn,
            judge_idea_fn=judge_idea_fn, record_activity_fn=record_activity_fn,
            record_attempt_fn=record_attempt_fn, rank_fn=rank_fn,
            candidate_limit=candidate_limit,
        ))
    return results


_VETTED_DB_PATH = data_dir() / "vetted_tickers.db"


class VettedTickers:
    """Fundamentals red-flag gate vetting record (S28/#881, decision #45).

    Keyed by symbol + the SEC filing accession number that was actually
    scanned, not just symbol -- so a genuinely NEW filing since the ticker
    was last checked re-triggers the scan, while re-graduating (or
    re-checking) an already-vetted ticker on the SAME filing skips the
    expensive fetch+LLM-scan and reuses the remembered verdict. One row
    per symbol (a symbol's vetting record is replaced wholesale on each
    real scan, not appended) -- only the latest filing's verdict matters
    for a graduation decision happening right now.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path if db_path is not None else _VETTED_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS vetted_tickers (
                symbol            TEXT PRIMARY KEY,
                accession_number  TEXT NOT NULL,
                vetted_at         TEXT NOT NULL,
                red_flagged       INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._con.commit()

    def get_verdict(self, symbol: str, accession_number: str) -> Optional[bool]:
        """Returns the remembered red_flagged verdict if `symbol` was
        already vetted on EXACTLY this accession number, else None (never
        vetted, or vetted on a since-superseded filing -- both mean "do a
        real scan")."""
        row = self._con.execute(
            "SELECT accession_number, red_flagged FROM vetted_tickers WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if row is None or row["accession_number"] != accession_number:
            return None
        return bool(row["red_flagged"])

    def record(self, symbol: str, accession_number: str, red_flagged: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute(
            "INSERT INTO vetted_tickers (symbol, accession_number, vetted_at, red_flagged) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET "
            "accession_number=excluded.accession_number, "
            "vetted_at=excluded.vetted_at, red_flagged=excluded.red_flagged",
            (symbol, accession_number, now, int(red_flagged)),
        )
        self._con.commit()

    def close(self) -> None:
        self._con.close()
