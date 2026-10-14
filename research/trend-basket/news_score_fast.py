"""Faster FinBERT scoring: only relevant articles (S&P-tagged or theme keyword), each unique headline
scored once (score copied to duplicates), dynamic int8, max 48 tokens, length-sorted batches, low
CPU priority. Same score definition and output DB as news_score.py; resumable."""
import json
import pickle
import re
import sqlite3
import time

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

try:
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # the user's own programs come first
except Exception:
    pass

D = "C:/Users/iggy/AppData/Local/Temp/claude/C--OpenMind/df5ca78e-6cd1-453a-905b-5014e349f2cb/scratchpad/"
sp = set(pickle.load(open(D + "sp500_close.pkl", "rb")))
theme = re.compile(r"\b(AI|artificial intelligence|crypto|bitcoin|EV|electric vehicle|oil|crude|OPEC|Fed|interest rate|rate hike|rate cut|tariff|trade war|beats?|miss(es)?|FDA|approval|merger|acquisition|acquire|buyout)\b", re.I)

dst = sqlite3.connect(r"C:\OpenMind\cerebral\data\news_scores.db")
dst.execute("CREATE TABLE IF NOT EXISTS scores (id TEXT PRIMARY KEY, score REAL)")
done = {r[0] for r in dst.execute("SELECT id FROM scores")}
src = sqlite3.connect(r"file:C:\OpenMind\cerebral\data\news_history.db?mode=ro", uri=True)
by_head = {}
for i, h, sy in src.execute("SELECT id, headline, symbols FROM news"):
    if i in done or not h:
        continue
    if set(json.loads(sy)) & sp or theme.search(h):
        by_head.setdefault(h, []).append(i)
heads = sorted(by_head, key=len)
print(f"unique headlines to score: {len(heads):,} ({sum(map(len, by_head.values())):,} articles)", flush=True)

tok = AutoTokenizer.from_pretrained("ProsusAI/finbert")
model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").eval()
model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
lab = {v.lower(): k for k, v in model.config.id2label.items()}
t0, B = time.time(), 128
with torch.inference_mode():
    for n in range(0, len(heads), B):
        batch = heads[n:n + B]
        p = torch.softmax(model(**tok(batch, padding=True, truncation=True, max_length=48, return_tensors="pt")).logits, -1)
        rows = [(i, float(p[k, lab["positive"]] - p[k, lab["negative"]])) for k, h in enumerate(batch) for i in by_head[h]]
        dst.executemany("INSERT OR REPLACE INTO scores VALUES (?,?)", rows)
        if (n // B) % 50 == 0:
            dst.commit()
            print(f"{n + len(batch):,}/{len(heads):,} unique scored, {(n + len(batch)) / (time.time() - t0):.0f}/s", flush=True)
dst.commit()
print("ALL SCORED", flush=True)
