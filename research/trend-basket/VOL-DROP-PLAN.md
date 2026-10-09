# Fast drop trigger -> high-volatility long bets (3x inverse / VIX), 10% slots (pre-registered 2026-10-09)

User ask 2026-10-09: profit from drops without shorting, high volatility, "the 10% plan in whatever
stretch of time it fits". The 1x-inverse test (BEAR-HOLD-PLAN.md) failed because the breadth<45%
trigger fires after the fall; this tests FAST triggers with %-based exits instead.

## Instruments (long only, bought like any stock)
SQQQ (-3x Nasdaq 100), SPXU (-3x S&P 500), SOXS (-3x semiconductors), UVXY (VIX futures, 1.5x).
Daily adjusted OHLC from yfinance. Common history from 2011-11-01.

## Triggers (signal at day t's close)
- A: SPY closes down 2% or more on the day.
- B: S&P 500 breadth (% above 50-day average, point-in-time members) falls 15+ points over 3 days.

## Trade
- Buy at the NEXT day's open, 10% of current equity, at most 10 open bets; no new bet in the same
  fund while one is open. Cash otherwise.
- Exit at the first of: take-profit TP% (day's high touches it), stop SL% (day's low touches it;
  if both touch on one day, assume the stop -- conservative), or the close after H trading days.
- Costs: 5 bps per side.

## Settings grid (chosen on the first half only)
TP in {5, 10, 20}%, SL in {5, 10}%, H in {3, 10} days -> 12 exit settings x 2 triggers x 4 funds = 96.
- Choose: the single setting with the best average bet in 2011-11..2018-12 (at least 20 bets).
- Test it untouched on 2019-01..2026-09.

## Pass bar
1. The chosen setting's average bet is above 0 on 2019-2026, with at least 20 bets there.
2. Its account (10% slots, cash otherwise) grows on 2019-2026 (CAGR > 0).
3. Rankings carry over: report the rank correlation of all 96 settings' average bet between halves;
   below 0.3 means the choice is mostly noise and the result is a fail regardless of 1-2.

Reference only: SPY buy & hold over each half.

## Results (run 2026-10-09, `vol_drop.py`) -- FAIL on all three bars

- Trigger days 2011-26: A (SPY -2% day) 110, B (breadth -15 pts in 3 days) 235.
- Of 96 settings, average bet positive: 18 in 2011-18, 11 in 2019-26. Median average bet
  -0.89% and -1.03%. Rank agreement between halves -0.26 (the first half's winners tended to be
  the second half's losers).
- Chosen on 2011-18: trigger A, UVXY, TP 10% / SL 5% / 3 days: 36 bets, +1.49% avg.
  On 2019-26: 71 bets, -1.87% avg, 23% winners, account -1.7%/yr.
- Post-hoc (hindsight, not a valid pick): the best 2019-26 settings averaged only +0.3..+0.5% per
  bet, and every one of them lost in 2011-18.
- SPY buy & hold for reference: +12.8%/yr (2011-18), +17.4%/yr (2019-26).

Why: a -2% day or a fast breadth drop is followed by a rebound about as often as by more
falling, and 3x / VIX funds lose fast in a rebound. The +10% target is hit less often than the
-5% stop.
