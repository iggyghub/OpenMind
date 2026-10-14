# ADR-0034: The verification contract -- one interface, per-mechanism meaning

**Date:** 2026-09-06
**Status:** Accepted (grill session)
**Relates:** ADR-0028 (R6 "verified running, or it did not ship"; R3's five
acquisition mechanisms), ADR-0015 (self-dev's sandbox test gate, the one
mechanism that already has this), ADR-0026 (trading's Confidence weight --
the first real `score` producer), ADR-0005 (the 16-class gate this mirrors
the shape of).

## Context

R6 states a uniform rule -- "verified running, or it did not ship" -- but
defines no mechanism. In practice each of the five capability-acquisition
paths (ADR-0028 R3) is verified differently, or not at all:

| Mechanism | Verification today |
|---|---|
| Plugin | `cerebral/tests/test_plugin_<name>.py`, called "mandatory" by the scaffold checklist -- but unenforced: 58 of 68 plugins have one, 10 do not, nothing blocks registration without it |
| self_dev diff | Enforced: the full suite runs inside the sandbox clone and gates the PR |
| Skill | Nothing -- it is instructions, not code; there is no unit to `pytest` |
| Recipe | Nothing -- it replays already-gated tool calls, so the only new failure mode is the chain going stale underneath it |
| Growth-loop tool | Produces a plugin, so same gap as Plugin |

Two shapes were considered for closing this:

1. **A hardcoded per-mechanism table**, enforced individually. Fast, but
   this is a long-lived project where mechanisms are added and retired over
   time -- a table means editing this ADR (or its code equivalent) every
   time a sixth mechanism shows up.
2. **A mechanism-agnostic eval harness** that scores output quality
   (LLM-as-judge, regression tracking). Solves a different, bigger problem
   ("is the output good") that nothing today has hit -- no repeat, no
   incident -- and is a multi-week build, not a slice.

## Decision

**One interface, implemented per mechanism, with an optional quality payload.**

Every mechanism type implements:

```
verify(self) -> VerifyResult
VerifyResult = { passed: bool, evidence: str, score: float | None }
```

- `passed` and `evidence` are mandatory for every mechanism -- `evidence` is
  the human-readable receipt that something was actually run, not just
  claimed (R6's "running" half, made checkable).
- `score` is optional and mechanism-defined. Most mechanisms leave it
  `None` -- pass/fail is the whole story for a plugin's pytest run or a
  Skill's witnessed live run. A mechanism only populates it when it has
  its own notion of quality worth carrying, the way trading's **Confidence
  weight** (ADR-0026) already does for Strategies. `score` is not ported
  into a universal judge; Confidence weight becomes the first real
  producer of this field, not a template every mechanism must fill.
- Discovery mirrors `REQUIRED_CAPABILITIES`: `verify` is a method the
  orchestrator can introspect on any registered mechanism, not a
  registry entry maintained by hand. A new mechanism type added years from
  now needs to implement one method; nothing elsewhere needs editing.

**Per-mechanism meaning of `verify()`, resolved in this session:**

| Mechanism | `verify()` runs... |
|---|---|
| Plugin / growth-loop tool | `cerebral/tests/test_plugin_<name>.py`; orchestrator refuses to register a plugin with none |
| self_dev diff | The existing sandbox suite (already built, ADR-0015) |
| Skill | One witnessed live run against a real task; a human confirms the output, since there is no unit in isolation |
| Recipe | A dry-run replay of the frozen chain against stub/last-known-good args, catching a tool signature that changed underneath it |

## Consequences

- Closes the 10-plugin enforcement gap as a side effect of making `verify()`
  mandatory rather than a checklist item.
- Skills and Recipes go from zero verification story to one, without
  inventing new machinery for either.
- Rejects building a scoring/eval-harness subsystem now. If a specific
  mechanism other than trading later wants a `score`, it defines its own --
  this ADR does not need revisiting to allow that.
