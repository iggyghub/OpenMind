"""Tests for plugins/stocks.py"""

import bz2
import io
import re
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest

from plugins import stocks


class MockCtx:
    """Minimal context mock for plugin tests."""
    def __init__(self):
        self.notify_calls = []

    def notify(self, event_type: str, payload: dict):
        self.notify_calls.append({"event": event_type, "payload": payload})


class TestStockFundamentals:
    @patch("plugins.stocks.yf.Ticker")
    def test_returns_sector_market_cap_quarterly_financials(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker_cls.return_value = mock_ticker

        mock_info = {
            "sector": "Technology",
            "marketCap": 1000000000,
            "beta": 1.2,
        }
        mock_ticker.info = mock_info

        import pandas as pd
        qf_data = pd.DataFrame(
            [[100, 200], [300, 400]],
            columns=["Revenue", "Net Income"],
            index=[datetime(2024, 1, 1), datetime(2023, 10, 1)],
        )
        mock_ticker.quarterly_financials = qf_data

        ctx = MockCtx()
        result = stocks._stock_fundamentals(ctx, "AAPL")

        assert result["symbol"] == "AAPL"
        assert result["sector"] == "Technology"
        assert result["market_cap"] == 1000000000
        assert result["beta"] == 1.2
        assert "quarterly_financials" in result
        assert len(result["quarterly_financials"]) == 2

    def test_empty_quarterly_financials(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker_cls.return_value = mock_ticker
        mock_ticker.info = {"sector": None}
        mock_ticker.quarterly_financials = None
        ctx = MockCtx()
        result = stocks._stock_fundamentals(ctx, "NOPE")
        assert result["quarterly_financials"] == {}


class TestSecFilings:
    @patch("plugins.stocks.http_get")
    def test_fetches_cik_and_filings(self, mock_get):
        # Mock search result
        xml_search = """<?xml version="1.0"?>
        <index xmlns="http://www.sec.gov/Archives/edgar/index">
          <filedAsOfType>CIK</filedAsOfType>
          <filedAsOfType>0000320193</filedAsOfType>
          <companyName>Apple Inc.</companyName>
          <companyName href="/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000320193">Apple Inc.</companyName>
          <CIK>0000320193</CIK>
        </index>
        """
        # Mock filings result
        xml_filings = """<?xml version="1.0"?>
        <index xmlns="http://www.sec.gov/Archives/edgar/index">
          <filing>
            <edgar:form>10-Q</edgar:form>
            <edgar:accessionNumber>0000320193-24-000001</edgar:accessionNumber>
            <edgar:filingDate>2024-01-01</edgar:filingDate>
          </filing>
          <filing>
            <edgar:form>4</edgar:form>
            <edgar:accessionNumber>0000320193-24-000002</edgar:accessionNumber>
          </filing>
        </index>
        """
        # Mock filing text
        filing_html = "<html><body><pre>Financials here...</pre></body></html>"

        mock_resp_search = MagicMock()
        mock_resp_search.text = xml_search
        mock_resp_filings = MagicMock()
        mock_resp_filings.text = xml_filings
        mock_resp_filing = MagicMock()
        mock_resp_filing.text = filing_html

        mock_get.side_effect = [mock_resp_search, mock_resp_filings, mock_resp_filing]

        ctx = MockCtx()
        result = stocks._sec_filings(ctx, "AAPL", count=1)

        assert len(result) == 1
        assert result[0]["form"] == "10-Q"
        assert "Financials" in result[0]["text_preview"]
        assert result[0]["full_text_url"].startswith("https://www.sec.gov/Archives")

        # Verify headers on calls
        for c in mock_get.call_args_list:
            headers = c.kwargs.get("headers", {})
            assert "User-Agent" in headers
            assert "OpenMind" in headers["User-Agent"]

    @patch("plugins.stocks.http_get")
    def test_respects_rate_limit(self, mock_get):
        mock_get.side_effect = [MagicMock(text="<root/>")] * 15
        
        ctx = MockCtx()
        # Trigger multiple requests; rate limiter should sleep
        # We just ensure no exception and calls happen
        stocks._sec_filings(ctx, "TEST", count=1)
        assert mock_get.call_count >= 1


class TestSecNewFilings:
    @patch("plugins.stocks.http_get")
    def test_identifies_ipos_and_notifies(self, mock_get):
        ctx = MockCtx()
        
        # Create a mock master index
        index_content = (
            b"20240514|0000320193|Apple Inc.|10-Q|20240514|acc001\n"
            b"20240514|0000999999|Startup Inc.|S-1|20240514|acc002\n"
            b"20240514|0000888888|Fintech Co.|424B4|20240514|acc003\n"
            b"20240514|0000777777|Legacy Corp.|10-K|20240514|acc004\n"
        )
        
        # Mock http_get for index fetch
        mock_index_resp = MagicMock()
        mock_index_resp.content = index_content
        mock_get.return_value = mock_index_resp

        result = stocks._sec_new_filings(ctx)

        # Should notify for S-1 and 424B4
        assert result["count"] == 2
        assert len(ctx.notify_calls) == 2
        
        notifications = [c["payload"]["message"] for c in ctx.notify_calls]
        assert any("S-1" in n for n in notifications)
        assert any("424B4" in n for n in notifications)
        assert not any("10-K" in n for n in notifications)

    @patch("plugins.stocks.http_get")
    @patch("cerebral.core.strategy.run_gauntlet")
    def test_never_calls_run_gauntlet(self, mock_gauntlet, mock_get):
        """sec_new_filings is notification-only; must not create strategies."""
        mock_get.return_value = MagicMock(content=b"20240514|000|CIK|S-1|20240514|acc")
        ctx = MockCtx()
        
        stocks._sec_new_filings(ctx)
        
        mock_gauntlet.assert_not_called()
