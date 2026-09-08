# VERIFICATION-CONTRACT.md -- Verification contract campaign driver

Consumed by Felix's own `self_dev` loop overnight (ADR-0015), not a
Claude-Code-run `run-*.ps1` loop. Each slice = one issue = one self_dev PR
(clone -> edit -> sandbox test gate -> PR), merged before the next
dependent slice starts. See docs/adr/0034-verification-contract.md.

## Status: ready

## Next slice -- start here

- **Active:** F -- #1128
- **Model:** self_dev's `task_type="self_dev"` router pin (local/cloud/connected server, per ADR-0015) -- not a fixed Claude-Code model choice.

## Queue

- [x] A -- #1123 -- VerifyResult + Verifiable protocol types (foundation, blocks C-F)
- [x] B -- #1124 -- backfill missing plugin test stubs -- self_dev's PR #1138 was broken (invented fetch_fn/plugin.run() pattern) and also missed 7 plugins the issue's own count undercounted; hand-fixed instead of retrying (commits b2bdb29, b048bce), PR #1138 closed unmerged. 68/68 plugins now covered.
- [x] C -- #1125 -- Plugin verify() + registration-time enforcement -- self_dev's PR #1139 was structurally broken (module-level RuntimeError that would crash the whole boot, called before plugins even registered); hand-fixed at the real per-plugin refusal point in MCPOrchestrator._load_plugin_file, added verify_test_files opt-in flag so ~20 existing discovery tests weren't broken (commit ec0e40e), PR #1139 closed unmerged.
- [x] D -- #1126 -- Skill verify() -- witnessed-run evidence field. PR #1140 was actually correct as self_dev wrote it (no hand-fix needed) -- its `tests_failed` was the same environmental full-suite-timeout false alarm as before; confirmed via a clean local full-suite run before merging.
- [x] E -- #1127 -- Recipe verify() -- dry-run replay. PR #1141 returned a bare dict instead of VerifyResult (breaking the contract's own type uniformity) and had zero tests; hand-fixed on Recipe itself (commit 8b80dea), PR #1141 closed unmerged.
- [ ] F -- #1128 -- self_dev verify() adapter over the existing sandbox gate (depends on A)

D, E, F may run in parallel with B/C once A lands.

## Landed PRs

- PR #1137 -- A (auto-merged by self_dev_campaign)
- B -- hand-fixed on master (b2bdb29, b048bce); PR #1138 closed unmerged, superseded
- C -- hand-fixed on master (ec0e40e); PR #1139 closed unmerged, superseded
- PR #1140 -- D (merged as-is after a full-suite recheck cleared its timeout false alarm)
- E -- hand-fixed on master (8b80dea); PR #1141 closed unmerged, superseded
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
