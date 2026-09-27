# Buy the dip: pre-registered plan (written 2026-09-27, BEFORE any results)

Question: when a sector has fallen X% from its high, is it a good time to buy? And how far do
sectors usually fall before turning back up?

## Data
9 original SPDR sector funds (XLB XLE XLF XLI XLK XLP XLU XLV XLY) + SPY, daily, 1999-2026,
dividend-adjusted (yfinance). Price only, no news.

## 1. How deep do dips go? (descriptive)
For each fund, every decline episode that reaches at least 10% below its prior all-time high,
ending when the fund makes a new high. Report the final depth (median, quartiles) and the share of
10% dips that went on to 20%+ and 30%+ before recovering.

## 2. Does being down X% predict the next months? (the real test)
At every month-end, bucket each fund by its drawdown from its trailing 12-month high:
0-5%, 5-10%, 10-15%, 15-20%, 20-30%, 30%+. For each bucket: average forward return over the next
3, 6 and 12 months, minus the average of the other 8 funds over the same window (so a
market-wide crash doesn't count as a sector signal). Also the raw forward return. t-stats are
overlap-adjusted (divide n by the horizon in months). Reported for 1999-2012 and 2013-2026 as well.

## 3. Tradeable rule (fixed now; thresholds 10%, 15%, 20% all reported)
Each month-end: hold, equal-weight, only the funds that are down >= X% from their 12-month high.
If none qualify, hold all 9 equal-weight. Signal at month M's close, earns month M+1. Cost 2 bps
per side on turnover.
Compared against: all 9 funds equal-weight (monthly rebalance), and SPY.

Pass bar: AFTER costs, beats BOTH equal-weight and SPY on annual return in BOTH halves
(2000-2012 and 2013-2026). Max drawdown and Sharpe are reported but don't rescue a fail.
Anything else is "no edge". No variants added after seeing results; any later one is labelled
post-hoc.
