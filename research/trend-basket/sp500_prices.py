"""Download daily adjusted closes for every S&P 500 member since 2004 (point-in-time list from
github.com/fja05680/sp500). Saves prices to sp500_close.pkl and reports coverage."""
import pickle
import pandas as pd
import yfinance as yf

se = pd.read_csv("sp500_ticker_start_end.csv", parse_dates=["start_date", "end_date"])
se = se[(se["end_date"].isna()) | (se["end_date"] >= "2004-01-01")]
tickers = sorted(set(se["ticker"]))
print("tickers ever members since 2004:", len(tickers), flush=True)

closes = {}
for i in range(0, len(tickers), 100):
    batch = tickers[i:i + 100]
    yf_names = {t.replace(".", "-"): t for t in batch}
    df = yf.download(list(yf_names), start="2004-01-01", auto_adjust=True, progress=False, threads=True)
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": batch[0]})
    for col in close.columns:
        s = close[col].dropna()
        if len(s) > 60:
            closes[yf_names.get(col, col)] = s
    print(f"{i + len(batch)}/{len(tickers)} done, {len(closes)} with data", flush=True)

pickle.dump(closes, open("sp500_close.pkl", "wb"))
missing = sorted(set(tickers) - set(closes))
print("with data:", len(closes), "missing:", len(missing))
print("missing sample:", missing[:40])
