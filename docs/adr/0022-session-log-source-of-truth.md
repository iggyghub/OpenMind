# ADR-0022: Session log as source of truth + fork (minimal subset)

**Date:** 2026-08-15
**Status:** Accepted (grill session; clear-victor calls made inline)
**Program:** harness-parity (H3, #734). Adapted from `deepseek-ai/deepseek-harness` (append-only `SessionEvent` log, "model-visible means logged" invariant, `deriveMessages`, fork/resume).
**Relates:** ADR-0021 (compaction), ADR-0020 (subagents), C StepLedger (#726), E encryption (#724).

## Context

`dsh` makes an append-only event log the single source of model-visible context, asserts a runtime invariant that anything reaching a model request is reconstructable from the log, and derives history + fork/resume/transcripts from that one stream. OpenMind assembles context ad hoc in `main.py` (`_conversation_context`, `_memory_preamble`, the raw transcript) with no invariant and no fork. `conversation_turns` already logs most of it (now encrypted per E), and StepLedger (C) already resumes chain steps.

## Decisions (clear victors, decided here)

1. **Adopt the SUBSET, not full event-sourcing.** `dsh` is a multi-client product; OpenMind is single-user, local. A full event-sourced rewrite is not worth it (ponytail). Take the two pieces that pay off: a single-assembly invariant, and fork.
2. **One `derive_model_context(profile)` function.** Refactor the scattered assembly in `main.py` into one function that builds the model-visible prompt **only** from `conversation_turns` (+ the memory store) — the single seam. Any new model-visible input must go through a logged turn. A cheap debug assertion (behind a flag) checks the assembled prompt has no un-logged content.
3. **`fork(session_id, boundary)` — minimal.** Snapshot `conversation_turns` for a session up to a boundary turn id into a new session id (copy rows, new session_id). Consumers: ADR-0020 subagents (a sub-agent may fork the parent's context slice explicitly) and self_dev/computer_use replay after a crash (pairs with C). No live "forked session" object — just a row copy + a new id.
4. **Reuse `conversation_turns` as the log.** Do not build a second log table. Add any missing model-visible event kinds (e.g. record the memory-preamble injection as its own turn kind so it's reconstructable) — minimal additions only.

## Open (genuine fork — your call)

- **Depth.** Minimal (invariant + fork, above) vs deeper (promote every ephemeral model-visible bit — tool schemas, shortlist decisions — to logged events). Recommend **minimal**; the deeper version is a large refactor with thin single-user payoff. Flag if you want it.

## Slices

- **S1** — `derive_model_context(profile)` consolidating the `main.py` assembly; behind-flag invariant assertion. Test: assembled prompt == derived-from-log. HITL (main.py).
- **S2** — `fork(session_id, boundary)` on the conversation store + test (forked session has the boundary-truncated turns, independent thereafter). AFK.
- **S3** — wire fork into ADR-0020 subagents (explicit-context path) once H2 lands.
