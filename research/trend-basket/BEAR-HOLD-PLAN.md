# Bear-regime holding: does putting idle cash into SH / TLT / GLD during weak breadth help? (pre-registered 2026-10-09)

User question 2026-10-09: can the strategy make money off drops without shorting? Long-only
candidates that tend to rise when stocks fall: SH (inverse S&P 500, 1x), TLT (long Treasuries),
GLD (gold). Leveraged inverse funds, VIX products and managed-futures ETFs are excluded
(decay / too little history).

## Setup (fixed before running)
- Engine: `sp500_backtest.py` unchanged -- point-in-time S&P 500, the LIVE trend basket rule
  (cross 55%, refill while >50%), 10 slots x 10%, 12% trail, 20-day cap, close fills,
  2 bps/side, signal + picks from the prior close.
- Bear regime: turns ON when S&P breadth crosses below 45%, stays ON while breadth < 50%
  (the mirror of the live 55/50 trigger/sustain). Signal from the prior close.
- While ON: all idle cash (not in basket positions) is held in the asset, bought at the close
  with 2 bps cost. Sold at the close the day the regime turns OFF. Basket slots that free up
  during the regime also go into the asset (rebalanced daily to "all idle cash").
- Baseline: the same basket with idle cash in cash (0%).
- Periods: 2006-07-01..2016-01-01 and 2016-01-01..2026-09-01 (SH starts 2006-06), plus full.

## Pass bar (all three, per asset)
1. CAGR above the baseline in BOTH halves.
2. Full-period max drawdown no worse than the baseline's (1 point tolerance).
3. The bear stints themselves average a positive return (asset return per ON stretch).

Three assets are tested, so one passing by luck is plausible; a pass on only one asset in one
half is a fail.
