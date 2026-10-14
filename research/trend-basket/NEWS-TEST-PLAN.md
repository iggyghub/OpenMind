# News -> where-to-be test: pre-registered plan (written 2026-09-25, BEFORE any results)

Goal: does news tone tell us which part of the market to be in, for DAILY profits, in a way that
works at any account size (percent returns, fractional shares / funds)?

## Data
- Headlines: every Benzinga article via Alpaca news API, 2016-01-01 .. 2026-09-24 (~2.6M),
  cerebral/data/news_history.db. Scored by FinBERT (pre-2014 training data, so no knowledge of
  later prices): score = P(pos) - P(neg), cerebral/data/news_scores.db.
- Prices: S&P 500 point-in-time members (sp500_close.pkl), SPY.
- No-hindsight rule: an article published at or after 16:00 ET counts toward the NEXT trading day.
  A signal formed from day D's news trades at day D's close and earns day D+1's return.

## Categories (fixed now)
1. 11 sectors (yfinance sector of each member).
2. 26 industries with >= 8 member companies.
3. 9 headline-keyword themes: AI; crypto/bitcoin; EV/electric vehicle; oil/crude/OPEC;
   interest rates/Fed; tariffs/trade war; earnings beat / earnings miss; FDA/approval;
   merger/acquisition/buyout. (A theme's "stocks" = the symbols tagged on its articles.)

## Tests
1. Stock level (does the signal exist at all?): each day, sort stocks with news by their average
   headline score into 5 groups; average next-day return of top group minus bottom group.
   Also 5-day and 20-day horizons. Report by year.
2. Category level, daily: each day rank sectors (then industries, then themes) by prior-day
   average score (min 5 articles); hold the top 3 equal-weight next day (members of that category,
   equal weight). Compare vs holding all categories equal-weight, and vs SPY.
3. Same as 2, weekly rebalance (score over the past 5 days), because daily turnover costs are high.

## Costs
2 bps per side on turnover (same as every earlier test). Daily rotation turns over a lot; the
result is reported both before and after costs.

## Pass bar (decided now)
A variant counts only if, AFTER costs, it beats BOTH SPY and its own equal-weight baseline in BOTH
halves (2016-2020 and 2021-2026). Anything else is reported as "no edge". No new variants get
added after seeing results; if one is added later, it's labeled as post-hoc.
