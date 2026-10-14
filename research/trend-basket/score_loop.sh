#!/usr/bin/env bash
# Keep FinBERT scoring caught up with the headline pull; final pass once the pull logs DONE.
cd "$(dirname "$0")"
while ! grep -q "^DONE" news_pull.log 2>/dev/null; do
  python news_score.py 2>&1 | grep -E "to score|DONE" >> news_score.log
  sleep 600
done
python news_score.py 2>&1 | grep -E "to score|DONE" >> news_score.log
echo "ALL SCORED" >> news_score.log
