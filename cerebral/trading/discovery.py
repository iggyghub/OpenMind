"""Autonomous discovery loop core logic (S27/#880, decisions #32-36, #44).

Pure and duck-typed, like live_tick.py -- cerebral/ must not import
plugins/, so sourcing (web_search/navigate), judging, and dispatching to
run_gauntlet all come in as injected async callables. The real ones are
bound by plugins/scheduler.py, which already sits on the plugins/ side of
that boundary.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from cerebral.paths import data_dir
from cerebral.trading_ideas import Idea

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

    def prefilter_candidates(self, idea: Idea, limit: int = 3) -> List[str]:
        """Cheap, in-process, TRUSTED-code pre-filter -- deliberately not
        the sandbox (S25/#878's own sub-decision 2: the sandbox exists for
        untrusted *generated strategy code*, not for Felix's own logic
        scanning a symbol list, and batching untrusted evaluation through
        argv is mechanically impossible on Windows regardless).

        Today: the most recently screened tickers already on the growing
        watchlist. A real ranking heuristic (liquidity/momentum matched
        against the idea's own text) is a deepening this slice's
        acceptance criteria don't require -- returning the existing
        watchlist honestly, capped, rather than inventing an unvalidated
        scoring function.

        An empty watchlist falls back to _KNOWN_TICKERS (fix, 2026-08-25):
        the watchlist only grows via upsert() inside a dispatch, and a
        pattern-general idea only dispatches via this method's own return
        value -- on a cold start (or after every prior candidate has
        already been fully explored) that's a real deadlock, not a
        theoretical one: the first live discovery pass against the real
        OpenClaw web_search wiring sourced 6 real ideas and dispatched
        zero, watchlist still empty. Falling back to the same ~20-symbol
        known-liquid set extract_ticker() already trusts (never an
        unbounded universe sweep -- decision #36 stands) breaks the
        deadlock without changing what "trusted symbol" means anywhere
        else in this module.
        """
        existing = self.symbols()
        if existing:
            return existing[:limit]
        return sorted(_KNOWN_TICKERS)[:limit]

    def close(self) -> None:
        self._con.close()


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


async def process_idea(
    idea: Idea,
    watchlist: DiscoveryWatchlist,
    run_gauntlet_fn: RunGauntletFn,
    judge_idea_fn: Optional[JudgeIdeaFn] = None,
    record_activity_fn: Optional[RecordActivityFn] = None,
) -> List[dict]:
    """One sourced idea -> zero or more run_gauntlet dispatches.

    Ticker-specific ideas (decision #36) skip judge_idea and the
    pre-filter entirely and go straight to run_gauntlet_fn -- screening
    exists to narrow an unbounded universe of pattern-general ideas down
    to a few candidates; an idea that already names its own ticker has
    nothing left to narrow.
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

    results: List[dict] = []
    for candidate in watchlist.prefilter_candidates(idea):
        results.append(await run_gauntlet_fn(idea, candidate))
        watchlist.upsert(candidate, source=idea.provenance)
    return results


async def run_discovery_pass(
    ideas: List[Idea],
    watchlist: DiscoveryWatchlist,
    run_gauntlet_fn: RunGauntletFn,
    judge_idea_fn: Optional[JudgeIdeaFn] = None,
    record_activity_fn: Optional[RecordActivityFn] = None,
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
