# ADR-0036: Admission control -- a per-failure-domain cap, proposal-adjusted

**Date:** 2026-09-06
**Status:** Accepted (grill session)
**Relates:** ADR-0028 R5 (the scarce resource is the scheduler, and it is
always singular; nothing preempts), the **Failure domain** and **Proposal**
terms in CONTEXT.md, ADR-0034 (verification contract -- same "small
mechanical contract, not a subsystem" shape).

## Context

R5's amendment identified the gap and left it explicitly undecided: nothing
today bounds concurrent calls to a remote endpoint whose known failure mode
is stalling under load. Checked `cerebral/llm/router.py` directly -- no
semaphore, no queue, no admission control of any kind exists. Every call
site already tags `task_type` (`"chat"`, `"self_dev"`, `"video"`,
`"quality"`, ...) for model-pin routing, which is the natural existing hook
for priority classification -- no new parameter needed.

The instinct to make the cap self-tuning ("use what's needed, not beyond the
maximum for connected") ran into a real constraint: nobody has ever measured
the current endpoint's actual concurrency ceiling (no documented rate limit,
no `/v1/models`, only observed intermittent stalls). Tuning against an
unmeasured number is premature -- R2 (promote on the third repeat) argues
for the manual version first.

## Decision

1. **`asyncio.Semaphore`-based cap, scoped per Failure domain, not global.**
   Reuses the existing **Failure domain** concept rather than inventing a
   new scoping unit. One domain exists today (Budd), so it is one number in
   practice, but a second remote endpoint later gets its own cap by keying
   into the existing concept, not a code change.
2. **Default cap = 1**, matching R5's "singular" language literally.
3. **Priority is queue-order among waiters, never preemption** (R5:
   "nothing preempts"). A call already holding a domain's slot runs to
   completion untouched. Among callers waiting for that slot, `task_type ==
   "chat"` (the live conversational turn) is served ahead of any waiting
   background `task_type`, regardless of arrival order. A background call
   already in flight is never interrupted -- the acknowledged consequence is
   that a chat turn can still queue behind an in-flight background call with
   no way to jump it, which is the accepted cost of R5's no-preemption rule.
4. **The cap is a System setting**, editable in the Settings panel like
   `active model + per-task model assignments` already is.
5. **Felix adjusts the cap only via a Proposal, never silently.** Reuses the
   existing "Felix proposes, the user decides" queue rather than building
   self-tuning logic. Observed repeated stalls, or sustained headroom with
   none, is what raises the Proposal -- the user approves or dismisses like
   any other. This is the load-bearing rule of this ADR: a shared resource
   limit is not something Felix's own judgment gets to change unilaterally,
   even when the judgment is probably right.
6. **No auto-probing / binary-search-the-real-limit algorithm now.** Nothing
   today has hit this as a repeat problem -- if stall-vs-headroom evidence
   accumulates over time from the Proposal history, that accumulated
   evidence is the third-repeat signal that would justify a real auto-tune
   algorithm later.

## Consequences

- Fixes the actual gap (zero admission control today) with a small,
  bounded mechanism rather than a scheduler or work queue -- consistent
  with R5's rejection of both.
- Extending to a second remote endpoint later requires no new concept,
  only a second Failure domain being registered.
- The user remains the final authority on a shared, contended resource
  limit; Felix's role is evidence-gathering and proposing, not deciding.
