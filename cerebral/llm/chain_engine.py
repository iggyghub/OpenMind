"""
S2 chaining engine -- Issue #275.

Wraps the S1 planner in a loop so Felix can chain tool calls: pick a tool,
run it through the gate, feed the result back, pick the next tool or return
final text, repeat.  Stops when the planner returns text (done) or the step
cap is reached.
"""

import json
import logging
import os
from typing import TYPE_CHECKING, Awaitable, Callable

from cerebral.llm.planner import Planner, validate_tool_args
from cerebral.llm.router import ToolCall

if TYPE_CHECKING:
    from cerebral.llm.step_ledger import StepLedger
    from cerebral.llm.spill_store import SpillStore

_ChainDoneFn = Callable[[list[dict]], Awaitable[None]]

logger = logging.getLogger(__name__)

MAX_CHAIN_STEPS: int = int(os.environ.get("MAX_CHAIN_STEPS", "8"))


class ChainEngine:
    """Runs the S1 planner in a loop until done or the step cap is hit (S2)."""

    def __init__(
        self,
        planner: Planner,
        gate_fn: Callable[[str, dict], Awaitable[object]],
        execute_fn: Callable[[str, dict], Awaitable[object]],
        record_fn: Callable[[str, dict], Awaitable[None]],
        spill_store: "SpillStore | None" = None,
    ) -> None:
        """
        planner   — Planner instance (S1 engine).
        gate_fn   — async (tool_name: str, args: dict) -> Decision; fires the
                    ADR-0005 gate. Args are passed so the ADR-0016 consequence-
                    gate can flag a committing computer_use action irreversible.
        execute_fn — async (tool_name: str, args: dict) -> ToolResult.
        record_fn — async (kind: str, content: dict) -> None; persists turns.
        spill_store — optional SpillStore (H5-S1 / #736). When set, an oversized
                    successful tool result is stored and replaced in the chain by
                    a short locator+hint so it never floods the context window.
                    None => results flow back raw, identical to before.
        """
        self._planner = planner
        self._gate_fn = gate_fn
        self._execute_fn = execute_fn
        self._record_fn = record_fn
        self._spill_store = spill_store

    async def run(
        self,
        transcript: str,
        tools: list[dict],
        *,
        max_steps: int = MAX_CHAIN_STEPS,
        on_chain_done: "_ChainDoneFn | None" = None,
        run_id: "str | None" = None,
        ledger: "StepLedger | None" = None,
    ) -> str:
        """Run the chaining loop. Returns the final text response to speak.

        on_chain_done -- optional async callback fired when the planner returns
        text naturally (not cap/denial/error) after 2+ successful steps. Receives
        the list of completed step dicts so the caller can offer to save a Recipe.
        Each step dict: {"name": str, "args": dict, "result": str, "is_error": bool}.
        """
        from cerebral.db.conversation import KIND_TOOL_CALL, KIND_TOOL_RESULT
        from cerebral.security import Decision

        prior_steps: list[dict] = []
        # Signatures of tools already run successfully. A tool-native model
        # that re-emits an identical call has stopped making progress (it isn't
        # reading the result back as a tool message -- see Planner.finalize);
        # break the loop and force a text answer instead of spinning to the cap.
        completed_sigs: set[str] = set()

        # Crash-resume (harness improvement C): steps already recorded for this
        # run_id are replayed from the ledger instead of re-executed, so a
        # restart mid-chain never re-fires a side-effectful tool. run_id/ledger
        # absent => resume is empty and behaviour is identical to before.
        resume: dict[str, dict] = {}
        if ledger is not None and run_id:
            for s in ledger.completed(run_id):
                resume[s["sig"]] = s

        for step_num in range(max_steps):
            result = await self._planner.plan(transcript, tools, prior_steps=prior_steps)
            logger.info("[chain] step %d: planner returned %s", step_num + 1, type(result).__name__)

            if isinstance(result, str):
                # Chain finished cleanly -- the ledger's job is done, drop it so
                # a later run with the same run_id starts fresh.
                if ledger is not None and run_id:
                    ledger.clear(run_id)
                if on_chain_done is not None and len(prior_steps) >= 2:
                    try:
                        await on_chain_done(prior_steps)
                    except Exception:
                        logger.exception("[chain] on_chain_done callback raised")
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

            # Loop guard: the model re-asked for a tool it already ran with the
            # same args. It isn't going to progress -- answer from what we have.
            sig = f"{result.name}:{json.dumps(result.args, sort_keys=True, default=str)}"

            # Replay a step completed in a prior (crashed) run: reuse the
            # persisted result and let the planner move on to the next step,
            # without re-recording the turn, re-gating, or re-executing.
            if sig in resume and sig not in completed_sigs:
                done = resume[sig]
                prior_steps.append({
                    "name": done["name"],
                    "args": done["args"],
                    "result": done["result"],
                    "is_error": done["is_error"],
                })
                completed_sigs.add(sig)
                continue

            if sig in completed_sigs:
                logger.info("[chain] repeated call %r -- finalizing from results", result.name)
                return await self._planner.finalize(transcript, prior_steps)

            # Surface the tool call as a Conversation turn before gating
            await self._record_fn(KIND_TOOL_CALL, {"name": result.name, "args": result.args})

            # Per-step gate -- independent, no whole-chain pre-approval (ADR-0008)
            decision = await self._gate_fn(result.name, result.args)

            if decision is not Decision.SILENT:
                logger.info("[chain] step %d: gate denied '%s'", step_num + 1, result.name)
                return _chain_summary(prior_steps, stopped_reason="permission denied")

            # Execute
            tool_result = await self._execute_fn(result.name, result.args)

            # Spill oversized successful output (H5-S1 / #736): swap the raw text
            # for a short locator+hint so a huge result never floods the window.
            # The full text lives in the store (retrieve_spilled pulls it back).
            # Errors are never spilled -- the model needs the message to recover.
            result_content = tool_result.content
            if (
                self._spill_store is not None
                and not tool_result.is_error
                and self._spill_store.should_spill(result_content)
            ):
                locator = self._spill_store.spill(result_content)
                result_content = self._spill_store.hint(locator, len(tool_result.content))

            # Persist the (possibly spilled) result content too, not just the
            # is_error flag -- otherwise a replayed turn on a later chain run
            # has no way to know e.g. what path a create_file call resolved to
            # (#789), and the model re-does work it already finished.
            await self._record_fn(
                KIND_TOOL_RESULT,
                {
                    "name": result.name,
                    "is_error": tool_result.is_error,
                    "result": result_content,
                },
            )

            prior_steps.append(
                {
                    "name": result.name,
                    "args": result.args,
                    "result": result_content,
                    "is_error": tool_result.is_error,
                }
            )

            if tool_result.is_error:
                return f"The tool {result.name} encountered an error: {tool_result.content}"

            if not tool_result.is_error:
                completed_sigs.add(sig)
                # Persist the completed step so a crash before the chain ends
                # can replay it instead of re-executing (harness improvement C).
                if ledger is not None and run_id:
                    ledger.record(run_id, sig, prior_steps[-1])

        # Hit the step cap -- answer from whatever results we gathered rather
        # than the robotic "I completed N steps" summary.
        logger.warning("[chain] reached %d-step cap -- finalizing", max_steps)
        if prior_steps:
            return await self._planner.finalize(transcript, prior_steps)
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
