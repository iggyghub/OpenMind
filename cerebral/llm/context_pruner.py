"""Context pruner -- H1-S2 tool-result pruning via the H5 spill store.

ADR-0021 decision 2a. When the assembled chain/history exceeds the compaction
threshold, prune FIRST (before summarization): replace the BIGGEST tool results
with a spill-store locator (cheap, lossless, no model call).

Pure function; wiring into main.py context assembly is a later slice (S3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cerebral.llm.context_budget import COMPACTION_THRESHOLD, is_over_threshold

if TYPE_CHECKING:
    from cerebral.llm.spill_store import SpillStore


def _assembled_text(steps: list[dict]) -> str:
    return "".join(str(s.get("result", "")) for s in steps)


def prune_prior_steps(
    steps: list[dict],
    spill_store: SpillStore,
    context_window: int,
    threshold: float = COMPACTION_THRESHOLD,
) -> tuple[list[dict], int]:
    pruned = 0
    while is_over_threshold(_assembled_text(steps), context_window, threshold):
        candidates = [
            s for s in steps if not s.get("is_error") and spill_store.should_spill(s.get("result"))
        ]
        if not candidates:
            break
        target = max(candidates, key=lambda s: len(s["result"]))
        raw = target["result"]
        locator = spill_store.spill(raw)
        target["result"] = spill_store.hint(locator, len(raw))
        pruned += 1
    return steps, pruned


def would_prune(
    steps: list[dict],
    context_window: int,
    threshold: float = COMPACTION_THRESHOLD,
) -> bool:
    return is_over_threshold(_assembled_text(steps), context_window, threshold)
