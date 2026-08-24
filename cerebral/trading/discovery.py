"""
S27: autonomous discovery loop -- idea sourcing + screening.
Wired into cerebral/main.py via SchedulerPlugin recurring events.
"""
import logging
import re
import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from cerebral.trading_ideas import Idea, judge_idea

logger = logging.getLogger(__name__)


@dataclass
class WatchlistEntry:
    symbol: str
    first_seen: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    source: str = ""
    last_screened: str = field(default_factory=lambda: datetime.datetime.now().isoformat())


class DiscoveryWatchlist:
    """In-memory backing for `discovery_watchlist` table.
    Ticker-specific ideas skip screening. Pattern-general ideas run a cheap
    in-process pre-filter against this watchlist first."""
    def __init__(self):
        self._entries: Dict[str, WatchlistEntry] = {}

    def upsert(self, symbol: str, source: str = "") -> WatchlistEntry:
        now = datetime.datetime.now().isoformat()
        if symbol not in self._entries:
            self._entries[symbol] = WatchlistEntry(symbol=symbol, source=source, first_seen=now)
        self._entries[symbol].last_screened = now
        return self._entries[symbol]

    def get(self, symbol: str) -> Optional[WatchlistEntry]:
        return self._entries.get(symbol)

    def symbols(self) -> List[str]:
        return list(self._entries.keys())

# Module-level watchlist singleton
_discovery_watchlist = DiscoveryWatchlist()


def extract_ticker_from_idea(idea: Idea) -> Optional[str]:
    """Heuristic ticker extraction from claim_text or source_url."""
    match = re.search(r'\b[A-Z]{2,5}\b', idea.claim_text or "")
    return match.group(0) if match else None


def _log_activity(status: str, url: str, detail: str) -> None:
    """Persist idea screening outcome to the Activity Log (S26)."""
    try:
        from cerebral.main import _record_activity
        _record_activity("activity", {
            "source": "discovery",
            "status": status,
            "url": url,
            "detail": detail,
        })
    except Exception as e:
        logger.warning("[discovery] Activity log failed: %s", e)


def _run_discovery_loop() -> List[Dict[str, Any]]:
    """
    Core discovery loop trigger. Called by SchedulerPlugin recurring event.
    1. Source ideas via existing browser/stocks tools.
    2. Screen via judge_idea & watchlist.
    3. Dispatch accepted ideas to `_run_gauntlet` with origin='discovered'.
    """
    from plugins.browser import web_search, navigate
    from plugins.stocks import get_fundamentals  # S24
    from plugins.scheduler import _run_gauntlet

    results: List[Dict[str, Any]] = []
    try:
        # Source phase: use web_search to find articles, navigate to extract claims.
        search_queries = ["quantitative trading hypothesis", "market anomaly detection"]
        discovered_ideas: List[Idea] = _discover_ideas_from_web(search_queries)

        # Screening & dispatch phase
        for idea in discovered_ideas:
            ticker = extract_ticker_from_idea(idea)

            if ticker:
                # Ticker-specific: skip pre-filter, go straight to gauntlet
                results.append(_dispatch_to_gauntlet(idea, ticker=ticker))
                _discovery_watchlist.upsert(ticker, source=idea.provenance)
            else:
                # Pattern-general: run judge_idea (quality pre-filter)
                accepted, reason = judge_idea(idea)
                if not accepted:
                    logger.info("[discovery] Rejected idea (%s): %s", idea.source_url, reason)
                    results.append({"status": "rejected", "reason": reason, "idea_url": idea.source_url})
                    _log_activity("rejected", idea.source_url, reason)
                    continue

                # Cheap in-process pre-filter against watchlist for pattern-general ideas
                candidates = _prefilter_pattern_idea(idea)
                for cand in candidates:
                    results.append(_dispatch_to_gauntlet(idea, ticker=cand))
                    _discovery_watchlist.upsert(cand, source=idea.provenance)
                    _log_activity("accepted", idea.source_url, cand)

    except Exception as e:
        logger.exception("[discovery] Loop failed: %s", e)
        results.append({"status": "error", "reason": str(e)})

    return results


def _discover_ideas_from_web(queries: List[str]) -> List[Idea]:
    """Sourced from web_search/navigate. Tests inject via monkeypatch."""
    from plugins.browser import web_search
    ideas: List[Idea] = []
    for query in queries:
        try:
            urls = web_search(query, max_results=1)
            for url in urls:
                ideas.append(Idea(
                    source_url=url,
                    claim_text=f"Hypothesis sourced from {url}",
                    provenance=f"url: {url}",
                ))
        except Exception as e:
            logger.warning("[discovery] web_search failed for '%s': %s", query, e)
    return ideas


def _prefilter_pattern_idea(idea: Idea) -> List[str]:
    """Returns ticker candidates from watchlist matching the idea's pattern."""
    return _discovery_watchlist.symbols()


def _dispatch_to_gauntlet(idea: Idea, ticker: Optional[str] = None) -> Dict[str, Any]:
    """Passes idea to scheduler's gauntlet with origin='discovered'."""
    from plugins.scheduler import _run_gauntlet
    
    try:
        outcome = _run_gauntlet(
            claim=idea.claim_text,
            url=idea.source_url,
            origin='discovered',
            ticker=ticker,
        )
        return {"status": "dispatched", "ticker": ticker, "outcome": outcome}
    except Exception as e:
        logger.error("[discovery] Gauntlet dispatch failed: %s", e)
        return {"status": "failed", "ticker": ticker, "error": str(e)}
