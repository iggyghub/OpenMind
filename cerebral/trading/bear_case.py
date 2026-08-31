"""Per-trade bear-case veto: a single LLM call asking for the strongest
reason NOT to take a specific trade right now, veto only if it's compelling.

Scoped-down version of TradingAgents' bull/bear researcher debate -- one
call, not a multi-turn argument, matching the cost-discipline of
_fundamentals_red_flag_scan and cerebral/trading/sentiment.py's own single-
shot LLM scans. Off by default (see settings.py's trading_bear_case_gate_
enabled) -- unlike sentiment.py's refresh() this runs per open-attempt, per
strategy, not market-wide-and-cached, so it carries real per-trade LLM
latency the sentiment gate doesn't.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

CompleteFn = Callable[[str], Awaitable[str]]

# Capped so a strategy's own generated code (which can be long) never blows
# up the prompt -- the hypothesis/signal matter more than exact code for
# this judgment call.
_MAX_CODE_CHARS = 2000


def _parse_verdict(raw: str) -> "tuple[bool, str]":
    """"PROCEED" / "VETO: <reason>" -> (veto, reason).

    Unrecognized/garbage output holds PROCEED rather than guessing a veto
    -- same "anything the model can't express cleanly is 'no opinion'"
    principle as live_tick.evaluate_signal and sentiment._parse_verdict.
    """
    raw = (raw or "").strip()
    label, _, rest = raw.partition(":")
    label = label.strip().upper()
    if label == "VETO":
        return True, rest.strip() or "model vetoed without a stated reason"
    if label != "PROCEED":
        logger.warning("[bear_case] unrecognized model output %r; proceeding", raw)
    return False, ""


async def assess(symbol: str, code: str, signal: int, complete_fn: CompleteFn) -> "tuple[bool, str]":
    """(veto, reason) for this specific trade. Fails open: any LLM
    exception -> proceed, never veto on a hiccup -- this is paper money,
    same philosophy as sentiment.py's refresh().

    No separate `hypothesis` param -- StrategySpec doesn't carry one at
    run_strategy_tick's layer (it's on the originating Idea, not persisted
    on the spec), and generated strategy code already tends to state its
    own rule as a comment (observed live in real registered strategies,
    e.g. "# Hypothesis test: Evaluate claim ..."), so `code` alone carries
    enough context without threading yet another lookup through dispatch_
    due_events/_run_paper_strategy just for this."""
    side = "buy/long" if signal > 0 else "sell/short" if signal < 0 else "flat"
    prompt = (
        "You are a skeptical risk reviewer. A trading strategy is about to "
        f"open a {side} position in {symbol}.\n\n"
        f"Strategy code:\n{code[:_MAX_CODE_CHARS]}\n\n"
        "What is the single strongest reason NOT to take this trade right "
        "now? Only veto if that reason is genuinely compelling, not a "
        "generic caveat that would apply to almost any trade.\n\n"
        "Respond with exactly one line: 'PROCEED' or 'VETO: <one-sentence reason>'."
    )
    try:
        raw = await complete_fn(prompt)
    except Exception:
        logger.warning("[bear_case] LLM assessment failed, proceeding", exc_info=True)
        return False, ""
    return _parse_verdict(raw)
