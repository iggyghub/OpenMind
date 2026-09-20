"""True intraday rules for day-trading research (2026-09-20).

The book-extracted library holds no real day-trading rules: the interval guess falls back to "1d", so
rules such as Aziz's pre-market gap scans or "13-bar Bollinger for intraday charts" were backtested on
daily bars, which tests nothing. These are hand-authored 5-minute rules for regular-session bars
(09:30-15:55 New York), each flat by 15:50 -- day trading, no overnight risk.

Contract (same as every stored strategy): source of `def strategy(data) -> list of -1/0/1`, one per bar,
decided at that bar's CLOSE and held over the NEXT bar (replay shifts by one). `pd` and `np` are
pre-imported in the sandbox. Every rule gates on the time of day, so a bar never reads a value that was
not known when it closed; cerebral.trading.causality re-checks that on real bars.

Provenance (the claim each encodes):
  ORB            -- Crabel opening-range breakout; Aziz, "How to Day Trade for a Living"
  VWAP reversion -- Aziz / institutional benchmark: price stretches from VWAP and snaps back
  VWAP trend     -- Aziz: trade with VWAP, long above a rising VWAP
  Gap-and-go     -- Aziz: stock gaps >1% and holds the opening range
  Gap fade       -- the opposing claim: a gap that fails its opening range fills
  Bollinger      -- book rule "13-bar, 2 std dev Bollinger Band for intraday charts", on real intraday bars
  Half-hour mom. -- Gao, Han, Li, Zhou, "Market intraday momentum": first half hour predicts the last
  EMA9/20 + VWAP -- Aziz: 9/20 EMA trend confirmed by VWAP

Informational only -- never feeds check_graduation, check_retirement, run_gauntlet or auto_promote.
"""

# Shared prelude: minutes since midnight (570 = 09:30, 600 = 10:00, 945 = 15:45), calendar day, OHLCV.
_PRELUDE = '''
    idx = data.index
    mins = idx.hour * 60 + idx.minute
    day = idx.date
    c = data['Close']
    h = data['High']
    l = data['Low']
    v = data['Volume']
'''
_VWAP = '''
    tp = (h + l + c) / 3
    vwap = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum()
'''
# Previous regular-session close, mapped onto every bar of the next day (known before the day opens).
_PREV_CLOSE = '''
    dayc = c.where(mins == 955).groupby(day).transform('max')
    prevc = pd.Series(dayc.groupby(day).first().shift(1).reindex(day).values, index=idx)
'''

_ORB = '''def strategy(data):''' + _PRELUDE + '''
    in_or = (mins >= 570) & (mins < 600)
    orh = h.where(in_or).groupby(day).transform('max')
    orl = l.where(in_or).groupby(day).transform('min')
    raw = pd.Series(np.nan, index=idx)
    raw[(mins >= 600) & (c > orh)] = 1.0
    raw[(mins >= 600) & (c < orl)] = -1.0
    raw = raw.groupby(day).ffill().fillna(0.0)
    raw[(mins < 600) | (mins >= 945)] = 0.0
    return raw.astype(int).tolist()
'''

_VWAP_REVERSION = '''def strategy(data):''' + _PRELUDE + _VWAP + '''
    dev = c - vwap
    sd = dev.groupby(day).expanding(12).std().droplevel(0)
    z = (dev / sd).values
    out = np.zeros(len(z), dtype=int)
    pos = 0
    prev = None
    for i in range(len(z)):
        if day[i] != prev:
            pos = 0
            prev = day[i]
        if mins[i] < 600 or mins[i] >= 945 or np.isnan(z[i]):
            pos = 0
        elif pos == 0:
            if z[i] < -2:
                pos = 1
            elif z[i] > 2:
                pos = -1
        elif (pos == 1 and z[i] >= 0) or (pos == -1 and z[i] <= 0):
            pos = 0
        out[i] = pos
    return out.tolist()
'''

_VWAP_TREND = '''def strategy(data):''' + _PRELUDE + _VWAP + '''
    rising = vwap > vwap.groupby(day).shift(6)
    falling = vwap < vwap.groupby(day).shift(6)
    sig = pd.Series(0, index=idx)
    sig[(c > vwap) & rising] = 1
    sig[(c < vwap) & falling] = -1
    sig[(mins < 600) | (mins >= 945)] = 0
    return sig.astype(int).tolist()
'''

_GAP_AND_GO = '''def strategy(data):''' + _PRELUDE + _PREV_CLOSE + '''
    dayo = data['Open'].where(mins == 570).groupby(day).transform('max')
    gap = dayo / prevc - 1.0
    in_or = (mins >= 570) & (mins < 585)
    orh = h.where(in_or).groupby(day).transform('max')
    orl = l.where(in_or).groupby(day).transform('min')
    raw = pd.Series(np.nan, index=idx)
    up = gap > 0.01
    down = gap < -0.01
    raw[(mins >= 585) & up & (c > orh)] = 1.0
    raw[(mins >= 585) & up & (c < orl)] = 0.0
    raw[(mins >= 585) & down & (c < orl)] = -1.0
    raw[(mins >= 585) & down & (c > orh)] = 0.0
    raw = raw.groupby(day).ffill().fillna(0.0)
    raw[(mins < 585) | (mins >= 945)] = 0.0
    return raw.astype(int).tolist()
'''

_GAP_FADE = '''def strategy(data):''' + _PRELUDE + _PREV_CLOSE + '''
    dayo = data['Open'].where(mins == 570).groupby(day).transform('max')
    gap = (dayo / prevc - 1.0).values
    in_or = (mins >= 570) & (mins < 585)
    orh = h.where(in_or).groupby(day).transform('max').values
    orl = l.where(in_or).groupby(day).transform('min').values
    cv = c.values
    pc = prevc.values
    out = np.zeros(len(cv), dtype=int)
    pos = 0
    prev = None
    for i in range(len(cv)):
        if day[i] != prev:
            pos = 0
            prev = day[i]
        if mins[i] < 585 or mins[i] >= 945 or np.isnan(gap[i]) or np.isnan(pc[i]):
            pos = 0
        elif pos == 0:
            if gap[i] > 0.01 and cv[i] < orl[i]:
                pos = -1
            elif gap[i] < -0.01 and cv[i] > orh[i]:
                pos = 1
        elif (pos == -1 and cv[i] <= pc[i]) or (pos == 1 and cv[i] >= pc[i]):
            pos = 0
        out[i] = pos
    return out.tolist()
'''

_BOLLINGER = '''def strategy(data):''' + _PRELUDE + '''
    m = c.groupby(day).rolling(13).mean().droplevel(0)
    s = c.groupby(day).rolling(13).std().droplevel(0)
    lo = (m - 2 * s).values
    hi = (m + 2 * s).values
    mid = m.values
    cv = c.values
    out = np.zeros(len(cv), dtype=int)
    pos = 0
    prev = None
    for i in range(len(cv)):
        if day[i] != prev:
            pos = 0
            prev = day[i]
        if mins[i] < 600 or mins[i] >= 945 or np.isnan(mid[i]):
            pos = 0
        elif pos == 0:
            if cv[i] < lo[i]:
                pos = 1
            elif cv[i] > hi[i]:
                pos = -1
        elif (pos == 1 and cv[i] >= mid[i]) or (pos == -1 and cv[i] <= mid[i]):
            pos = 0
        out[i] = pos
    return out.tolist()
'''

_HALF_HOUR = '''def strategy(data):''' + _PRELUDE + _PREV_CLOSE + '''
    first = c.where(mins == 595).groupby(day).transform('max')
    r = first / prevc - 1.0
    sig = pd.Series(0, index=idx)
    late = (mins >= 925) & (mins < 950)
    sig[late & (r > 0)] = 1
    sig[late & (r < 0)] = -1
    return sig.astype(int).tolist()
'''

_EMA_VWAP = '''def strategy(data):''' + _PRELUDE + _VWAP + '''
    e9 = c.groupby(day).transform(lambda s: s.ewm(span=9, adjust=False).mean())
    e20 = c.groupby(day).transform(lambda s: s.ewm(span=20, adjust=False).mean())
    sig = pd.Series(0, index=idx)
    sig[(e9 > e20) & (c > vwap)] = 1
    sig[(e9 < e20) & (c < vwap)] = -1
    sig[(mins < 600) | (mins >= 945)] = 0
    return sig.astype(int).tolist()
'''

INTERVAL = "5m"
SYMBOL = "AAPL"   # the spec's home symbol; research runs each rule across the whole basket

RULES = {
    "Intraday: opening-range breakout -- after the first 30 minutes go long above the range high or short below the range low, hold to 15:45, flat by the close": _ORB,
    "Intraday: VWAP reversion -- fade a 2 standard deviation stretch from the session VWAP back to VWAP, flat by the close": _VWAP_REVERSION,
    "Intraday: VWAP trend -- long while price is above a rising VWAP, short below a falling VWAP, flat by the close": _VWAP_TREND,
    "Intraday: gap-and-go -- a gap over 1% that holds its first-15-minute range continues in the gap direction to the close": _GAP_AND_GO,
    "Intraday: gap fade -- a gap over 1% that breaks its first-15-minute range the other way fills back to the prior close": _GAP_FADE,
    "Intraday: 13-bar 2 standard deviation Bollinger reversion on 5-minute bars, exit at the middle band, flat by the close": _BOLLINGER,
    "Intraday: first-half-hour momentum -- trade the last half hour in the direction of the return from the prior close to 10:00": _HALF_HOUR,
    "Intraday: 9/20 EMA trend confirmed by VWAP, flat by the close": _EMA_VWAP,
}


def register_intraday_rules(strategy_store) -> int:
    """Save every rule not already stored (idempotent). Deliberately NOT cross_test_eligible: the daily
    cross-stock sweep runs eligible specs on daily-cache bars; these have their own runner. Returns how many
    were added."""
    from cerebral.trading.strategy_store import StrategySpec
    have = {s.strategy_id for s in strategy_store.list_all()}
    n = 0
    for claim, code in RULES.items():
        if claim in have:
            continue
        strategy_store.save(
            StrategySpec(strategy_id=claim, symbol=SYMBOL, code=code, qty=1.0, interval=INTERVAL,
                         cross_test_eligible=False),
            origin="generated",
            provenance_json={"source": "hand-authored intraday rule (2026-09-20)"},
            hypothesis=claim,
        )
        n += 1
    return n
