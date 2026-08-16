"""Oldest-turn summarization (harness parity H1-S3 / #732, ADR-0021 decision 2b
+ 5). Pruning (H5/H1-S2) handles oversized tool results; when the assembled
conversation history is STILL over the compaction threshold, this is the
second stage: fold the oldest turns into one summary via an LLM call
(task_type="quality" -- wired in main.py, not here), keeping the most recent
turns verbatim. Pure logic, no DB/router imports -- the caller (main.py)
supplies plain {"who", "text"} dicts and an injected summarize_fn so this
stays hermetically testable.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from cerebral.llm.context_budget import COMPACTION_THRESHOLD, is_over_threshold

SummarizeFn = Callable[[str], Awaitable[str]]


def should_summarize(
    text: str, context_window: int, threshold: float = COMPACTION_THRESHOLD
) -> bool:
    """True when the assembled turn text is over the compaction threshold --
    thin wrapper over context_budget so callers share one definition of
    'over budget' across the pruning and summarization stages."""
    return is_over_threshold(text, context_window, threshold)


def _format_turns(turns: list[dict]) -> str:
    return "\n".join(f"{t['who']}: {t['text']}" for t in turns if t.get("text"))


async def summarize_oldest(
    turns: list[dict],
    summarize_fn: SummarizeFn,
    *,
    keep_recent: int = 4,
) -> "dict | None":
    """Summarize the oldest turns, keeping the last `keep_recent` verbatim.

    `turns` is oldest-first, each {"who": str, "text": str}. Returns None when
    there's nothing worth summarizing (len(turns) <= keep_recent) -- the
    caller keeps its current behaviour unchanged in that case. Otherwise
    returns {"text": <summary>, "turns_summarized": N} -- the content payload
    for a KIND_SUMMARY turn.
    """
    if len(turns) <= keep_recent:
        return None
    to_summarize = turns[:-keep_recent] if keep_recent else turns
    if not to_summarize:
        return None
    transcript = _format_turns(to_summarize)
    if not transcript:
        return None
    prompt = (
        "Summarize this earlier portion of a conversation in 2-4 sentences, "
        "preserving names, decisions, and facts the user might refer back to:\n\n"
        f"{transcript}"
    )
    summary_text = (await summarize_fn(prompt)).strip()
    return {"text": summary_text, "turns_summarized": len(to_summarize)}
