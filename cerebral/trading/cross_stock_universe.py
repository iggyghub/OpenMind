"""Basket and classification logic for CROSS-STOCK-VALIDATION S1."""
import re
from typing import List

from .strategy_store import StrategySpec

# 100-stock basket, deduplicated and ordered exactly as user-confirmed.
_BASKET_RAW = """
AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AMD, NFLX, CRM,
INTC, MU, QCOM, AVGO, TXN, AMAT, ASML, ARM, ON, MRVL,
PYPL, SQ, SOFI, COIN, AFRM, V, MA, AXP,
JPM, BAC, GS, MS, WFC, C, SCHW, BLK,
WMT, COST, HD, TGT, NKE, SBUX, MCD, LOW, TJX,
DAL, UAL, AAL, CCL, ABNB, BKNG, MAR,
UBER, LYFT, DASH,
RIVN, LCID, NIO, F, GM,
UNH, PFE, JNJ, LLY, MRNA, BNTX, ABBV, MRK,
XOM, CVX, OXY, SLB, COP,
BA, CAT, GE, HON, DE, LMT,
DIS, CMCSA, T, VZ, WBD,
SNAP, PINS, RBLX, DKNG, ROKU, UPST,
MARA, RIOT,
ORCL, ADBE, NOW, PANW, SNOW, SHOP, PLTR,
KO, PEP, PG, MDLZ,
FCX, NEM,
O, SPG,
NEE, DUK,
SPY, QQQ, IWM, DIA, XLF, XLE, XLK, XLV, XLI, GLD
"""

BASKET: List[str] = list(dict.fromkeys(s.strip() for s in _BASKET_RAW.replace(",", "\n").splitlines() if s.strip()))

assert len(BASKET) == 100, f"BASKET must contain exactly 100 unique symbols, got {len(BASKET)}"
assert len(set(BASKET)) == 100


def is_stock_specific(spec: StrategySpec) -> bool:
    """Return True if the strategy's code explicitly names a stock/basket ticker,
    making it stock-specific and thus ineligible for the 100-stock generic sweep.
    Errs toward stock-specific on ambiguity per SAFETY rules."""
    tickers = BASKET + [spec.symbol]
    pattern = re.compile(r'\b(' + '|'.join(re.escape(t) for t in tickers) + r')\b', re.IGNORECASE)
    return bool(pattern.search(spec.code))
