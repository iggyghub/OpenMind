import pytest
from cerebral.trading.ipo_calendar import fetch_upcoming_ipos


def fake_fetch_spac_and_dupes(_url: str) -> str:
    return (
        "<html><body>"
        '{s:"AAPL",n:"Apple Inc.",ipoDate:"2026-09-15"}'
        '{s:"XYZ",n:"Example Acquisition Corp.",ipoDate:"2026-09-16"}'
        '{s:"MSFT",n:"Microsoft Corporation",ipoDate:"2026-09-17"}'
        '{s:"AAPL",n:"Apple Inc. Duplicate",ipoDate:"2026-09-18"}'
        "</body></html>"
    )


def test_fetch_upcoming_ipos_filters_spacs_and_duplicates():
    results = fetch_upcoming_ipos(fetch_html_fn=fake_fetch_spac_and_dupes)
    
    # Should exclude the SPAC-named company
    assert not any(r["ticker"] == "XYZ" for r in results)
    
    # Should keep real operating companies
    assert any(r["ticker"] == "AAPL" for r in results)
    assert any(r["ticker"] == "MSFT" for r in results)
    
    # Correct values for kept entries
    aapl = next(r for r in results if r["ticker"] == "AAPL")
    assert aapl["company"] == "Apple Inc."
    assert aapl["ipo_date"] == "2026-09-15"
    
    msft = next(r for r in results if r["ticker"] == "MSFT")
    assert msft["company"] == "Microsoft Corporation"
    assert msft["ipo_date"] == "2026-09-17"
    
    # Duplicates should be deduplicated
    assert len([r for r in results if r["ticker"] == "AAPL"]) == 1
    assert len(results) == 2
