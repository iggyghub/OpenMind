"""News-volume flip: sell a sector fund when its abnormal news volume >= +10%, buy back when it
falls to <= +2%. As written in VOLUME-FLIP-PLAN.md (post-hoc by origin)."""
import pathlib

import numpy as np
import pandas as pd
import yfinance as yf

# ponytail: reuse sector_turns.py's data loading + turn events by running its top half
src = pathlib.Path(__file__).with_name("sector_turns.py").read_text(encoding="utf-8")
exec(src.split("# ---- test b")[0])

av = abn["volume"]
fm = fund.resample("ME").last()
fm.index = fm.index.to_period("M")

print("\n=== Timing: abnormal volume around 20%-rule troughs (month 0 = trough month) ===")
tr = ev[(ev.rule == "20% rule") & (ev.kind == "bear->bull")]
path = pd.DataFrame({k: [av[s].get(m + k, np.nan) for s, m in zip(tr.sector, tr.month)] for k in range(-6, 7)})
print(" ".join(f"{k:+d}:{v:+.2f}" for k, v in path.mean().items()))

# ---- the rule ----
months = av.index
state = pd.DataFrame(True, index=months, columns=list(ETF))
for sec in ETF:
    held = True
    for m in months:
        v = av.at[m, sec]
        if held and v >= 0.10:
            held = False
        elif not held and v <= 0.02:
            held = True
        state.at[m, sec] = held

print("\n=== Per trough: when did the buy-back fire, and what had the fund done by then? ===")
for s, m, d in zip(tr.sector, tr.month, tr.date):
    if m < months[12]:
        continue
    after = state[s].loc[m:]
    out_at_trough = not state[s].get(m - 1, True)
    back = after[after].index.min() if out_at_trough else None
    tk = ETF[s]
    trough_px = fund[tk].loc[d]
    gain = fm[tk].get(back) / trough_px - 1 if back is not None else np.nan
    print(f"  {s:18s} trough {d.date()}  " + (f"out at trough; back in {back} ({(back - m).n} mo later), fund already {gain:+.0%} off the low"
                                             if out_at_trough else "was holding at the trough (no alarm)"))

r = fm.pct_change().shift(-1).rename(columns={v: k for k, v in ETF.items()})
sig = months[(months >= pd.Period("2016-12", "M")) & (months <= pd.Period("2026-07", "M"))]
w = state.loc[sig].astype(float) / len(ETF)
rr = r.loc[sig, list(ETF)]
rule = (w * rr).sum(axis=1) - w.diff().abs().sum(axis=1).fillna(w.iloc[0].sum()) * COST
spy = yf.download("SPY", start="2015-12-01", auto_adjust=True, progress=False)["Close"].squeeze().resample("ME").last()
spy.index = spy.index.to_period("M")
b = pd.DataFrame({"volume flip": rule, "EW hold": rr.mean(axis=1), "SPY": spy.pct_change().shift(-1).reindex(sig)})
b.index = sig + 1


def st(x):
    e = (1 + x).cumprod()
    return e.iloc[-1] ** (12 / len(x)) - 1, (e / e.cummax() - 1).min(), x.mean() / x.std() * np.sqrt(12)


print(f"\n=== Rule result (invested {w.sum(axis=1).mean():.0%} of the time; "
      f"{int((w.diff().abs() > 0).sum().sum())} switches over {len(sig)} months) ===")
ok = True
for lab, a, z in (("2017-2020", "2017-01", "2020-12"), ("2021-2026", "2021-01", "2026-08"), ("all", "2017-01", "2026-08")):
    part = b.loc[pd.Period(a, "M"):pd.Period(z, "M")]
    s = {k: st(part[k]) for k in part}
    print(f"{lab}: " + " | ".join(f"{k} {c:+.1%}/yr DD {dd:.0%} Sh {sh:.2f}" for k, (c, dd, sh) in s.items()))
    if lab != "all":
        ok &= s["volume flip"][0] > max(s["EW hold"][0], s["SPY"][0])
print("PASS (post-hoc: re-test on new data before trusting)" if ok else "no edge (fails the bar)")
