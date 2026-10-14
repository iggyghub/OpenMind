"""Score headlines with FinBERT (ProsusAI/finbert, trained on pre-2014 Financial PhraseBank -- no
knowledge of later price moves). score = P(positive) - P(negative), in [-1, 1]. Incremental: scores
only ids not yet in news_scores.db, so it can run alongside the pull and be re-run until caught up."""
import sqlite3
import time

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

SRC = r"C:\OpenMind\cerebral\data\news_history.db"
DST = r"C:\OpenMind\cerebral\data\news_scores.db"
tok = AutoTokenizer.from_pretrained("ProsusAI/finbert")
model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").eval()
labels = model.config.id2label  # {0: positive, 1: negative, 2: neutral}
pos = next(i for i, l in labels.items() if l.lower() == "positive")
neg = next(i for i, l in labels.items() if l.lower() == "negative")
torch.set_num_threads(max(1, torch.get_num_threads()))

dst = sqlite3.connect(DST)
dst.execute("CREATE TABLE IF NOT EXISTS scores (id TEXT PRIMARY KEY, score REAL)")
done = {r[0] for r in dst.execute("SELECT id FROM scores")}
src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
todo = [(i, h) for i, h in src.execute("SELECT id, headline FROM news ORDER BY created_at") if i not in done and h]
print(f"to score: {len(todo)}", flush=True)

t0 = time.time()
B = 64
with torch.inference_mode():
    for n in range(0, len(todo), B):
        batch = todo[n:n + B]
        enc = tok([h for _, h in batch], padding=True, truncation=True, max_length=64, return_tensors="pt")
        p = torch.softmax(model(**enc).logits, dim=-1)
        dst.executemany("INSERT OR REPLACE INTO scores VALUES (?,?)",
                        [(i, float(p[k, pos] - p[k, neg])) for k, (i, _) in enumerate(batch)])
        if n % (B * 200) == 0:
            dst.commit()
            rate = (n + len(batch)) / max(1e-9, time.time() - t0)
            print(f"{n + len(batch)}/{len(todo)} scored, {rate:.0f}/s", flush=True)
dst.commit()
print("DONE scoring", len(todo), flush=True)
