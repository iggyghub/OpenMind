# SANDBOX-BUILD.md — shell_exec sandbox campaign driver

Autonomous slice loop for **ADR-0010: shell_exec subprocess sandbox**. Read
`docs/adr/0010-shell-exec-sandbox.md` and CONTEXT.md "Shell sandbox" for the design.
`scripts/run-sandbox.ps1` drives this file. Each slice = one issue = one PR, merged to
master before the next starts (successive slices edit the same `cerebral/sandbox/` files).

## Status: ready

<!-- ready = slices remain; done = SBX-4 landed; blocked = a session needs a human -->

## Next slice — start here

- **Active:** SBX-3 — #354
- **Model:** sonnet

## Queue

- [x] SBX-1 — #352 — Job Object resource caps + wall-clock kill (Python/pywin32)
- [x] SBX-2 — #353 — AppContainer network-deny + per-profile workdir kernel ACL
- [ ] SBX-3 — #354 — env scrub + wire shell_exec to always execute sandboxed
- [ ] SBX-4 — #355 — gate shell_exec deny→ask opt-in on sandbox availability (fail-closed)

## Landed PRs

- SBX-1 -> PR #356
- SBX-2 -> PR #357

## SAFETY

- Build + unit-test only. Tests must **never** disable the caller's own OS protections,
  weaken the host, or leave an AppContainer profile / orphan process behind — delete
  test profiles and kill test children in teardown.
- Windows-only. Non-Windows keeps `shell_exec` denied (fail-closed) — do not add a
  non-Windows execution path.
- No change to the 16-class capability vocabulary. This campaign is execution mechanics
  beneath the existing `shell_exec` class (ADR-0005), not a permissions change.
- If a slice genuinely needs a human decision, set `Status: blocked` with a one-line
  reason and stop without merging.
