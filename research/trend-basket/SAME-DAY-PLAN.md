# Same-day %-exit trades: pre-registered plan (written 2026-09-29, BEFORE any results)

The user's intended style: same-day trades on volatile stocks, exits by % move, not by days held.

## Data
5-minute bars (cerebral/data/bars.db, interval 5m, times in New York local), 30 stocks, 2020-2026:
AAPL AFRM AMAT AMD AMZN AVGO AXP BAC COIN CRM GOOGL GS INTC JPM MA META MRVL MS MSFT MU NFLX NVDA ON
PYPL QCOM SOFI SQ TSLA TXN V. Known bias: this list was picked recently (survivors, mostly winners
of 2020-2026), which flatters any buy-side rule.

## Rule
Each trading day, regular session only (09:30-16:00):
- Pick the 10 most volatile of the 30 (20-day std of daily close-to-close returns, prior days only).
- Buy each at the close of the 09:30 bar (i.e. at 09:35), 10% of the account each.
- Take profit at +X% (filled at exactly +X% when a bar's high reaches it); stop at -Y% (filled at
  -Y%, or at the bar's open if it opens past the stop). If both happen in one bar, count the stop.
- Otherwise sell at the close of the 15:55 bar. Nothing held overnight.
- Cost 2 bps per side.
Grid: X and Y each in {1, 2, 3, 5}% (16 combinations).

## Method and pass bar
Pick ONE (X, Y) on 2020-2022 by the account's average daily return. Run it once on 2023-2026.
Pass, in 2023-2026, ALL of: account average day > 0 after costs with t >= 2; beats simply holding
the same 10 stocks 09:35 -> close with no % exits; and the account grows per year by more than 5%.
Also reported: per-trade average, winners %, share of trades hitting target / stop / close,
worst drop, and rank agreement of the 16 settings between halves.
Secondary (reported, not the pass test): the same chosen rule only on days the live breadth gate
would be on (S&P breadth crossed 55%, still above 50%).
Any later variant is labelled post-hoc.
