"""Video idea extraction + incremental clustering (ADR-0017 S5 #642).

Each transcribed/escalated video gets an extracted idea and a cluster label in
one cheap LLM call (JSON-forced, validate-and-retry).  Cluster assignment is
incremental: existing labels are passed to the LLM so it can reuse one or
propose a new one.  No vector math; ChromaDB is the documented upgrade path.

Injectable seam:
  set_extract_fn(async fn(transcript, ocr_text, visual_summary, labels) -> dict)
  fn must return {"idea": str, "cluster_label": str, "people_required": int}.
  (people_required is lenient — a missing value defaults to 1.)

Tests always set the seam to a stub; the seam is never None in the loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_extract_fn: Optional[Callable] = None


def set_extract_fn(fn: "Callable | None") -> None:
    global _extract_fn
    _extract_fn = fn


def get_extract_fn() -> Callable:
    return _extract_fn or _prod_extract


async def _prod_extract(
    transcript: str,
    ocr_text: str,
    visual_summary: str,
    existing_labels: list[str],
) -> dict:
    # ponytail: live-verify only -- in production, injected from main.py via _wire_plugin_seams
    logger.warning("[video/extraction] _prod_extract fallback -- wire set_extract_fn via main.py")
    raise NotImplementedError("extract_fn not wired; ensure _wire_plugin_seams ran")


def _validate(result: object) -> None:
    """Raise ValueError if result is missing required keys or has empty values."""
    if not isinstance(result, dict):
        raise ValueError(f"extract_fn returned non-dict: {result!r}")
    idea = result.get("idea", "")
    label = result.get("cluster_label", "")
    if not isinstance(idea, str) or not idea.strip():
        raise ValueError(f"Missing/empty 'idea': {result!r}")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"Missing/empty 'cluster_label': {result!r}")


def _people_required(result: dict) -> int:
    """Coerce people_required to a positive int; default 1 (solo) if missing/bad.

    Lenient by design — a missing count must not fail an otherwise-valid extraction.
    """
    try:
        n = int(result.get("people_required", 1))
        return n if n >= 1 else 1
    except (TypeError, ValueError):
        return 1


async def extract_idea(
    transcript: str,
    ocr_text: str,
    visual_summary: str,
    existing_labels: list[str],
    *,
    max_retries: int = 3,
) -> dict:
    """Extract idea + cluster label with validate-and-retry.

    Raises on all-retry failure (caller must not write a null/partial row).
    Returns {"idea": str, "cluster_label": str}.
    """
    fn = get_extract_fn()
    last_exc: Exception = ValueError("no attempts made")
    for attempt in range(max_retries):
        try:
            result = fn(transcript, ocr_text, visual_summary, existing_labels)
            if asyncio.iscoroutine(result):
                result = await result
            _validate(result)
            return {
                "idea": result["idea"].strip(),
                "cluster_label": result["cluster_label"].strip(),
                "people_required": _people_required(result),
            }
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[video/extraction] attempt %d/%d failed: %s", attempt + 1, max_retries, exc
            )
    raise last_exc
