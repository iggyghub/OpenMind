# VERIFICATION-CONTRACT.md -- Verification contract campaign driver

Consumed by Felix's own `self_dev` loop overnight (ADR-0015), not a
Claude-Code-run `run-*.ps1` loop. Each slice = one issue = one self_dev PR
(clone -> edit -> sandbox test gate -> PR), merged before the next
dependent slice starts. See docs/adr/0034-verification-contract.md.

## Status: ready

## Next slice -- start here

- **Active:** A -- #1123
- **Model:** self_dev's `task_type="self_dev"` router pin (local/cloud/connected server, per ADR-0015) -- not a fixed Claude-Code model choice.

## Queue

- [ ] A -- #1123 -- VerifyResult + Verifiable protocol types (foundation, blocks C-F)
- [ ] B -- #1124 -- backfill the 10 missing plugin test stubs (must land before C)
- [ ] C -- #1125 -- Plugin verify() + registration-time enforcement (depends on A, B)
- [ ] D -- #1126 -- Skill verify() -- witnessed-run evidence field (depends on A)
- [ ] E -- #1127 -- Recipe verify() -- dry-run replay (depends on A)
- [ ] F -- #1128 -- self_dev verify() adapter over the existing sandbox gate (depends on A)

D, E, F may run in parallel with B/C once A lands.

## Landed PRs

## SAFETY

- Registration-time enforcement (C) must never land before the backfill
  (B) -- flipping it first would refuse to register the 10 plugins the
  backfill hasn't reached yet, breaking the live boot.
- `verify()` for Plugin (C) is a cheap existence check at registration
  time, not a live pytest re-run on every boot -- the sandbox's own test
  gate already runs the full suite during self_dev.
- No mechanism outside this list gets a `score` requirement -- `score`
  stays `None` unless a mechanism has its own notion of quality worth
  carrying (see ADR-0034; trading's Confidence weight is the only current
  producer and is out of scope for this campaign).
