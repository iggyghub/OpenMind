"""Context budget — token estimation & threshold detection for prompt compaction.

S1 builds the two ingredients ADR-0021 decision 1 needs:
  (a) each model knows its context window (wired in router.py)
  (b) a cheap estimator + threshold check over an assembled prompt.
Compaction wiring is S2/S3.
"""

import os

# Cheap heuristic: ~4 chars per token (rough average for mixed prose/code).
# Ponytail: swap in a real tokenizer if truncation still shows.
_CHARS_PER_TOKEN = 4

# ADR-0021 decision 1: trigger compaction when estimated prompt tokens exceed
# 70% of the active model's context window. Override via env.
COMPACTION_THRESHOLD = float(os.environ.get("COMPACTION_THRESHOLD", "0.70"))


def estimate_tokens(text: str) -> int:
    """Return a cheap token estimate for an assembled prompt string.

    Never raises. Empty or non-string input returns 0.
    """
    if not isinstance(text, str):
        return 0
    return len(text) // _CHARS_PER_TOKEN


def is_over_threshold(text: str, context_window: int, threshold: float = COMPACTION_THRESHOLD) -> bool:
    """Return True when the estimated prompt tokens exceed the threshold fraction
    of the model's context window.

    Safely returns False when context_window is zero or negative.
    """
    if context_window <= 0:
        return False
    return estimate_tokens(text) > threshold * context_window
