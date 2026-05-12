"""
Markets MCP plugin — Issue #25.

Tools: market_price, market_quote.

Read-only price + quote lookup. Auto-detects crypto vs stock by symbol:
  - Crypto (BTC, ETH, …) → CoinGecko (no API key)
  - Otherwise → Yahoo Finance public chart endpoint (no API key)

An explicit `asset_type` of "crypto" or "stock" overrides auto-detection.

The default fetch_fn tries aiohttp then httpx — same pattern as
plugins/wikipedia.py. Tests inject a stub.
"""
import json
import logging
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "markets"

# ADR-0005 / Issue #44 — market_price / market_quote read price data from
# CoinGecko or Yahoo Finance over the public internet.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
})

_COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"

# Common crypto tickers — used for auto-detection only. Anything not in this
# set is assumed to be a stock; pass `asset_type="crypto"` to override.
_KNOWN_CRYPTO = {
    "BTC", "ETH", "DOGE", "ADA", "SOL", "XRP", "LTC", "DOT", "AVAX", "BNB",
    "MATIC", "USDT", "USDC", "SHIB", "TRX", "LINK", "ATOM", "UNI", "BCH",
    "XLM", "ETC", "FIL", "NEAR", "ALGO",
}

FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, params=params, json=json) as resp:
                resp.raise_for_status()
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


class MarketsPlugin:
    name = PLUGIN_NAME

    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetch = fetch_fn or _default_fetch

    def list_tools(self) -> list[Tool]:
        symbol_prop = {
            "type": "string",
            "description": "Ticker or coin symbol (e.g. 'AAPL', 'BTC').",
        }
        type_prop = {
            "type": "string",
            "description": "Override auto-detection: 'crypto' or 'stock'.",
            "enum": ["crypto", "stock"],
        }
        return [
            Tool(
                name="market_price",
                description="Get the latest price for a stock ticker or crypto symbol.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"symbol": symbol_prop, "asset_type": type_prop},
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="market_quote",
                description=(
                    "Get a full quote (price, 24h change, market cap) for a stock "
                    "ticker or crypto symbol."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"symbol": symbol_prop, "asset_type": type_prop},
                    "required": ["symbol"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "market_price":
            return await self._lookup(args, full=False)
        if tool_name == "market_quote":
            return await self._lookup(args, full=True)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _resolve_asset_type(self, symbol: str, override: str | None) -> str:
        if override in ("crypto", "stock"):
            return override
        return "crypto" if symbol.upper() in _KNOWN_CRYPTO else "stock"

    async def _lookup(self, args: dict, *, full: bool) -> ToolResult:
        symbol = args.get("symbol")
        if not symbol:
            return ToolResult(content="'symbol' is required", is_error=True)
        asset_type = self._resolve_asset_type(symbol, args.get("asset_type"))
        try:
            if asset_type == "crypto":
                data = await self._fetch_crypto(symbol)
            else:
                data = await self._fetch_stock(symbol)
        except Exception as exc:
            logger.error("[markets] %s lookup for '%s' failed: %s", asset_type, symbol, exc)
            return ToolResult(content=f"Market lookup failed: {exc}", is_error=True)

        if data is None:
            return ToolResult(
                content=f"No market data for symbol '{symbol}'",
                is_error=True,
            )
        if not full:
            return ToolResult(content=json.dumps({
                "symbol": symbol.upper(),
                "asset_type": asset_type,
                "price": data["price"],
                "currency": data.get("currency", "USD"),
            }))
        return ToolResult(content=json.dumps({
            "symbol": symbol.upper(),
            "asset_type": asset_type,
            "price": data["price"],
            "change_24h_pct": data.get("change_24h_pct"),
            "market_cap": data.get("market_cap"),
            "currency": data.get("currency", "USD"),
        }))

    async def _fetch_crypto(self, symbol: str) -> dict | None:
        params = {
            "vs_currency": "usd",
            "symbols": symbol.lower(),
        }
        response = await self._fetch("GET", _COINGECKO_MARKETS, params=params)
        if not isinstance(response, list) or not response:
            return None
        coin = response[0]
        return {
            "price": coin.get("current_price"),
            "change_24h_pct": coin.get("price_change_percentage_24h"),
            "market_cap": coin.get("market_cap"),
            "currency": "USD",
        }

    async def _fetch_stock(self, symbol: str) -> dict | None:
        url = f"{_YAHOO_CHART}{symbol.upper()}"
        response = await self._fetch("GET", url)
        chart = (response or {}).get("chart") or {}
        results = chart.get("result") or []
        if not results:
            return None
        meta = results[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        prev_close = meta.get("previousClose")
        change_pct = None
        if price is not None and prev_close:
            change_pct = (price - prev_close) / prev_close * 100
        return {
            "price": price,
            "change_24h_pct": change_pct,
            "market_cap": meta.get("marketCap"),
            "currency": meta.get("currency", "USD"),
        }


def create(fetch_fn: FetchFn | None = None) -> MarketsPlugin:
    return MarketsPlugin(fetch_fn=fetch_fn)
