# ADR-0021: Conversation compaction under context pressure

**Date:** 2026-08-15
**Status:** Accepted (grill session; clear-victor calls made inline)
**Program:** harness-parity (H1, #732). Adopted from `deepseek-ai/deepseek-harness` (`ctx.compaction` + `ctx.toolResultPruner` + `ctx.tokenMeter`).
**Builds on:** A (per-call token usage, PR #721), H5 spill store (#736). **Relates:** ADR-0024 (E encryption), ADR-0020.

## Context

Long turns/chains grow the prompt until the local model (qwen3:8b, ~8-32k ctx) silently truncates and "loses" its tools/history. OpenMind now captures per-call token usage in `cerebral/llm/router.py` (A) but does nothing with it. `dsh` compacts on token pressure: prune oversized tool results first, then summarize, with replayable surface replacements.

## Decisions (clear victors, decided here)

1. **Trigger from A's usage vs the active model's context window.** Add a `context_window` int to each model's metadata (`CLOUD_MODELS` + discovered-at-runtime for local). Compact when the assembled prompt's estimated tokens exceed **70%** of that window. *ponytail: 70% is a calibration knob (`COMPACTION_THRESHOLD`), tune per model if truncation still shows.*
2. **Two-stage, pruner-first (mirror dsh).** (a) **Prune/spill oversized tool results** via the H5 spill store — replace the biggest `prior_steps`/history tool results with a locator first (cheap, lossless, no model call). (b) If still over threshold, **summarize the oldest conversation turns** into one summary turn via an LLM call.
3. **Summarize on cloud/quality, not local.** The summary call uses `task_type="quality"` (Budd/cloud first) — a bad summary poisons all downstream context, so spend the better model here. Falls back to local if cloud is down. *This costs; it's the right place to spend.*
4. **One hook: context assembly.** Compaction runs inside a new `compact_if_needed()` called from `main.py` where the prompt is assembled (`_conversation_context` / `_memory_preamble`), before `chain.run`. Chain-step (`prior_steps`) bloat is handled by the same spill pruning.
5. **Summaries are logged turns.** A summary is written back as a `conversation_turns` row (encrypted per E), so derived history stays reconstructable from the log — aligns with ADR-0022's "model-visible means logged."

## Open (genuine fork — your call)

- **How aggressive?** Default is lazy: prune tool results, summarize only when still over. Alternative is rolling summarization (always keep a running summary). Recommend the lazy default; flag if you want rolling.

## Slices

- **S1** — model `context_window` metadata + a token estimator over the assembled prompt (reuse A's counts). Test: estimator + threshold detection. AFK.
- **S2** — tool-result pruning via H5 spill store in context assembly. Depends on #736. AFK.
- **S3** — oldest-turn summarization (cloud/quality) writing a summary turn; wired into `main.py` assembly. HITL (main.py).
