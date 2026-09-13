"""Unit tests for cerebral/trading/news_cache.py (RP8)."""
import os
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from cerebral.trading import news_cache


@pytest.fixture
def mock_db(tmp_path):
    """Create a temporary bars.db and initialize it."""
    db_path = str(tmp_path / "bars.db")
    news_cache.init_news_db(db_path)
    return db_path


def test_fetch_news_pagination_and_dedup(mock_db):
    """Confirm pagination follows next_page_token until exhausted and dedupes on article_id."""
    call_count = 0
    def mock_get_news(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "articles": [
                    {"id": "a1", "symbols": ["AAPL"], "published_at": "2024-01-01T12:00:00Z"},
                    {"id": "a2", "symbols": ["TSLA"], "published_at": "2024-01-02T12:00:00Z"},
                ],
                "next_page_token": "token2",
            }
        elif call_count == 2:
            return {
                "articles": [
                    {"id": "a3", "symbols": ["MSFT"], "published_at": "2024-01-03T12:00:00Z"},
                    {"id": "a2", "symbols": ["TSLA"], "published_at": "2024-01-02T12:00:00Z"},  # duplicate
                ],
                "next_page_token": None,
            }
        return {"articles": [], "next_page_token": None}

    with patch("cerebral.trading.news_cache.AlpacaMarketDataClient") as MockClient:
        mock_client = MagicMock()
        mock_client.get_news = mock_get_news
        MockClient.return_value = mock_client

        result = news_cache.fetch_news("AAPL", "2024-01-01", "2024-01-03", client=mock_client)

        assert call_count == 2
        assert len(result) == 3  # a1, a2, a3
        assert {r["id"] for r in result} == {"a1", "a2", "a3"}

    # Verify DB state
    conn = sqlite3.connect(mock_db)
    rows = conn.execute("SELECT article_id, symbol, published_day, n_symbols FROM news").fetchall()
    conn.close()
    assert len(rows) == 4  # a1(AAPL), a2(TSLA), a3(MSFT), a2(TSLA)
    assert set(r[0] for r in rows) == {"a1", "a2", "a3"}
    assert {r[3] for r in rows} == {1}  # all n_symbols=1


def test_fetch_news_gap_fill(mock_db):
    """Confirm gap-fill-on-refetch behavior mirrors bar_cache."""
    call_count = 0
    def mock_get_news(**kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "articles": [{"id": "b1", "symbols": ["SPY"], "published_at": f"2024-01-{10+call_count:02d}T00:00:00Z"}],
            "next_page_token": None,
        }

    with patch("cerebral.trading.news_cache.AlpacaMarketDataClient") as MockClient:
        mock_client = MagicMock()
        mock_client.get_news = mock_get_news
        MockClient.return_value = mock_client

        # First fetch
        news_cache.fetch_news("SPY", "2024-01-01", "2024-01-05", client=mock_client)
        assert call_count == 1

        # Second fetch (should pick up from day 2 because day 1 is cached)
        news_cache.fetch_news("SPY", "2024-01-01", "2024-01-05", client=mock_client)
        assert call_count == 2  # fetches 2024-01-02

        # Third fetch (fully cached)
        news_cache.fetch_news("SPY", "2024-01-01", "2024-01-05", client=mock_client)
        assert call_count == 2  # no new calls


def test_count_news_events_prominence_filter(mock_db):
    """Seed DB with one article tagged to 2 symbols (counts) and one to 5 symbols (excluded), assert count reflects only prominent."""
    conn = sqlite3.connect(mock_db)
    # Article A: 2 symbols
    conn.execute("INSERT OR IGNORE INTO news VALUES ('artA', 'SPY', '2024-01-01', 2)")
    # Article B: 5 symbols
    conn.execute("INSERT OR IGNORE INTO news VALUES ('artB', 'SPY', '2024-01-01', 5)")
    conn.commit()
    conn.close()

    assert news_cache.count_news_events("SPY", "2024-01-01") == 1
    assert news_cache.count_news_events("SPY", "2024-01-01", max_symbols=10) == 2
