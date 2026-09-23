"""Unit tests for cerebral/trading/news_cache.py (RP8).

Fakes stand in for alpaca-py's real NewsClient/NewsRequest/NewsSet/News --
those are attribute-based Pydantic models (article.id, article.symbols,
article.created_at; news_set.data["news"], news_set.next_page_token), not
dicts, so the fakes here mirror that shape rather than a generic dict.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

import pytest

from cerebral.trading import news_cache


@dataclass
class FakeArticle:
    id: str
    symbols: list
    created_at: datetime


@dataclass
class FakeNewsSet:
    data: dict
    next_page_token: "str | None" = None


@pytest.fixture
def db_path(tmp_path):
    """A tmp-path-backed news db, schema already created."""
    return news_cache.init_news_db(str(tmp_path / "bars.db"))


def test_fetch_news_pagination_and_dedup(db_path):
    """Confirm pagination follows next_page_token until exhausted and dedupes on article id."""
    call_count = 0

    class FakeClient:
        def get_news(self, req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return FakeNewsSet(
                    data={"news": [
                        FakeArticle("a1", ["AAPL"], datetime(2024, 1, 1, 12)),
                        FakeArticle("a2", ["TSLA"], datetime(2024, 1, 2, 12)),
                    ]},
                    next_page_token="token2",
                )
            if call_count == 2:
                return FakeNewsSet(
                    data={"news": [
                        FakeArticle("a3", ["MSFT"], datetime(2024, 1, 3, 12)),
                        FakeArticle("a2", ["TSLA"], datetime(2024, 1, 2, 12)),  # duplicate
                    ]},
                    next_page_token=None,
                )
            return FakeNewsSet(data={"news": []}, next_page_token=None)

    result = news_cache.fetch_news("AAPL", "2024-01-01", "2024-01-03", client=FakeClient(), db_path=db_path)

    assert call_count == 2
    assert len(result) == 3  # a1, a2, a3
    assert {a.id for a in result} == {"a1", "a2", "a3"}

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT article_id, symbol, published_day, n_symbols FROM news").fetchall()
    conn.close()
    assert len(rows) == 3  # one row per article (each tagged to exactly 1 symbol here)
    assert set(r[0] for r in rows) == {"a1", "a2", "a3"}
    assert {r[3] for r in rows} == {1}  # all n_symbols=1


def test_fetch_news_gap_fill(db_path):
    """Confirm gap-fill-on-refetch mirrors bar_cache: only the gap past the
    cached max day is fetched on a subsequent call."""
    call_count = 0

    # Call 1 advances the cache to day 2 (still short of `end`=05, so a
    # second fetch is genuinely needed); call 2 reaches `end` exactly, so a
    # third call must see the range as fully cached and fetch nothing.
    fake_days = {1: 2, 2: 5}

    class FakeClient:
        def get_news(self, req):
            nonlocal call_count
            call_count += 1
            return FakeNewsSet(
                data={"news": [FakeArticle(f"b{call_count}", ["SPY"], datetime(2024, 1, fake_days[call_count]))]},
                next_page_token=None,
            )

    client = FakeClient()

    news_cache.fetch_news("SPY", "2024-01-01", "2024-01-05", client=client, db_path=db_path)
    assert call_count == 1

    news_cache.fetch_news("SPY", "2024-01-01", "2024-01-05", client=client, db_path=db_path)
    assert call_count == 2  # picked up from the day after the cached max

    news_cache.fetch_news("SPY", "2024-01-01", "2024-01-05", client=client, db_path=db_path)
    assert call_count == 2  # fully cached through `end` -- no new calls


def test_count_news_events_prominence_filter(db_path):
    """Seed with one article tagged to 2 symbols (counts) and one to 5
    (excluded by the default max_symbols=3), confirm the count reflects
    only the prominent one."""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO news VALUES ('artA', 'SPY', '2024-01-01', 2)")
    conn.execute("INSERT OR IGNORE INTO news VALUES ('artB', 'SPY', '2024-01-01', 5)")
    conn.commit()
    conn.close()

    assert news_cache.count_news_events("SPY", "2024-01-01", db_path=db_path) == 1
    assert news_cache.count_news_events("SPY", "2024-01-01", max_symbols=10, db_path=db_path) == 2


def test_fetch_news_skips_when_genuinely_fully_covered(db_path):
    """No fetch when [start, end] was already genuinely, entirely covered -- both the newest
    cached day reaching `end` AND `start` itself already having been covered by a prior real
    fetch_news call (recorded in news_cover), not just a coincidental MAX(published_day)."""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO news VALUES ('artZ', 'AAPL', '2024-01-05', 1)")
    conn.execute("INSERT OR REPLACE INTO news_cover VALUES ('AAPL', '2024-01-01')")
    conn.commit()
    conn.close()

    class FailIfCalled:
        def get_news(self, req):
            raise AssertionError("should not fetch -- already genuinely fully covered")

    result = news_cache.fetch_news("AAPL", "2024-01-01", "2024-01-05", client=FailIfCalled(), db_path=db_path)
    assert result == []


def test_fetch_news_backfills_older_start_despite_recent_fragment(db_path):
    """Regression (2026-09-23): a symbol with only a small RECENT fragment cached (from
    unrelated ad-hoc use, no news_cover row -- the legacy-cache case) must still trigger a real
    fetch when a request asks for an OLDER start date than anything ever covered. Before this
    fix, gap-fill only ever reached forward from the newest cached day, so this silently fetched
    nothing -- confirmed live against a real symbol during the trend-basket campaign."""
    conn = sqlite3.connect(db_path)
    # A recent-only fragment, as if from unrelated prior ad-hoc use -- no news_cover row at all.
    conn.execute("INSERT OR IGNORE INTO news VALUES ('recent1', 'AAPL', '2026-08-01', 1)")
    conn.commit()
    conn.close()

    call_count = 0

    class FakeClient:
        def get_news(self, req):
            nonlocal call_count
            call_count += 1
            return FakeNewsSet(
                data={"news": [FakeArticle("old1", ["AAPL"], datetime(2020, 1, 15))]},
                next_page_token=None,
            )

    result = news_cache.fetch_news(
        "AAPL", "2020-01-01", "2026-09-01", client=FakeClient(), db_path=db_path,
    )

    assert call_count == 1  # the old bug would have skipped fetching entirely
    assert len(result) == 1
    assert result[0].id == "old1"

    conn = sqlite3.connect(db_path)
    covered_from = conn.execute(
        "SELECT covered_from FROM news_cover WHERE symbol = 'AAPL'"
    ).fetchone()[0]
    conn.close()
    assert covered_from == "2020-01-01"  # the real backfilled start, not the recent fragment's
