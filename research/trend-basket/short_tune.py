"""Tune the short basket on 2005-2015, test the single choice on 2016-2026. As in SHORT-TUNE-PLAN.md."""
import itertools
import pathlib

import numpy as np

# ponytail: reuse short_basket.py's data + functions by running its definitions only
src = pathlib.Path(__file__).with_name("short_basket.py").read_text(encoding="utf-8")
exec(src.split("# ---- run ----")[0])

TRAIN, TEST = ("2005-06-01", "2016-01-01"), ("2016-01-01", "2026-09-01")
rows = []
for lvl, stop, hold in itertools.product((0.40, 0.45), (0.08, 0.12, 0.16, 0.20), (5, 10, 20)):
    sig = short_signal(lvl, 0.50)
    tr = simulate_short(sig, *TRAIN, trail=stop, max_hold=hold)
    te = simulate_short(sig, *TEST, trail=stop, max_hold=hold)  # computed for the rank check, not for choosing
    rows.append(dict(lvl=lvl, stop=stop, hold=hold, train=tr[0], train_bet=tr[2].ret.mean(),
                     test=te[0], test_bet=te[2].ret.mean(), test_dd=te[1], test_n=len(te[2])))
    print(f"  switch <{lvl:.0%} stop {stop:.0%} hold {hold:2d}d | 2005-15 {tr[0]:+6.1%}/yr | 2016-26 {te[0]:+6.1%}/yr", flush=True)
import pandas as pd
r = pd.DataFrame(rows)
best = r.train.idxmax()
b = r.loc[best]
r["test_rank"] = r.test.rank(ascending=False).astype(int)
print(f"\nChosen on 2005-2015: switch below {b.lvl:.0%}, stop {b.stop:.0%}, hold {b.hold}d "
      f"(2005-15 account {b.train:+.1%}/yr, avg bet {b.train_bet:+.2%})")
print(f"Unseen 2016-2026: account {b.test:+.1%}/yr, avg bet {b.test_bet:+.2%}, worst drop {b.test_dd:.0%}, "
      f"{b.test_n} bets; ranks {r.test_rank[best]} of {len(r)} in 2016-2026")
print(f"rank agreement between halves across all 24: {r.train.corr(r.test, method='spearman'):+.2f}")
print("PASS" if b.test > 0 and b.test_bet > 0 else "no edge (fails the pre-registered bar)")
