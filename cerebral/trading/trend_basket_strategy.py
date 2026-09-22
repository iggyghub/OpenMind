"""
TREND_BASKET_STRATEGY: Hand-written trend-basket strategy.

Entry: Bar 0 of the input data is the entry bar.
Exit: Flat 12% pullback from tracked peak High (peak * 0.88).
      No tightening ratchet.
Hard Limit: Flat exit once bar index reaches 20.
Stop Order: Check stop before updating peak to prevent same-bar
            retroactive stop tripping (peak from end of i-1 is used).
"""

TREND_BASKET_STRATEGY_CODE = '''
entry_price = data["Open"].iloc[0]
peak = data["High"].iloc[0]
position = 0

for i in range(1, len(data)):
    # Stop check BEFORE peak update (uses peak from end of i-1)
    if position == 1 and data["Close"].iloc[i] < peak * 0.88:
        position = 0

    # Update peak AFTER stop check
    if data["High"].iloc[i] > peak:
        peak = data["High"].iloc[i]

    # Hard exit at bar index 20
    if i == 20:
        position = 0
'''
