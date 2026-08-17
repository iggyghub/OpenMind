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


# Every locator is "spill:" + 12 hex chars, so a hint's length depends only on
# the size it reports -- this stand-in measures the real replacement cost
# without having to spill first.
_SAMPLE_LOCATOR = "spill:" + "0" * 12


def _worth_spilling(spill_store: SpillStore, result: str) -> bool:
    """True when spilling result buys a meaningful reduction (at least halves it).

    Spilling swaps the raw text for a ~150-char hint, so spilling anything not
    comfortably bigger than that hint is pointless or actively harmful -- it
    GROWS the prompt. Worse, a hint is itself longer than a typical spill
    threshold, so without this guard an already-spilled step stays a candidate
    forever and prune_prior_steps spins: spill the hint, get a marginally
    shorter hint, spill that... That hang was real -- it stalled the full test
    suite at test_context_pruner.py, which is why that file's own
    test_error_results_are_never_spilled had never once run to completion.

    "At least halves it" rather than merely "is smaller": a 2-char gain is what
    let the spin continue one extra round and corrupted the stored locator (it
    ended up pointing at the previous hint instead of the original output).
    """
    return len(result) >= 2 * len(spill_store.hint(_SAMPLE_LOCATOR, len(result)))


def prune_prior_steps(
    steps: list[dict],
    spill_store: SpillStore,
    context_window: int,
    threshold: float = COMPACTION_THRESHOLD,
) -> tuple[list[dict], int]:
    pruned = 0
    # ponytail: bounded by the step count as belt-and-braces. The _shrinks guard
    # already guarantees progress (each spilled step stops qualifying), but this
    # function is destined for the turn path, where an infinite loop freezes the
    # whole event loop -- cheap insurance against a future edit reopening that.
    for _ in range(len(steps) + 1):
        if not is_over_threshold(_assembled_text(steps), context_window, threshold):
            break
        candidates = [
            s for s in steps
            if not s.get("is_error")
            and spill_store.should_spill(s.get("result"))
            and _worth_spilling(spill_store, s["result"])
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
