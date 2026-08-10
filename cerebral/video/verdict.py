"""Validity verdict per cluster -- ADR-0017 S6 #644.

First video in a new cluster triggers a strong-model + web-search validity
pass; every later video in that cluster inherits the verdict instantly.

Verdict: legit / dubious / scam / unverifiable
  + confidence (0.0-1.0) + evidence (list of 1-3 URLs/strings).

S9 #655: the model is Budd (or local), grounded on Felix's own web_search
(OpenClaw) -- no Anthropic API key. build_verdict_prompt() is a pure helper the
prod seam uses so the RAG prompt is unit-testable without any network.

Injectable seam:
  set_verify_fn(async fn(cluster_label, idea_text) -> dict)
  fn must return {"verdict": str, "confidence": float, "evidence": list[str]}.

Tests always set the seam to a stub; no network/API key required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_VALID_VERDICTS = frozenset({"legit", "dubious", "scam", "unverifiable"})

_verify_fn: Optional[Callable] = None


def set_verify_fn(fn: "Callable | None") -> None:
    global _verify_fn
    _verify_fn = fn


def get_verify_fn() -> Callable:
    return _verify_fn or _prod_verify


async def _prod_verify(cluster_label: str, idea_text: str) -> dict:
    # ponytail: live-verify only -- injected from main.py via _wire_plugin_seams
    logger.warning("[video/verdict] _prod_verify fallback -- wire set_verify_fn via main.py")
    raise NotImplementedError("verify_fn not wired; ensure _wire_plugin_seams ran")


def build_verdict_prompt(
    cluster_label: str, idea_text: str, search_results: str = ""
) -> str:
    """Build the validity-verdict prompt (S8 de-bias + S9 web grounding).

    When ``search_results`` is non-empty the model judges from them (RAG) and
    cites their URLs; otherwise it judges from its own knowledge and says so.
    The de-bias clause ensures the channel's sensational framing (e.g. a channel
    calling its ideas "unethical") never drives the verdict on its own.
    """
    debias = (
        "You are a financial-idea fact-checker. Judge the METHOD below on its own "
        "merits -- its actual legality and whether it really works. Ignore any "
        "sensational or clickbait framing from the source video (e.g. a channel "
        "calling its ideas 'unethical' or 'secret'): many such videos describe "
        "legitimate programs such as government grants, benefits, tax credits, or "
        "incentives paid for doing something. Do NOT mark an idea 'scam' or 'dubious' "
        "merely because of framing; base the verdict on what the method itself is.\n"
    )
    if search_results.strip():
        grounding = (
            "Base your verdict on these web search results:\n"
            f"{search_results.strip()[:4000]}\n\n"
            "Cite the relevant result URLs as your evidence.\n"
        )
    else:
        grounding = (
            "No web search results are available; judge from your own knowledge and "
            "describe your reasoning in the evidence rather than inventing URLs.\n"
        )
    schema = (
        "Return ONLY valid JSON with exactly three keys:\n"
        '  "verdict": one of "legit", "dubious", "scam", "unverifiable"\n'
        '  "confidence": a float between 0.0 and 1.0\n'
        '  "evidence": a JSON array of 1-3 URLs or source descriptions\n\n'
    )
    return debias + grounding + schema + f"Cluster: {cluster_label}\nIdea: {idea_text}"


def _validate(result: object) -> None:
    """Raise ValueError if result has invalid verdict structure."""
    if not isinstance(result, dict):
        raise ValueError(f"verify_fn returned non-dict: {result!r}")
    verdict = result.get("verdict", "")
    if verdict not in _VALID_VERDICTS:
        raise ValueError(
            f"Invalid verdict {verdict!r}; must be one of {sorted(_VALID_VERDICTS)}"
        )
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
        raise ValueError(f"confidence must be float in [0,1], got {confidence!r}")
    evidence = result.get("evidence", [])
    if not isinstance(evidence, list) or not (1 <= len(evidence) <= 3):
        raise ValueError(f"evidence must be a list of 1-3 strings, got {evidence!r}")


async def verify_cluster(
    cluster_label: str,
    idea_text: str,
    *,
    max_retries: int = 3,
) -> dict:
    """Run validity verdict with validate-and-retry.

    Raises on all-retry failure (caller must not write a null verdict).
    Returns {"verdict": str, "confidence": float, "evidence": list[str]}.
    """
    fn = get_verify_fn()
    last_exc: Exception = ValueError("no attempts made")
    for attempt in range(max_retries):
        try:
            result = fn(cluster_label, idea_text)
            if asyncio.iscoroutine(result):
                result = await result
            _validate(result)
            return {
                "verdict": result["verdict"],
                "confidence": float(result["confidence"]),
                "evidence": [str(e) for e in result["evidence"]],
            }
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[video/verdict] attempt %d/%d failed: %s", attempt + 1, max_retries, exc
            )
    raise last_exc
