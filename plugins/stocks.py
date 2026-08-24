"""
stocks.py -- Fundamentals, SEC Filings, IPO Detection.

Implements tools for structured financial data retrieval via yfinance and
SEC EDGAR. Follows the ADR-0005 plugin shape.
"""

import re
import time
import bz2
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

import yfinance as yf

from plugins.http_client import get as http_get

logger = logging.getLogger(__name__)

PLUGIN_NAME = "stocks"
REQUIRED_CAPABILITIES = frozenset({"external_data_read", "network_egress_cloud"})

# SEC fair-access policy requires a User-Agent identifying the app.
# Include a contact email to comply with the 10 req/s policy documentation.
SEC_UA = "OpenMind/1.0 (openmind@example.com)"

# Internal rate limiter state for SEC requests.
_sec_requests: List[float] = []


def _check_sec_rate_limit():
    """Enforce SEC EDGAR 10 requests per second limit."""
    now = time.time()
    # Keep only requests within the last 0.1 seconds
    _sec_requests[:] = [t for t in _sec_requests if now - t < 0.1]
    if len(_sec_requests) >= 10:
        wait = 0.1 - (now - _sec_requests[0])
        if wait > 0:
            time.sleep(wait)
    _sec_requests.append(time.time())


def _edgar_get(url: str, params: dict = None) -> str:
    """Perform a rate-limited, compliant GET request to SEC EDGAR."""
    _check_sec_rate_limit()
    headers = {"User-Agent": SEC_UA}
    resp = http_get(url, params=params, headers=headers)
    return resp.text


def list_tools() -> List[str]:
    return ["stock_fundamentals", "sec_filings", "sec_new_filings"]


def create(ctx: Any) -> Dict[str, Any]:
    """Return the plugin's tool implementations."""
    return {
        "stock_fundamentals": _stock_fundamentals,
        "sec_filings": _sec_filings,
        "sec_new_filings": _sec_new_filings_factory(ctx),
    }


def _stock_fundamentals(ctx: Any, symbol: str) -> Dict[str, Any]:
    """
    Fetch sector, market cap, beta, and quarterly financials via yfinance.
    """
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}
    qf = ticker.quarterly_financials
    
    # Serialize DataFrame to dict for JSON serialization
    quarterly_dict = {}
    if qf is not None and not qf.empty:
        quarterly_dict = qf.to_dict(orient="index")

    return {
        "symbol": symbol,
        "sector": info.get("sector"),
        "market_cap": info.get("marketCap"),
        "beta": info.get("beta"),
        "quarterly_financials": quarterly_dict,
    }


def _sec_filings(ctx: Any, symbol: str, count: int = 3) -> List[Dict[str, Any]]:
    """
    Fetch recent 10-Q/10-K filing text from SEC EDGAR.
    """
    # 1. Search for CIK using the symbol
    search_url = "https://www.sec.gov/cgi-bin/browse-edgar"
    search_params = {
        "action": "getcompany",
        "CIK": symbol,
        "type": "",
        "dateb": "",
        "owner": "include",
        "count": "40",
        "search_text": "",
        "output": "xml",
    }
    xml_text = _edgar_get(search_url, params=search_params)
    
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    ns = {"edgar": "http://www.sec.gov/Archives/edgar/index"}
    company_element = root.find(".//edgar:companyName", ns)
    if company_element is None:
        raise ValueError(f"Could not find company for symbol {symbol}")
    
    # CIK is often a sibling or derived; EDGAR index XML structure varies.
    # We parse the links which contain the CIK in the URL or find the CIK element.
    # A robust way: find the 'accessionNumber' links or the CIK field.
    # EDGAR index XML often has <CIK> or links like <a href="/cgi-bin/browse-edgar?action=getcompany&CIK=...">
    # We extract CIK from the first link's query param or the CIK element.
    cik = None
    # Try to find CIK element directly
    cik_elem = root.find(".//edgar:CIK", ns)
    if cik_elem is not None and cik_elem.text:
        cik = cik_elem.text.strip().lstrip("0")
    else:
        # Fallback: parse from first company link
        link = root.find(".//edgar:companyName", ns)
        if link is not None and link.get("href"):
            href = link.get("href")
            match = re.search(r"CIK=(\d+)", href)
            if match:
                cik = match.group(1)
    
    if not cik:
        raise ValueError(f"Could not determine CIK for symbol {symbol}")

    # 2. Fetch filing index for this CIK
    filings_url = "https://www.sec.gov/cgi-bin/browse-edgar"
    filings_params = {
        "action": "getcompany",
        "CIK": cik,
        "type": "",  # Empty for all types, we filter later
        "dateb": "",
        "owner": "include",
        "count": "4",  # Fetch more to ensure we get 10-Q/10-K
        "search_text": "",
        "output": "xml",
    }
    filings_xml = _edgar_get(filings_url, params=filings_params)
    filings_root = ET.fromstring(filings_xml)

    # 3. Extract recent 10-Q/10-K
    accepted_types = {"10-Q", "10-K"}
    results = []
    
    # Parse filings; XML structure has <filing> elements
    for filing in filings_root.findall(".//edgar:filing", ns):
        form = filing.find("edgar:form", ns)
        accession = filing.find("edgar:accessionNumber", ns)
        
        if form is not None and form.text and form.text.strip() in accepted_types:
            acc = accession.text.strip().replace("-", "") if accession is not None else ""
            # Construct full text URL
            # EDGAR full text is at /Archives/edgar/full-index/.../ACCN/ACCN.html or .htm
            # Or via API: https://www.sec.gov/Archives/edgar/data/{CIK_Padded}/{ACCN}/{DOC}.htm
            # Simpler: use the accession to find the filing URL.
            # The XML contains <filing>...</filing>. We can look for the .htm link.
            doc_link = filing.find("edgar:filedAsOfType", ns) # Not always present for URL.
            
            # Robust: Construct the standard EDGAR full text URL.
            # Accession format: 0001234567-20-000012
            # Acc without dashes: 000123456720000012
            # CIK padded to 10 digits.
            cik_padded = cik.zfill(10)
            full_url = f"https://www.sec.gov/Archives/edgar/data/{cik_padded}/{acc}/{acc}.htm"
            
            # Fetch the HTML and extract text
            try:
                html = _edgar_get(full_url)
                # Strip HTML tags to get text
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                results.append({
                    "form": form.text.strip(),
                    "accession": acc,
                    "filing_date": _get_filing_date(filing, ns),
                    "text_preview": text[:500] if text else "",
                    "full_text_url": full_url,
                })
                if len(results) >= count:
                    break
            except Exception as e:
                logger.warning(f"Failed to fetch filing text for {acc}: {e}")

    return results


def _get_filing_date(filing, ns):
    """Helper to extract filing date from EDGAR XML."""
    date_elem = filing.find("edgar:filingDate", ns)
    if date_elem is not None and date_elem.text:
        return date_elem.text.strip()
    return None


def _sec_new_filings_factory(ctx: Any):
    """
    Returns the sec_new_filings function bound to the context.
    Notification-only. Never calls run_gauntlet.
    """
    def sec_new_filings() -> Dict[str, Any]:
        """
        Watch EDGAR's daily filing index for IPO registration/pricing filings (S-1/424B4).
        Emits notifications; does not create strategies.
        """
        today = datetime.utcnow().strftime("%Y%m%d")
        # EDGAR master index file URL pattern
        index_url = f"https://www.sec.gov/Archives/edgar/daily-index/{today}/master.{today}.bz2"
        
        # Download and decompress master index
        try:
            compressed_data = http_get(index_url).content
        except Exception as e:
            logger.warning(f"Could not fetch EDGAR master index for {today}: {e}")
            return {"status": "no_index", "date": today}

        with bz2.open(compressed_data) as f:
            master_text = f.read().decode("utf-8", errors="ignore")

        # Master index format: date|cik|form|...
        # Parse lines matching S-1 or 424B4
        new_filings = []
        for line in master_text.splitlines():
            if not line.startswith(today):
                continue
            parts = line.split("|")
            if len(parts) < 5:
                continue
            # Format: date|cik|form|filing_date|accession|...
            # Note: Exact schema may vary; SEC master index uses specific columns.
            # Columns: date, cik, company, form, filing_date, ...
            # We look for form in fields.
            try:
                form = parts[3] if len(parts) > 3 else ""
                cik = parts[1]
                acc = parts[4] if len(parts) > 4 else ""
                
                if form in ("S-1", "424B4"):
                    new_filings.append({
                        "form": form,
                        "cik": cik,
                        "accession": acc,
                        "date": parts[0],
                    })
            except IndexError:
                continue

        # Emit notifications
        notified = []
        for filing in new_filings:
            msg = (
                f"IPO Filing Detected: {filing['form']} for CIK {filing['cik']} "
                f"(Accession: {filing['accession']})"
            )
            # Emit via standard notification path
            ctx.notify("ipo_alert", {
                "filing": filing,
                "message": msg,
            })
            notified.append(filing)

        return {
            "status": "checked",
            "date": today,
            "count": len(notified),
            "filings": notified,
        }

    return sec_new_filings
