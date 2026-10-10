# Fast drop trigger -> short the most volatile, weakest S&P 500 stocks (pre-registered 2026-10-10)

User ask 2026-10-10: "just try it if we could use this strategy while shorting" -- i.e. assume a
margin account (Alpaca needs $2k equity and whole shares to short; not possible on the $100 paper
account today). The earlier short basket (SHORT-BASKET-PLAN.md) used the slow breadth<45% trigger;
this uses the FAST triggers from VOL-DROP-PLAN.md with %-based exits.

## Setup
- Universe: S&P 500 point-in-time members (`research_sp500_close.pkl` / `research_sp500_hist.csv`).
  Closes only, so entries, exits and the TP/SL checks all happen at the daily close.
- Triggers (signal at day t's close): A = SPY down 2%+ on the day; B = S&P breadth falls 15+
  points over 3 days.
- Picks: the members with the most negative 20-day return x 20-day volatility (the mirror of the
  live basket's ranking), as of day t's close.
- Trade: short at day t+1's close, 10% of equity each, at most 10 open shorts, one per stock.
  Each trigger day fills free slots.
- Exit (checked at each close): take profit when the stock is TP% below entry, stop when it is SL%
  above entry, else at the close after H trading days. Delisted / no price for 5 days: closed at
  the last price.
- Costs: 5 bps per side. Borrow fees ignored (would make results worse).

## Grid, chosen on the first half only
TP {5, 10, 20}% x SL {5, 10}% x H {3, 10} days x trigger {A, B} = 24 settings.
Choose the best average bet on 2005-01..2016-01 (>= 20 bets); test untouched on 2016-01..2026-09.

## Pass bar
1. The chosen setting's average bet > 0 on 2016-26, with >= 20 bets.
2. Its account (10% slots, cash otherwise) grows on 2016-26.
3. Rank agreement (Spearman) of the 24 settings' average bet between halves >= 0.3.

Reference: SPY buy & hold per half.
