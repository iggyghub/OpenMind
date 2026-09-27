"""Sector + industry for every priced S&P 500 member (yfinance Ticker.info). Saves industry_map.pkl."""
import pickle
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf

closes = pickle.load(open("sp500_close.pkl", "rb"))


def info(t):
    try:
        i = yf.Ticker(t.replace(".", "-")).info
        return t, (i.get("sector"), i.get("industry"))
    except Exception:
        return t, (None, None)


with ThreadPoolExecutor(8) as ex:
    result = dict(ex.map(info, list(closes)))
pickle.dump(result, open("industry_map.pkl", "wb"))
ind = Counter(v[1] for v in result.values() if v[1])
print("industries:", len(ind), "| with >= 8 companies:", sum(1 for n in ind.values() if n >= 8))
