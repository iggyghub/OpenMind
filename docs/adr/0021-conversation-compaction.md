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

## Amendment (2026-08-17) -- shipped behaviour is summarize-only; the retroactive pruner is not wired

**Context** -- All three slices landed and were ticked complete, but an audit while
grilling ADR-0023/0024 (issue #776) found the shipped runtime does not match decision
2a. Specifically:

- **2b (summarize) IS live.** `cerebral/main.py::_conversation_context` folds the
  turns it is about to drop into one summary via `should_summarize` /
  `summarize_oldest`, on `task_type="quality"`, and records it as a `KIND_SUMMARY`
  turn — decisions 2b, 3 and 5 all hold as written.
- **2a (prune tool results FIRST) is NOT live.** `cerebral/llm/context_pruner.py`
  exists, is tested, and is imported by nothing but its own tests. No caller. So the
  "two-stage, pruner-first" ordering never happens; only stage (b) runs.
- **Decision 4's named seam never appeared either.** There is no `compact_if_needed()`
  anywhere in the tree. The *location* decision 4 specifies is right — compaction does
  run at context assembly — but it is inlined into `_conversation_context` rather than
  living in the function the decision names.

**Decision** -- Record summarize-only as the shipped behaviour, deliberately, rather
than wiring the pruner to match the original text.

The reason 2a can be dropped without loss is that **H5's spill store already solves
the problem at produce-time.** `ChainEngine` spills an oversized tool result the
moment it is produced (`cerebral/llm/chain_engine.py:165`), replacing it with a
locator before it ever enters the transcript. A retroactive pass over `prior_steps`
therefore has very little left to find. Decision 2a was written assuming oversized
results accumulate and must be cleaned up afterwards; H5 made that assumption false.

Decision 4's second sentence — "Chain-step (`prior_steps`) bloat is handled by the
same spill pruning" — remains **true in effect**, just via the produce-time path
rather than the retroactive one.

**Considered and rejected** --
- *Wire `context_pruner` into the assembly path as 2a specifies.* Rejected: it is a
  change to the live turn path for a benefit H5 has largely already captured, and the
  module's only real-world exercise to date was an infinite loop (#764) that stalled
  the full test suite for hours. Adding an unnecessary hazard to the hot path to
  satisfy a document is backwards.
- *Delete `context_pruner.py`.* Rejected: it is correct, tested, and cheap to keep.
  If a future workload does accumulate large results that H5 misses (a tool whose
  output grows across steps rather than in one shot), it is ready.
- *Leave the ADR alone and just un-tick the driver.* Rejected: the driver was only
  half the problem. The ADR is what a future reader trusts, and it currently describes
  an ordering that does not exist.

**Consequences** --
- Compaction is **one stage, not two**: summarize the oldest turns when the assembled
  window is over `COMPACTION_THRESHOLD`. Oversized tool results are handled earlier and
  elsewhere, by H5 at produce time.
- `cerebral/llm/context_pruner.py` is retained as **available but unused**
  infrastructure. Anyone wiring it must re-read #764 first — the loop guard there
  (a spill must at least halve the text) is load-bearing.
- `HARNESS-PARITY.md`'s H1-S2 tick means "module landed", not "capability live". The
  driver has been annotated accordingly.
- Trigger, threshold, model routing and summary-logging (decisions 1, 3, 5) are
  unaffected.
- **The 16-class capability vocabulary is unchanged** — compaction touches prompt
  assembly only, never the ADR-0005 gate or its classes.
- *Correction to this ADR's own header, recorded rather than silently edited:* the
  `Relates:` line cites "ADR-0024 (E encryption)". ADR-0024 is **Code Mode** (now
  Rejected); encryption is covered by ADR-0005. Read that reference as ADR-0005.
