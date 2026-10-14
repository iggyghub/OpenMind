"""Sector for every priced S&P 500 member (yfinance Ticker.info). Saves sector_map.pkl."""
import pickle
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf

closes = pickle.load(open("sp500_close.pkl", "rb"))


def sector(t):
    try:
        return t, yf.Ticker(t.replace(".", "-")).info.get("sector")
    except Exception:
        return t, None


with ThreadPoolExecutor(8) as ex:
    result = dict(ex.map(sector, list(closes)))
pickle.dump(result, open("sector_map.pkl", "wb"))
found = {k: v for k, v in result.items() if v}
print("sectors found:", len(found), "of", len(result))
from collections import Counter
print(Counter(found.values()).most_common())
