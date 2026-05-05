"""
Markets MCP plugin tests — Issue #25.

Tools: market_price, market_quote.

Auto-detects crypto vs stock by symbol — CoinGecko for known crypto symbols,
Yahoo Finance public quote endpoint otherwise. An explicit `asset_type` of
"crypto" or "stock" overrides auto-detection.

All HTTP calls injected via fetch_fn.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fetch(routes: dict, captured: list | None = None):
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        if captured is not None:
            captured.append({"method": method, "url": url, "params": params})
        for needle, response in routes.items():
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected url: {url}")
    return fake_fetch


_COINGECKO_BTC = [{
    "id": "bitcoin",
    "symbol": "btc",
    "name": "Bitcoin",
    "current_price": 50000.0,
    "market_cap": 1_000_000_000_000,
    "price_change_percentage_24h": 2.5,
}]

_YAHOO_AAPL = {
    "chart": {
        "result": [{
            "meta": {
                "symbol": "AAPL",
                "regularMarketPrice": 175.5,
                "previousClose": 170.0,
                "marketCap": 2_500_000_000_000,
                "currency": "USD",
            },
        }],
    },
}


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools, create()
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_two(self):
        from plugins.markets import create

        names = {t.name for t in create().list_tools()}
        assert names == {"market_price", "market_quote"}

    def test_plugin_name_is_markets(self):
        from plugins.markets import create

        assert create().name == "markets"

    def test_required_args_in_schema(self):
        from plugins.markets import create

        tools = {t.name: t for t in create().list_tools()}
        assert "symbol" in tools["market_price"].schema.get("required", [])
        assert "symbol" in tools["market_quote"].schema.get("required", [])


# ---------------------------------------------------------------------------
# Cycle 2 — Required-arg validation
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["market_price", "market_quote"])
    async def test_missing_symbol_returns_error(self, tool):
        from plugins.markets import MarketsPlugin

        plugin = MarketsPlugin(fetch_fn=_make_fetch({}))
        result = await plugin.call_tool(tool, {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 3 — market_price for crypto via CoinGecko
# ---------------------------------------------------------------------------

class TestMarketPriceCrypto:
    @pytest.mark.asyncio
    async def test_btc_routed_to_coingecko(self):
        from plugins.markets import MarketsPlugin

        captured: list = []
        plugin = MarketsPlugin(
            fetch_fn=_make_fetch(
                {"coingecko.com": _COINGECKO_BTC},
                captured=captured,
            )
        )
        result = await plugin.call_tool("market_price", {"symbol": "BTC"})
        assert not result.is_error
        # Did we call CoinGecko?
        assert any("coingecko" in c["url"] for c in captured)

        data = json.loads(result.content)
        assert data["symbol"] == "BTC"
        assert data["price"] == 50000.0
        assert data["asset_type"] == "crypto"

    @pytest.mark.asyncio
    async def test_coingecko_empty_response_returns_error(self):
        from plugins.markets import MarketsPlugin

        plugin = MarketsPlugin(
            fetch_fn=_make_fetch({"coingecko.com": []})
        )
        result = await plugin.call_tool("market_price", {"symbol": "BTC"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 4 — market_price for stocks via Yahoo Finance
# ---------------------------------------------------------------------------

class TestMarketPriceStock:
    @pytest.mark.asyncio
    async def test_aapl_routed_to_yahoo(self):
        from plugins.markets import MarketsPlugin

        captured: list = []
        plugin = MarketsPlugin(
            fetch_fn=_make_fetch(
                {"finance.yahoo.com": _YAHOO_AAPL},
                captured=captured,
            )
        )
        result = await plugin.call_tool("market_price", {"symbol": "AAPL"})
        assert not result.is_error
        assert any("yahoo" in c["url"] for c in captured)

        data = json.loads(result.content)
        assert data["symbol"] == "AAPL"
        assert data["price"] == 175.5
        assert data["asset_type"] == "stock"

    @pytest.mark.asyncio
    async def test_yahoo_empty_result_returns_error(self):
        from plugins.markets import MarketsPlugin

        plugin = MarketsPlugin(
            fetch_fn=_make_fetch(
                {"finance.yahoo.com": {"chart": {"result": []}}}
            )
        )
        result = await plugin.call_tool("market_price", {"symbol": "AAPL"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — explicit asset_type override
# ---------------------------------------------------------------------------

class TestAssetTypeOverride:
    @pytest.mark.asyncio
    async def test_stock_override_uses_yahoo_for_crypto_symbol(self):
        """asset_type=stock forces Yahoo even for a known crypto symbol."""
        from plugins.markets import MarketsPlugin

        captured: list = []
        plugin = MarketsPlugin(
            fetch_fn=_make_fetch(
                {"finance.yahoo.com": _YAHOO_AAPL},
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "market_price", {"symbol": "BTC", "asset_type": "stock"}
        )
        assert not result.is_error
        assert all("coingecko" not in c["url"] for c in captured)

    @pytest.mark.asyncio
    async def test_crypto_override_uses_coingecko_for_stock_symbol(self):
        from plugins.markets import MarketsPlugin

        captured: list = []
        plugin = MarketsPlugin(
            fetch_fn=_make_fetch(
                {"coingecko.com": _COINGECKO_BTC},
                captured=captured,
            )
        )
        result = await plugin.call_tool(
            "market_price", {"symbol": "AAPL", "asset_type": "crypto"}
        )
        assert not result.is_error
        assert all("yahoo" not in c["url"] for c in captured)


# ---------------------------------------------------------------------------
# Cycle 6 — market_quote includes 24h change + market cap
# ---------------------------------------------------------------------------

class TestMarketQuote:
    @pytest.mark.asyncio
    async def test_quote_for_crypto_includes_change_and_cap(self):
        from plugins.markets import MarketsPlugin

        plugin = MarketsPlugin(
            fetch_fn=_make_fetch({"coingecko.com": _COINGECKO_BTC})
        )
        result = await plugin.call_tool("market_quote", {"symbol": "BTC"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["price"] == 50000.0
        assert data["change_24h_pct"] == 2.5
        assert data["market_cap"] == 1_000_000_000_000

    @pytest.mark.asyncio
    async def test_quote_for_stock_computes_change_from_previous_close(self):
        from plugins.markets import MarketsPlugin

        plugin = MarketsPlugin(
            fetch_fn=_make_fetch({"finance.yahoo.com": _YAHOO_AAPL})
        )
        result = await plugin.call_tool("market_quote", {"symbol": "AAPL"})
        assert not result.is_error
        data = json.loads(result.content)
        # (175.5 - 170.0) / 170.0 * 100 ≈ 3.235
        assert data["change_24h_pct"] == pytest.approx(3.235, rel=1e-3)
        assert data["market_cap"] == 2_500_000_000_000


# ---------------------------------------------------------------------------
# Cycle 7 — Network failure surfaces is_error
# ---------------------------------------------------------------------------

class TestNetworkFailure:
    @pytest.mark.asyncio
    async def test_network_failure_returns_error(self):
        from plugins.markets import MarketsPlugin

        plugin = MarketsPlugin(
            fetch_fn=_make_fetch({"coingecko.com": ConnectionError("offline")})
        )
        result = await plugin.call_tool("market_price", {"symbol": "BTC"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 — Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.markets import MarketsPlugin

        plugin = MarketsPlugin(fetch_fn=_make_fetch({}))
        result = await plugin.call_tool("market_history", {"symbol": "BTC"})
        assert result.is_error
