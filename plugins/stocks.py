"""
stocks.py -- Fundamentals, SEC filings, notification-only IPO detection
(S24/#877). Follows plugins/markets.py's exact shape.

Tools: stock_fundamentals, sec_filings, sec_new_filings.

sec_filings/sec_new_filings use SEC EDGAR's real JSON/text APIs (the
ticker->CIK map at sec.gov/files/company_tickers.json and the per-company
submissions JSON at data.sec.gov/submissions/) rather than scraping the
legacy browse-edgar XML endpoint, whose schema is not stable enough to
parse reliably. sec_new_filings is notification-only per decision #37 --
it never calls run_gauntlet; the user decides whether to trade an IPO.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional

import yfinance as yf

from cerebral.mcp.orchestrator import Tool, ToolResult
from plugins.http_client import _default_fetch

logger = logging.getLogger(__name__)

PLUGIN_NAME = "stocks"

# ADR-0005 / Issue #44 -- yfinance/SEC EDGAR calls are external, read-only,
# public-internet lookups. Matches plugins/markets.py's declaration.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
})

# SEC's fair-access policy requires a real identifying User-Agent.
SEC_UA = "OpenMind/1.0 (contact: openmind@example.com)"
_SEC_MIN_INTERVAL = 0.1  # 10 req/s fair-access limit

FetchFn = Callable[..., Awaitable[Any]]
# (event_type, payload) -> None. Optional -- a plugin instance with no
# notify_fn simply logs instead of raising, matching this codebase's
# pattern for optional seams not yet wired by main.py (e.g. token
# providers before #153's wiring).
NotifyFn = Callable[[str, dict], None]


class StocksPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: FetchFn | None = None, notify_fn: NotifyFn | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch
        self._notify = notify_fn
        self._last_sec_request = 0.0
        self._cik_map: dict[str, str] | None = None  # ticker -> zero-padded CIK, lazily built

    def list_tools(self) -> list[Tool]:
        symbol_prop = {"type": "string", "description": "Ticker symbol (e.g. 'AAPL')."}
        return [
            Tool(
                name="stock_fundamentals",
                description="Sector, market cap, beta, and quarterly financials for a ticker (yfinance).",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"symbol": symbol_prop},
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="sec_filings",
                description="Recent 10-Q/10-K filing text for a ticker, via SEC EDGAR.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "symbol": symbol_prop,
                        "count": {"type": "integer", "description": "Max filings to return (default 3)."},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="sec_new_filings",
                description=(
                    "Notification-only: scans today's EDGAR daily filing index for S-1/424B4 "
                    "(IPO registration/pricing) filings and emits a notification for each. "
                    "Never creates or trades a strategy."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "stock_fundamentals":
            return await self._stock_fundamentals(args)
        if tool_name == "sec_filings":
            return await self._sec_filings(args)
        if tool_name == "sec_new_filings":
            return await self._sec_new_filings()
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # -- stock_fundamentals ---------------------------------------------

    async def _stock_fundamentals(self, args: dict) -> ToolResult:
        import json
        symbol = (args.get("symbol") or "").strip().upper()
        if not symbol:
            return ToolResult(content="'symbol' is required", is_error=True)
        try:
            data = await asyncio.to_thread(self._fetch_fundamentals_sync, symbol)
        except Exception as exc:
            logger.error("[stocks] fundamentals lookup for '%s' failed: %s", symbol, exc)
            return ToolResult(content=f"Fundamentals lookup failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps(data))

    def _fetch_fundamentals_sync(self, symbol: str) -> dict:
        """yfinance is a blocking library -- run off-thread (asyncio.to_thread),
        matching how run_strategy_tick's own fetch_ohlcv import is isolated."""
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        qf = ticker.quarterly_financials
        quarterly = {}
        if qf is not None and not qf.empty:
            quarterly = {str(k): {str(kk): (None if _is_nan(vv) else float(vv)) for kk, vv in v.items()}
                         for k, v in qf.to_dict(orient="index").items()}
        return {
            "symbol": symbol,
            "sector": info.get("sector"),
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "quarterly_financials": quarterly,
        }

    # -- sec_filings ------------------------------------------------------

    async def _sec_get_text(self, url: str) -> str:
        await self._respect_sec_rate_limit()
        response = await self._fetch("GET", url, headers={"User-Agent": SEC_UA})
        # http_client._default_fetch returns {"status", "body", "headers"};
        # markets.py's own _default_fetch returns the parsed body directly --
        # this plugin uses http_client's variant, so unwrap "body".
        return response["body"] if isinstance(response, dict) and "body" in response else response

    async def _respect_sec_rate_limit(self) -> None:
        now = time.monotonic()
        wait = _SEC_MIN_INTERVAL - (now - self._last_sec_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_sec_request = time.monotonic()

    async def _resolve_cik(self, symbol: str) -> Optional[str]:
        """Ticker -> zero-padded 10-digit CIK, via SEC's own static ticker map
        (sec.gov/files/company_tickers.json) -- an official JSON mapping,
        far more reliable than scraping the legacy browse-edgar XML search,
        whose schema this plugin's first draft found impossible to parse
        with any confidence (see PR #888's closing comment)."""
        if self._cik_map is None:
            raw = await self._sec_get_text("https://www.sec.gov/files/company_tickers.json")
            import json
            data = json.loads(raw) if isinstance(raw, str) else raw
            self._cik_map = {
                str(row["ticker"]).upper(): str(row["cik_str"]).zfill(10)
                for row in data.values()
            }
        return self._cik_map.get(symbol.upper())

    async def _sec_filings(self, args: dict) -> ToolResult:
        import json
        symbol = (args.get("symbol") or "").strip().upper()
        if not symbol:
            return ToolResult(content="'symbol' is required", is_error=True)
        count = int(args.get("count") or 3)

        try:
            cik = await self._resolve_cik(symbol)
            if cik is None:
                return ToolResult(content=f"No SEC CIK found for symbol '{symbol}'", is_error=True)

            submissions_raw = await self._sec_get_text(f"https://data.sec.gov/submissions/CIK{cik}.json")
            submissions = json.loads(submissions_raw) if isinstance(submissions_raw, str) else submissions_raw
            recent = submissions.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            dates = recent.get("filingDate", [])
            docs = recent.get("primaryDocument", [])

            accepted = {"10-Q", "10-K"}
            results: List[dict] = []
            for form, accession, filing_date, doc in zip(forms, accessions, dates, docs):
                if form not in accepted:
                    continue
                accession_no_dashes = accession.replace("-", "")
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{accession_no_dashes}/{doc}"
                )
                results.append({
                    "form": form,
                    "accession": accession,
                    "filing_date": filing_date,
                    "url": url,
                })
                if len(results) >= count:
                    break

            return ToolResult(content=json.dumps({"symbol": symbol, "filings": results}))
        except Exception as exc:
            logger.error("[stocks] sec_filings for '%s' failed: %s", symbol, exc)
            return ToolResult(content=f"SEC filings lookup failed: {exc}", is_error=True)

    # -- sec_new_filings (notification-only, decision #37) ----------------

    async def _sec_new_filings(self) -> ToolResult:
        import json
        today = datetime.now(timezone.utc).date()
        quarter = (today.month - 1) // 3 + 1
        index_url = (
            f"https://www.sec.gov/Archives/edgar/daily-index/{today.year}/QTR{quarter}/"
            f"master.{today.strftime('%Y%m%d')}.idx"
        )
        try:
            text = await self._sec_get_text(index_url)
        except Exception as exc:
            logger.info("[stocks] no EDGAR daily index for %s yet: %s", today.isoformat(), exc)
            return ToolResult(content=json.dumps({"status": "no_index", "date": today.isoformat()}))

        accepted = {"S-1", "424B4"}
        new_filings: List[dict] = []
        # Format: header rows, then "CIK|Company Name|Form Type|Date Filed|Filename"
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) != 5:
                continue
            cik, company, form, filing_date, filename = parts
            if form not in accepted or not cik.strip().isdigit():
                continue
            new_filings.append({
                "cik": cik.strip(),
                "company": company.strip(),
                "form": form.strip(),
                "filing_date": filing_date.strip(),
                "url": f"https://www.sec.gov/Archives/{filename.strip()}",
            })

        for filing in new_filings:
            message = f"IPO filing detected: {filing['form']} for {filing['company']}"
            if self._notify is not None:
                self._notify("ipo_alert", {"filing": filing, "message": message})
            else:
                logger.info("[stocks] %s (no notify_fn wired -- log only)", message)

        return ToolResult(content=json.dumps({
            "status": "checked",
            "date": today.isoformat(),
            "count": len(new_filings),
            "filings": new_filings,
        }))


def _is_nan(value: Any) -> bool:
    try:
        return value != value  # NaN is the only float that isn't equal to itself
    except Exception:
        return False


def create(fetch_fn: FetchFn | None = None, notify_fn: NotifyFn | None = None) -> StocksPlugin:
    return StocksPlugin(fetch_fn=fetch_fn, notify_fn=notify_fn)
