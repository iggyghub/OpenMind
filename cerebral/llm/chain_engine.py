"""
S2 chaining engine -- Issue #275.

Wraps the S1 planner in a loop so Felix can chain tool calls: pick a tool,
run it through the gate, feed the result back, pick the next tool or return
final text, repeat.  Stops when the planner returns text (done) or the step
cap is reached.
"""

import logging
import os
from typing import Awaitable, Callable

from cerebral.llm.planner import Planner, validate_tool_args
from cerebral.llm.router import ToolCall

logger = logging.getLogger(__name__)

MAX_CHAIN_STEPS: int = int(os.environ.get("MAX_CHAIN_STEPS", "8"))


class ChainEngine:
    """Runs the S1 planner in a loop until done or the step cap is hit (S2)."""

    def __init__(
        self,
        planner: Planner,
        gate_fn: Callable[[str], Awaitable[object]],
        execute_fn: Callable[[str, dict], Awaitable[object]],
        record_fn: Callable[[str, dict], Awaitable[None]],
    ) -> None:
        """
        planner   — Planner instance (S1 engine).
        gate_fn   — async (tool_name: str) -> Decision; fires ADR-0005 gate.
        execute_fn — async (tool_name: str, args: dict) -> ToolResult.
        record_fn — async (kind: str, content: dict) -> None; persists turns.
        """
        self._planner = planner
        self._gate_fn = gate_fn
        self._execute_fn = execute_fn
        self._record_fn = record_fn

    async def run(
        self,
        transcript: str,
        tools: list[dict],
        *,
        max_steps: int = MAX_CHAIN_STEPS,
    ) -> str:
        """Run the chaining loop. Returns the final text response to speak."""
        from cerebral.db.conversation import KIND_TOOL_CALL, KIND_TOOL_RESULT
        from cerebral.security import Decision

        prior_steps: list[dict] = []

        for step_num in range(max_steps):
            result = await self._planner.plan(transcript, tools, prior_steps=prior_steps)
            logger.info("[chain] step %d: planner returned %s", step_num + 1, type(result).__name__)

            if isinstance(result, str):
                return result

            # Validate args -- one re-ask on failure (ADR-0008 bounded correction)
            val_err = validate_tool_args(result.name, result.args, tools)
            if val_err:
                logger.warning("[chain] step %d: arg validation failed: %s", step_num + 1, val_err)
                result = await self._planner.plan(
                    transcript, tools, error=val_err, prior_steps=prior_steps
                )
                if not isinstance(result, ToolCall):
                    return result if isinstance(result, str) else "Something went wrong. Please try again."

            # Surface the tool call as a Conversation turn before gating
            await self._record_fn(KIND_TOOL_CALL, {"name": result.name, "args": result.args})

            # Per-step gate -- independent, no whole-chain pre-approval (ADR-0008)
            decision = await self._gate_fn(result.name)

            if decision is not Decision.SILENT:
                logger.info("[chain] step %d: gate denied '%s'", step_num + 1, result.name)
                return _chain_summary(prior_steps, stopped_reason="permission denied")

            # Execute
            tool_result = await self._execute_fn(result.name, result.args)
            await self._record_fn(
                KIND_TOOL_RESULT,
                {"name": result.name, "is_error": tool_result.is_error},
            )

            prior_steps.append(
                {
                    "name": result.name,
                    "args": result.args,
                    "result": tool_result.content,
                    "is_error": tool_result.is_error,
                }
            )

            if tool_result.is_error:
                return f"The tool {result.name} encountered an error: {tool_result.content}"

        # Hit the step cap -- stop gracefully and summarise
        logger.warning("[chain] reached %d-step cap", max_steps)
        return _chain_summary(
            prior_steps, stopped_reason=f"reached the {max_steps}-step limit"
        )


def _chain_summary(steps: list[dict], *, stopped_reason: str) -> str:
    """Human-readable summary of what was accomplished before stopping early."""
    if not steps:
        return f"I stopped early: {stopped_reason}."
    done = ", ".join(s["name"] for s in steps)
    return (
        f"I completed {len(steps)} step(s) ({done}) "
        f"but stopped early -- {stopped_reason}."
    )
