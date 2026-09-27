"""Where is FinBERT scoring slow? Time tokenize vs model, fp32 vs dynamic int8, batch sizes."""
import sqlite3
import time
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

heads = [h for (h,) in sqlite3.connect(r"file:C:\OpenMind\cerebral\data\news_history.db?mode=ro", uri=True)
         .execute("SELECT headline FROM news WHERE headline != '' LIMIT 1024 OFFSET 900000")]
tok = AutoTokenizer.from_pretrained("ProsusAI/finbert")
fp32 = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").eval()
int8 = torch.quantization.quantize_dynamic(fp32, {torch.nn.Linear}, dtype=torch.qint8)
print("threads:", torch.get_num_threads())

t = time.time(); enc = tok(heads, padding=True, truncation=True, max_length=64, return_tensors="pt")
print(f"tokenize 1024: {time.time() - t:.2f}s, padded length {enc['input_ids'].shape[1]}")
for name, m in (("fp32", fp32), ("int8", int8)):
    for B in (64, 256):
        t = time.time()
        with torch.inference_mode():
            for i in range(0, 1024, B):
                e = tok(heads[i:i + B], padding=True, truncation=True, max_length=64, return_tensors="pt")
                m(**e)
        print(f"{name} batch {B}: {1024 / (time.time() - t):.0f} headlines/s")
