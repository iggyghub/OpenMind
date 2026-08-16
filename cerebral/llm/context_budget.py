"""Context budget -- token estimation & threshold detection for prompt compaction.

Harness parity H1-S1 (issue #732, ADR-0021 decision 1). Builds the two ingredients
compaction needs:
  (a) each model knows its context window (wired in router.py: context_window_for)
  (b) a cheap estimator + threshold check over an assembled prompt.
Compaction wiring itself is S2/S3.
"""

import os

# Cheap heuristic: ~4 chars per token (rough average for mixed prose/code).
# ponytail: char/4 is a rough estimate -- swap in a real tokenizer if truncation
# still shows below this.
_CHARS_PER_TOKEN = 4

# ADR-0021 decision 1: trigger compaction when estimated prompt tokens exceed
# 70% of the active model's context window. Override via COMPACTION_THRESHOLD.
COMPACTION_THRESHOLD = float(os.environ.get("COMPACTION_THRESHOLD", "0.70"))


def estimate_tokens(text: str) -> int:
    """Cheap token estimate for an assembled prompt string. Never raises;
    empty or non-string input returns 0."""
    if not isinstance(text, str):
        return 0
    return len(text) // _CHARS_PER_TOKEN


def is_over_threshold(
    text: str, context_window: int, threshold: float = COMPACTION_THRESHOLD
) -> bool:
    """True when the estimated prompt tokens exceed the threshold fraction of the
    model's context window. Returns False for a zero/negative window."""
    if context_window <= 0:
        return False
    return estimate_tokens(text) > threshold * context_window
