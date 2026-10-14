# Does sector news shift BEFORE sector turns? Pre-registered plan (written 2026-09-27, BEFORE any results)

Question: over weeks/months, does a sector's news tone or news volume change before that sector
flips between bull and bear? (Different from NEWS-TEST-PLAN.md, which asked whether today's tone
predicts tomorrow's return. It didn't.)

## Data
- Headlines + FinBERT scores: same as NEWS-TEST-PLAN.md (news_history.db, news_scores.db),
  2016-01 .. 2026-09. No-hindsight rule: published at/after 16:00 ET counts toward the next day.
- Sector of each stock: research_sector_map.pkl. Point-in-time S&P 500 membership:
  research_sp500_hist.csv. An (article, stock) pair counts for a sector only if the stock was a
  member that day.
- Sector prices: the 9 original SPDR funds (from 1999, so the phase state in 2016 is known).
  Technology=XLK, Financial Services=XLF, Energy=XLE, Healthcare=XLV, Industrials=XLI,
  Basic Materials=XLB, Consumer Defensive=XLP, Utilities=XLU, Consumer Cyclical=XLY.
  Communication Services and Real Estate are left out (no fund with long enough history).

## Monthly sector measures (fixed now)
For each sector and calendar month (by signal day):
1. **Tone**: mean score over the sector's (article, member) pairs.
2. **Volume**: log of the pair count.
3. **Relative tone**: sector tone minus the all-S&P tone that month (removes market-wide mood).
Each is turned into an "abnormal" value: this month minus the mean of the prior 12 months
(minimum 6). Abnormal values are what get tested.

## Turning points (fixed now)
- **20% rule** (as in sector_regimes.py): a 20% fall from a peak starts a bear, a 20% rise from a
  trough starts a bull. The turn date is the PEAK (bull->bear) or TROUGH (bear->bull) itself, i.e.
  the real top/bottom, not the later confirmation day.
- **200-day rule**: the day the fund's close crosses its 200-day average AND stays on the new side
  for 20+ trading days. Down-cross = bull->bear, up-cross = bear->bull.
- An event is used only if its 6 months before have abnormal values (turns from ~mid-2017 on).

## Event study (test b)
For each turn type (bull->bear, bear->bull), each turn rule, and each measure: average the
abnormal measure over months -3..-1 and -6..-4 before the turn month (one number per event).
Compare with that measure's average over all sector-months (the "normal times" baseline).
t = (event mean - baseline) / (event sd / sqrt(n events)).

"Shifts before turns" = |t| >= 2, expected direction for tone/relative tone (falls before
bull->bear, rises before bear->bull; volume is two-sided), AND the same sign when events are split
into 2016-2020 and 2021-2026 by turn date. 24 tests run (3 measures x 2 windows x 2 turn types x
2 rules), so ~1 false |t| >= 2 is expected by chance; the report says so. Events in the same month
across sectors (e.g. March 2020) are not independent; the report lists how many distinct months
the events fall in.

## Tradeable check (test c) -- run regardless of (b), rule fixed now
Each month-end, for each of the 9 funds: hold it next month if its 3-month tone (pair-weighted,
last 3 months) >= its 12-month tone (pair-weighted, last 12 months); otherwise that slot is in cash
(0% return). Needs 12 months of news, so trading starts 2017-01. Signal at month M's last close,
earns month M+1. Cost 2 bps per side on turnover.
Compared against: the same 9 funds equal-weight held all the time (monthly rebalance), and SPY.

Pass bar: AFTER costs, the rule's portfolio beats BOTH equal-weight hold and SPY on annual return
in BOTH halves (2017-2020 and 2021-2026). Max drawdown and Sharpe are reported but don't rescue a
fail. Anything else is "no edge".

No variants added after seeing results; any later one is labelled post-hoc.
