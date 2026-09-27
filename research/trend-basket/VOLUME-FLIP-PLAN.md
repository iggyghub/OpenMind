# News-volume flip: plan (written 2026-09-27, BEFORE running; POST-HOC by origin)

The thresholds come from sector_turns.py's post-hoc check (abnormal news volume ~+10% in bear
phases, ~+2% in bull phases), so this whole test is post-hoc by origin even though the rule below
is fixed before running it. Treat a pass as a lead to re-test on new data, not as proof.

## Measure
Same as SECTOR-TURNS-PLAN.md: each sector's monthly abnormal news volume = log(pair count) minus
the mean of the prior 12 months (min 6). 9 sectors / 9 SPDR funds, 2016-2026.

## Rule (fixed now)
Per sector, a state machine at each month-end:
- In the fund by default.
- Abnormal volume >= +0.10 -> sell (alarm).
- After an alarm, abnormal volume <= +0.02 -> buy back.
Signal at month M's close, earns month M+1. Cash earns 0. 2 bps per side.
Portfolio = the 9 slots equal-weight. Compared with all 9 held and SPY. Trading 2017-01..2026-08.

Pass bar: after costs, beats both on annual return in both 2017-2020 and 2021-2026.

## Timing check (descriptive)
Around each 20%-rule trough (same events as sector_turns.py): the mean abnormal volume in months
-6..+6, and for each trough, how many months after it the buy-back signal fired and how much the
fund had already risen by then.
