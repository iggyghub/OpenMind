"""Tests for cerebral/trading/market_hours.py (S31/#896)."""
from datetime import datetime

from cerebral.trading.market_hours import is_market_hours


def test_open_on_a_weekday_mid_session():
    assert is_market_hours(datetime(2026, 8, 25, 10, 0)) is True  # Tuesday


def test_open_at_the_exact_opening_bell():
    assert is_market_hours(datetime(2026, 8, 25, 9, 30)) is True


def test_closed_one_minute_before_the_opening_bell():
    assert is_market_hours(datetime(2026, 8, 25, 9, 29)) is False


def test_closed_at_the_exact_closing_bell():
    assert is_market_hours(datetime(2026, 8, 25, 16, 0)) is False


def test_open_one_minute_before_the_closing_bell():
    assert is_market_hours(datetime(2026, 8, 25, 15, 59)) is True


def test_closed_overnight():
    assert is_market_hours(datetime(2026, 8, 25, 2, 0)) is False


def test_closed_on_saturday():
    assert is_market_hours(datetime(2026, 8, 29, 12, 0)) is False  # Saturday


def test_closed_on_sunday():
    assert is_market_hours(datetime(2026, 8, 30, 12, 0)) is False  # Sunday


def test_default_now_does_not_raise():
    """No injected `now` -- must actually read the real clock, not crash."""
    is_market_hours()
