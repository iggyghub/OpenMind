"""Basket and classification logic for CROSS-STOCK-VALIDATION S1."""
import re
from typing import List, Optional

from .strategy_store import StrategySpec, StrategyStore

# 100-stock basket, deduplicated and ordered exactly as user-confirmed.
# The original hand-typed list totaled 119 unique symbols, not 100 as
# claimed (miscounted when first drafted in conversation) -- this test's
# own len(BASKET)==100 assertion caught it, self_dev correctly self-
# blocked rather than force-merging. Trimmed 19 symbols below, each from
# a category that already has multiple representatives, to hold sector
# diversity while landing on exactly 100 (see CROSS-STOCK-VALIDATION.md
# S1 landed-PR note for the removed list).
_BASKET_RAW = """
AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AMD, NFLX, CRM,
INTC, MU, QCOM, AVGO, TXN, AMAT, ON, MRVL,
PYPL, SQ, SOFI, COIN, AFRM, V, MA, AXP,
JPM, BAC, GS, MS, WFC, C,
WMT, COST, HD, TGT, NKE, SBUX, MCD,
DAL, UAL, AAL, CCL, ABNB,
UBER, LYFT, DASH,
RIVN, LCID, NIO, F, GM,
UNH, PFE, JNJ, LLY, MRNA, MRK,
XOM, CVX, OXY,
BA, CAT, GE, HON,
DIS, CMCSA, T, VZ,
SNAP, PINS, RBLX, DKNG, ROKU, UPST,
MARA, RIOT,
ORCL, ADBE, SNOW, SHOP, PLTR,
KO, PEP, PG,
FCX,
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
    Errs toward stock-specific on ambiguity per SAFETY rules.

    Case-SENSITIVE match, deliberately: several real tickers (ON, GE, F, T, C,
    MA, V) are also common lowercase English words ("based on", "a", "the C
    programming language"...), and this codebase's own strategy descriptions
    always write real ticker mentions in caps (see StrategyStore.list_all()
    output). Case-insensitive matching was tried first and immediately
    misclassified a strategy whose only text was "...based on interest
    rates" as ON-specific -- catching that false positive is the reason this
    note exists, not a hypothetical."""
    tickers = BASKET + [spec.symbol]
    pattern = re.compile(r'\b(' + '|'.join(re.escape(t) for t in tickers) + r')\b')
    return bool(pattern.search(spec.code))


def classify_all_strategies(store: Optional[StrategyStore] = None) -> int:
    """One-shot classification pass (S1's 4th deliverable): runs
    is_stock_specific over every registered strategy that hasn't been
    classified yet and persists the result. Idempotent -- only touches
    specs where cross_test_eligible is still None, so re-running after
    new strategies register never re-classifies (or overwrites a manual
    correction on) an already-classified one. Returns the count newly
    classified."""
    _store = store if store is not None else StrategyStore()
    classified = 0
    for spec in _store.list_all():
        if spec.cross_test_eligible is not None:
            continue
        _store.update_cross_test_eligible(spec.strategy_id, not is_stock_specific(spec))
        classified += 1
    return classified
