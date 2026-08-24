"""
Tests for plugins/stocks.py (S24/#877).

All HTTP calls injected via fetch_fn (returning http_client._default_fetch's
{"status", "body", "headers"} shape); yfinance mocked directly. No network.
"""
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from plugins.stocks import StocksPlugin, create


def _make_fetch(routes: dict, captured: list | None = None):
    async def fake_fetch(method, url, *, headers=None, params=None, json=None):
        if captured is not None:
            captured.append({"method": method, "url": url, "headers": headers})
        for needle, body in routes.items():
            if needle in url:
                if isinstance(body, Exception):
                    raise body
                return {"status": 200, "body": body, "headers": {}}
        raise AssertionError(f"unexpected url: {url}")
    return fake_fetch


COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla Inc."},
}

SUBMISSIONS_AAPL = {
    "filings": {
        "recent": {
            "form": ["10-Q", "4", "10-K", "8-K"],
            "accessionNumber": ["0000320193-24-000010", "0000320193-24-000009",
                                 "0000320193-24-000005", "0000320193-24-000001"],
            "filingDate": ["2024-05-01", "2024-04-15", "2024-01-01", "2023-12-01"],
            "primaryDocument": ["aapl-10q.htm", "aapl-4.htm", "aapl-10k.htm", "aapl-8k.htm"],
        }
    }
}


class TestCreate:
    def test_create_takes_no_required_args(self):
        """The real plugin loader calls module.create() with zero arguments
        (cerebral/mcp/orchestrator.py's _load_plugin_file) -- a required
        positional param here means the plugin can never actually load."""
        plugin = create()
        assert plugin.name == "stocks"

    def test_list_tools_returns_real_tool_objects(self):
        from cerebral.mcp.orchestrator import Tool
        plugin = create()
        tools = plugin.list_tools()
        assert len(tools) == 3
        assert all(isinstance(t, Tool) for t in tools)
        assert {t.name for t in tools} == {"stock_fundamentals", "sec_filings", "sec_new_filings"}


class TestStockFundamentals:
    @patch("plugins.stocks.yf.Ticker")
    @pytest.mark.asyncio
    async def test_returns_sector_market_cap_quarterly_financials(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker_cls.return_value = mock_ticker
        mock_ticker.info = {"sector": "Technology", "marketCap": 1_000_000_000, "beta": 1.2}
        mock_ticker.quarterly_financials = pd.DataFrame(
            [[100, 200], [300, 400]], columns=["Revenue", "Net Income"],
            index=["2024-01-01", "2023-10-01"],
        )

        plugin = create()
        result = await plugin.call_tool("stock_fundamentals", {"symbol": "aapl"})

        assert not result.is_error
        data = json.loads(result.content)
        assert data["symbol"] == "AAPL"
        assert data["sector"] == "Technology"
        assert data["market_cap"] == 1_000_000_000
        assert data["beta"] == 1.2
        assert len(data["quarterly_financials"]) == 2

    @patch("plugins.stocks.yf.Ticker")
    @pytest.mark.asyncio
    async def test_empty_quarterly_financials(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker_cls.return_value = mock_ticker
        mock_ticker.info = {"sector": None}
        mock_ticker.quarterly_financials = None

        plugin = create()
        result = await plugin.call_tool("stock_fundamentals", {"symbol": "NOPE"})

        assert json.loads(result.content)["quarterly_financials"] == {}

    @pytest.mark.asyncio
    async def test_missing_symbol_is_an_error(self):
        plugin = create()
        result = await plugin.call_tool("stock_fundamentals", {})
        assert result.is_error


class TestSecFilings:
    @pytest.mark.asyncio
    async def test_resolves_cik_and_returns_recent_10q_10k(self):
        fetch = _make_fetch({
            "company_tickers.json": json.dumps(COMPANY_TICKERS),
            "submissions/CIK0000320193.json": json.dumps(SUBMISSIONS_AAPL),
        })
        plugin = StocksPlugin(fetch_fn=fetch)

        result = await plugin.call_tool("sec_filings", {"symbol": "AAPL", "count": 2})

        assert not result.is_error
        data = json.loads(result.content)
        forms = [f["form"] for f in data["filings"]]
        assert forms == ["10-Q", "10-K"]  # "4" and "8-K" filtered out, count=2 respected
        assert data["filings"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/")

    @pytest.mark.asyncio
    async def test_unknown_symbol_has_no_cik(self):
        fetch = _make_fetch({"company_tickers.json": json.dumps(COMPANY_TICKERS)})
        plugin = StocksPlugin(fetch_fn=fetch)

        result = await plugin.call_tool("sec_filings", {"symbol": "NOTREAL"})

        assert result.is_error
        assert "CIK" in result.content

    @pytest.mark.asyncio
    async def test_sends_the_required_sec_user_agent(self):
        captured = []
        fetch = _make_fetch({
            "company_tickers.json": json.dumps(COMPANY_TICKERS),
            "submissions/CIK0000320193.json": json.dumps(SUBMISSIONS_AAPL),
        }, captured=captured)
        plugin = StocksPlugin(fetch_fn=fetch)

        await plugin.call_tool("sec_filings", {"symbol": "AAPL"})

        assert captured  # at least one real request went out
        for call in captured:
            assert "OpenMind" in call["headers"]["User-Agent"]


class TestSecNewFilings:
    INDEX_TEXT = (
        "Description:           Master Index\n"
        "Last Data Received:    20240514\n"
        "--------------------------------------------------------\n"
        "CIK|Company Name|Form Type|Date Filed|Filename\n"
        "--------------------------------------------------------\n"
        "320193|Apple Inc.|10-Q|2024-05-14|edgar/data/320193/acc001.txt\n"
        "999999|Startup Inc.|S-1|2024-05-14|edgar/data/999999/acc002.txt\n"
        "888888|Fintech Co.|424B4|2024-05-14|edgar/data/888888/acc003.txt\n"
        "777777|Legacy Corp.|10-K|2024-05-14|edgar/data/777777/acc004.txt\n"
    )

    @pytest.mark.asyncio
    async def test_identifies_ipo_filings_and_notifies(self):
        fetch = _make_fetch({"daily-index": self.INDEX_TEXT})
        notified = []
        plugin = StocksPlugin(fetch_fn=fetch, notify_fn=lambda event, payload: notified.append((event, payload)))

        result = await plugin.call_tool("sec_new_filings", {})

        data = json.loads(result.content)
        assert data["count"] == 2  # S-1 and 424B4 only
        assert len(notified) == 2
        assert all(event == "ipo_alert" for event, _ in notified)
        forms = {payload["filing"]["form"] for _, payload in notified}
        assert forms == {"S-1", "424B4"}

    @pytest.mark.asyncio
    async def test_no_notify_fn_does_not_raise(self):
        """notify_fn is optional -- a plugin instance with none configured
        must log, not crash, matching this codebase's convention for seams
        main.py hasn't wired yet."""
        fetch = _make_fetch({"daily-index": self.INDEX_TEXT})
        plugin = StocksPlugin(fetch_fn=fetch, notify_fn=None)

        result = await plugin.call_tool("sec_new_filings", {})

        assert json.loads(result.content)["count"] == 2

    @pytest.mark.asyncio
    async def test_missing_index_is_a_safe_no_op(self):
        """Today's index may not be published yet (weekend/holiday/timing) --
        must not raise, must not fabricate a filing count."""
        fetch = _make_fetch({})  # no route matches -> the fake raises AssertionError
        plugin = StocksPlugin(fetch_fn=fetch)

        result = await plugin.call_tool("sec_new_filings", {})

        assert json.loads(result.content)["status"] == "no_index"
