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

## Results (run 2026-10-09, `bear_hold.py`)

Bear regime ON 28% of days 2006-2026; the basket's buy regime ON 66%.

| idle cash in weak breadth | 2006-15 CAGR | 2016-26 CAGR | full CAGR | full maxDD | bear stints (avg / winners) | verdict |
|---|---|---|---|---|---|---|
| cash (baseline) | +5.5% | +6.1% | +5.6% | -55% | -- | -- |
| SH (inverse S&P) | +0.1% | +1.1% | +0.3% | -67% | -0.94% / 23% | FAIL |
| TLT (bonds) | +8.2% | +4.5% | +5.9% | -47% | +0.27% / 45% | FAIL (2016-26) |
| GLD (gold) | +8.3% | +12.0% | +9.9% | -52% | +0.96% / 59% | PASS |
| SPY buy & hold | +7.2% | +15.2% | +11.3% | -55% | -- | reference |

SH loses because weak-breadth stretches usually start after the fall and end in the rebound:
121 stints, 23% winners.

**Post-hoc control (not pre-registered).** Is it the timing or just gold rising? GLD buy & hold
2006-26 = +9.8%/yr. Gold in idle cash whenever the basket isn't buying = +9.3%. The same number
of gold days placed at random times (100 circular shifts) = median +6.5%, 10-90% +5.2..+7.7%,
0 of 100 reached the tested rule's +9.9%. So holding gold specifically while breadth is weak
beats random gold days. Gold tended to rise when stocks were weak in both halves (2008-11,
2022-26). That is the bet: it keeps being a crisis hedge. Still below SPY (+11.3%).
