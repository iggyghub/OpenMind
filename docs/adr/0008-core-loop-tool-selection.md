# ADR-0008: The core loop — native tool-calling, two-loop dispatch, and Recipes

**Date:** 2026-06-15
**Status:** Accepted

## Context

CONTEXT.md defined "the core loop" (intent → the LLM selects a tool → execute via MCP) but it was never built: `_process_command` (`cerebral/main.py`) only ever produced a chat reply, never selected or executed a tool from natural-language intent (issue #270). Worse, the documented loop was self-contradictory — the glossary said the LLM "executes" tools directly, while the ship criteria described a "wake → queue → approve" path. The orchestrator already emitted a tool-use schema for an LLM (`tools_for_llm`, `orchestrator.py:632`) that nothing consumed. This ADR records how the loop is actually built.

## Decision

**Two loops, not one.** The contradiction was a conflation of two distinct paths:

- **Active loop** — Felix is *addressed* (wake word or typed command). A planner picks a tool and **dispatches it directly** through the ADR-0005 gate with `passive=False`. Silent-class tools run friction-free; ask-class prompt via the Conversation consent card; irreversible pop the modal. No queue detour. **This is the v1 path.**
- **Passive loop** — an ambient 5W1H candidate is queued and executed only after the user approves, with `passive=True` (escalated). Reserved for ambient capture. **v1 narrows this to wake-first: Felix acts only when addressed; always-listening ambient capture into the queue is designed-for but deferred post-v1.**

**Tool selection is native tool-calling.** The planner uses the model's built-in tool API (the Anthropic/OpenAI tool-use schema `tools_for_llm` already emits — zero translation), not structured-JSON-in-a-prompt. Structured-JSON (the `passive/extractor.py` pattern) is kept only as a documented fallback for a model that cannot do native calls; it is not built for v1.

**Consequence — the default model changes.** Native tool-calling requires a tool-capable model. The default tool-picking brain moves from **Gemma 4 to Qwen 3.6**: as of 2026 Gemma 4 *can* tool-call but forces a reasoning trace by default (tool output lands in `reasoning_content`, needing a thinking-mode workaround), while Qwen 3.6 emits clean tool calls. Gemma 4 stays *supported*; Hermes 3 is dropped (legacy, unmaintained since Dec 2025). The CONTEXT.md Stack table is updated to match.

**Planner shape, built for the loop from day one.** A new `cerebral/llm/planner.py` owns intent → tool selection. The `Backend` Protocol (`cerebral/llm/router.py`) gains `complete_with_tools(prompt, tools) -> ToolCall | str`. The Ollama backend uses `/api/chat` (the native-tools endpoint) — not the `/api/generate` path the chat-only `complete` uses. When the planner returns text instead of a `ToolCall`, it falls through to the existing chat path (`_process_command`); the planner is the sole arbiter of conversation-vs-action — no separate classifier. Ambiguity resolves to a spoken clarifying question via a system-prompt instruction (native tool-calling gives no numeric confidence score).

**Single-step first, chaining is the target.** The planner returns `ToolCall | text` from day one so chaining is additive. Build order: **S1** single-step engine → **S2** chaining (the same engine in a loop) → **S3** Recipes. Chaining caps at **8 steps** (env-overridable, mirroring `OLLAMA_TIMEOUT_S`), because per-step reliability compounds (≈95%/step → ≈66% over 8 steps); hitting the cap stops gracefully and reports what was done. Each step re-gates independently (Q7) and surfaces as its own Conversation turn; there is no whole-chain pre-approval.

**Arg validation.** The LLM's tool arguments get a lightweight built-in check (required fields + top-level types, read off the tool's `input_schema`; no `jsonschema`/`pydantic` dependency added) at the engine dispatch boundary — not inside `call_tool`, which also serves the human/queue paths. On failure: feed the error back to the model **once**, then fail gracefully to a spoken reply. Bounded self-correction, mirroring ADR-0005's "one-shot in v1" posture.

**Dispatch reuses the existing gate.** The active loop does not invent consent code. It mirrors the issue #238 tray-IPC pattern verbatim — `plugin_for_tool` → `required_capabilities_for` → `check_capabilities(name, caps, CallFlags())` → dispatch if SILENT, else report the refusal as text. (#238 closed the `main.py` "no-capability dispatch" gap that ADR-0005's 2026-05-20 amendment still describes as open.)

**Recipes (S3, in v1 scope).** A **Recipe** is a saved, named, user-approved chain — a frozen literal sequence of tool calls — that a user re-runs on command. Felix offers to save after a successful 2+-step chain; the user accepts-and-names or denies. Stored per-profile in a `recipes` SQLite table (`name`, `profile_id`, ordered steps, `created_at`, `run_count`, `last_run_at`). Frozen args still yield fresh results (a stored `gmail_search(query="is:unread")` re-runs against today's mail). Recipes are exposed to the planner as synthetic tools so they compose with normal tools. Parameterized (fill-in-the-blank) Recipes are deferred post-v1. A Main-window Recipes panel surfaces usage with a 30-day "stale" flag and an exact-duplicate flag for weekly hygiene; the weekly cadence itself rides the existing scheduler, not #270. **Re-running a Recipe re-fires every per-step gate** — see the ADR-0005 amendment of the same date.

## Considered and rejected

- **One loop** (the original CONTEXT.md framing). It hid the active/passive distinction that the `passive` flag and ADR-0005's escalation semantics already depend on.
- **Structured-JSON tool selection for v1.** Hand-rolled parse/repair the model can drift from; native tool-calling is the schema `tools_for_llm` was built for.
- **A separate conversation-vs-action classifier** before the planner. Redundant — one tool-calling pass decides both.
- **Whole-plan pre-approval for chains.** The planner computes each step from the previous result, so there is no full plan to pre-approve; per-step gating is both simpler and stricter.
- **Standing approval for saved Recipes** (save the grant, not just the plan). Rejected — see the ADR-0005 amendment; it would make a Recipe a pre-approved footgun.

## Consequences

- The 16-class capability vocabulary and the two cross-cutting flags (`passive`, `irreversible`) are **unchanged**. This ADR is loop mechanics on top of ADR-0005, not a permissions change.
- Cloud tool-calling rides a runtime assumption: OpenClaw must forward `tools`/`tool_calls`. The OpenAI-compatible `/v1` surface is known to drop tool-call chunks in some builds, so the cloud path ships with an integration smoke test and **fail-soft-to-text** degradation. The verifiable primary for v1 is the local Ollama `/api/chat` path.
- The separate `docs_create` local-ODF-fallback-despite-Google-connected bug is tracked independently; it is not part of the loop.
