# ADR-0030: The chain's tool-result contract -- structured results, recoverable errors

**Date:** 2026-09-05
**Status:** Accepted (grill session)
**Relates:** ADR-0008 (native tool-calling), ADR-0005 (the gate), ADR-0021
(compaction), ADR-0029 (shell mode -- its scope depends on this).

## Context

Three defects in how the chain handles tool results, all in one layer, all
self-documented in the code that works around them.

**1. Results arrive as prose, not as tool messages.** `Planner.plan` flattens
prior steps into a text block (`planner.py:287`):

```
Previous steps:
Step 1: web_search -> <result>
What should I do next? Use a tool to continue, or reply with a summary if done.
```

The model reads narration *about* tool output rather than tool output. The
codebase already names this as the cause -- `chain_engine.py:85`: *"it isn't
reading the result back as a tool message"* -- and the mitigation in place is a
**loop-breaker** (`completed_sigs` forces `Planner.finalize`), which treats the
symptom. The cause is that `complete_with_tools(prompt: str, tools)` takes a flat
string at every one of its six implementations plus three test doubles; there is
no message-list seam anywhere in the stack. The OpenAI-compatible backend posts
to `/v1/chat/completions`, and the Anthropic backend builds
`messages=[{"role": "user", ...}]` -- both APIs support tool-result messages
natively, and the abstraction above them cannot express one.

**2. The world's errors are fatal; the model's own errors are retried.**

| Failure | Behaviour | Recoverable |
|---|---|---|
| Bad arguments (the model's mistake) | re-plan with the error attached | yes, one retry |
| Repeated identical call | force `finalize()` | yes |
| Gate denied | abort, summarise | no |
| **Tool error** (the world's) | **abort, speak the raw error** | **no** |

`chain_engine.py:193` returns on any `is_error`. A locked file, a network blip, a
rate limit -- each kills the whole chain. And the recovery path is *already
built and unreachable behind that return*: errors are deliberately excluded from
spilling ("the model needs the message to recover", :161), the failed step is
appended to `prior_steps` with its flag (:183), and `planner.py:288` formats
`ERROR: …` for exactly this case. Three pieces of machinery that can never fire.

**3. Felix's copy system is one clause, on the wrong path.** *"Answer the user
now in one or two natural sentences"* (`planner.py:329`) lives in `finalize()`,
which only runs on the loop-break path. The normal path has no length discipline
at all, and `main.py:7154` speaks the response verbatim -- so the same question
gets a different spoken style depending on which path answered it.

## Decision

1. **`complete_with_tools` takes a message list, not a prompt string.** Tool
   results reach the model as tool-result messages on backends that support them.
   Nine sites: six implementations and three test doubles. Backends without native
   support keep today's flattening behind the same seam.

2. **Length discipline applies to what Felix *says*, never to what the model
   *reads*.** The "one or two sentences" rule moves out of `finalize()` and into
   `_SYSTEM_PROMPT`, so every path obeys it. Tool results feeding back into the
   chain stay at full fidelity. These are opposite directions on one axis and are
   recorded together precisely so a later reader does not "apply the length rule"
   to context. Depth belongs in the screen's own affordances -- panels, the queue,
   the transcript -- not in a longer paragraph the TTS has to read aloud.

3. **Speech and screen stay the same string.** Diverging (a short spoken line plus
   a fuller written answer) is the conventional voice-assistant design and was
   rejected: it costs a second generation per turn against the single endpoint
   that already stalls (ADR-0029).

4. **A tool error is recoverable; the chain continues.** The early return is
   removed. The failed step flows into `prior_steps`, the planner sees `ERROR: …`
   and decides -- retry, try a different tool, or answer with what it has.

5. **Guarded by a per-signature failure cap of two.** Keyed on the `sig` already
   computed at `chain_engine.py:126`. A model that varies its arguments after an
   error is making progress; one re-emitting the identical failing call is not.
   Same signal `completed_sigs` uses for the loop-break, inverted.

6. **A gate denial is never fed back.** It stays terminal. A tool error is
   information about the world and retrying it is diligence; a denial is a
   decision about authority and *routing around it is the failure mode* --
   `shell_exec` denied, then `files_write`, then `computer_use` to type it. Each
   is a fresh gate check on a different class, but the intent was already refused.
   From the planner's side that is indistinguishable from legitimately trying an
   alternative after a tool error, which is exactly why the two must not share a
   path.

7. **Per-plugin error classification is not built.** Transient-vs-permanent
   semantics would touch 70 plugins for a distinction the planner can usually
   draw from the error text. It can be added later, per-plugin, on top of these
   decisions without rework.

## Consequences

- **This may lower the model capability floor**, which is the constraint driving
  ADR-0029. A model reading structured tool results needs far less capability
  than one reconstructing them from prose. Build this first and re-measure before
  sizing shell mode.
- The loop-breaker (`completed_sigs` -> `finalize`) should become rare rather than
  routine. It stays as a backstop; if it stops firing, decision 1 worked.
- Chains get longer on average, since a failure no longer ends them. The step cap
  and the failure cap are the two bounds.
- Felix will occasionally retry something the user would rather it did not. The
  failure cap of two bounds the cost; the gate bounds the blast radius.
- One runnable check is owed: a tool that errors once then succeeds completes the
  chain; one that errors twice aborts it.

## Open

Whether decision 3 survives once the length rule is enforced globally. If one or
two sentences proves too thin for results that genuinely need detail, the escape
hatch is the screen (decision 2), not a second generation -- but that assumes the
screen is actually carrying it, which is ADR-0031's problem.
