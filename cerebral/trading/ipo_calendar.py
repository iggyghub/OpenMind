"""Fetches upcoming IPO tickers from a free public calendar page. No API key, no auth --
see the module-level docstring in ipo_strategy.py / CONTEXT.md's "IPO play" glossary entry
for why this feeds a Gauntlet-skipping dispatch path rather than the normal idea-discovery
one."""
import re
from typing import Callable, List, Dict, Optional

_CALENDAR_URL = "https://stockanalysis.com/ipos/calendar/"

# ponytail: name-based SPAC filter, not a real classifier -- the site's own data model
# declares an `isSpac` field but doesn't populate it on individual calendar rows (confirmed
# live 2026-09-02). SPACs IPO flat at a fixed price and never show a real pop, so they're
# noise for this strategy. Upgrade to a real classifier if this heuristic starts missing.
_SPAC_NAME_RE = re.compile(r"Acquisition (Corp|Corporation)|Capital (Corp|Partners)|SPAC", re.IGNORECASE)

_ROW_RE = re.compile(r'\{s:"([A-Z.]+)",n:"([^"]+)",ipoDate:"(\d{4}-\d{2}-\d{2})"')


def fetch_upcoming_ipos(fetch_html_fn: Optional[Callable[[str], str]] = None) -> List[Dict[str, str]]:
    """Returns a list of {"ticker": str, "company": str, "ipo_date": "YYYY-MM-DD"} dicts for
    upcoming IPOs, filtering out SPAC-shaped names. `fetch_html_fn` is a test-only injection
    seam (a callable taking the URL and returning raw HTML text) -- defaults to a real
    stdlib-only HTTP GET, matching this codebase's convention of test-injected fetch seams
    (see e.g. _run_gauntlet's `fetch=None` param) rather than a new dependency."""
    if fetch_html_fn is not None:
        html = fetch_html_fn(_CALENDAR_URL)
    else:
        import urllib.request
        req = urllib.request.Request(_CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

    results = []
    seen = set()
    for m in _ROW_RE.finditer(html):
        ticker, company, ipo_date = m.group(1), m.group(2), m.group(3)
        if ticker in seen:
            continue
        seen.add(ticker)
        if _SPAC_NAME_RE.search(company):
            continue
        results.append({"ticker": ticker, "company": company, "ipo_date": ipo_date})
    return results
