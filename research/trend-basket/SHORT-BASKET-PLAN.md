# Short basket: pre-registered plan (written 2026-09-29, BEFORE any results)

Question: when the market trend is down, do short bets on the weakest stocks average positive?

Mirror of the live trend basket, same data and mechanics as sp500_backtest.py (S&P 500 as it was
each day, 10 slots x 10%, close fills, signal + picks from the prior close, 20-day cap):
- Trigger: S&P breadth (share of members above their 50-day average) crosses BELOW 45%;
  keep refilling while it stays below 50%.
- Picks: the LOWEST momentum x volatility score (falling hardest, most volatile).
- Stop: 12% trailing stop upward (out when price rises 12% above its lowest close since entry).
- Costs: 2 bps per side, plus 0.5%/yr borrow on the shorted amount.
- Bet return = entry / exit - 1 (what a short earns).

Pass bar: the average short bet is positive after costs in BOTH halves (2005-2015 and 2016-2026).
Also reported: winners %, and the short-only account's growth per year in each half.
Known bias: companies that went bankrupt are the most likely to be missing from free price data,
and they're the best shorts, so this test leans slightly against shorting.
No variants added after seeing results; any later one is labelled post-hoc.
