# ADMISSION-CONTROL.md -- Admission control campaign driver

Consumed by Felix's own `self_dev` loop overnight (ADR-0015), not a
Claude-Code-run `run-*.ps1` loop. Each slice = one issue = one self_dev PR
(clone -> edit -> sandbox test gate -> PR), merged before the next
dependent slice starts. See docs/adr/0036-admission-control.md.

## Status: done (N cut)

L and M are landed on master and close the campaign's real gap (zero
admission control on a remote endpoint with a known stalling failure
mode). N was cut after a self_dev attempt produced a disconnected
simulation rather than a real integration -- see its Queue entry. Not
reattempting or hand-implementing: the issue itself and ADR-0036
pre-approve cutting N if the campaign needs to shrink, and it does not
block anything else.

## Queue

- [x] L -- #1134 -- per-Failure-domain semaphore in router.py, cap=1, chat-priority queue. A self_dev attempt (PR #1154, closed) got the right file but keyed the cap by model_id instead of by host/Failure domain, only wrapped `complete()` (leaving `complete_with_tools` -- the actual hot path -- and `complete_with_images` uncapped), had a genuine race in the release handoff, and added no tests. Hand-implemented instead (commit 1e6803e): `_DomainSemaphore` keyed by `backend.url`, covers all three router entry points, race-free slot handoff, 4 new concurrency tests in test_router.py.
- [x] M -- #1135 -- expose the cap as a System setting. A self_dev attempt (PR #1155, closed) only registered the settings schema key (5 lines) with a default of 10 that silently contradicted L's ADR-mandated default of 1 -- no UI, no settings_control wiring, no connection to the live router at all (the actual acceptance criterion). Hand-implemented instead (commit 27110f0): `ModelRouter.set_admission_cap()` live-updates every domain semaphore, wired from both the `set_system_setting` tool and the Settings panel's direct `set_setting` path, new "Admission cap" input in the Settings panel.
- [x] N -- #1136 (cut) -- Felix proposes cap changes from observed stalls. A self_dev attempt (PR #1156, closed) built a brand-new, fully disconnected `cerebral/admission_control.py`: its `AdmissionController.record_stall`/`record_headroom` are never called from `router.py` anywhere (no real stall would ever reach it), and its `ProposalQueue` is a fake in-memory class -- not the real `cerebral/action_queue/manager.py` `QueueManager` the actual Queue panel reads, so even a call into it would never surface to the user or connect back to M's `set_admission_cap`. Cut rather than reattempted or hand-built: the issue text and ADR-0036 both pre-approve this as the most cuttable slice, and L+M alone already close the gap this campaign exists for.

## Landed PRs

- L -- hand-implemented on master (1e6803e); PR #1154 closed unmerged
- M -- hand-implemented on master (27110f0); PR #1155 closed unmerged
- N -- cut, not implemented; PR #1156 closed unmerged

## SAFETY

- `cerebral/security/` (the ADR-0005 gate) is untouched by this campaign --
  admission control lives entirely in `cerebral/llm/router.py`. Note the
  blast-radius gate no longer blocks merge on a guardrail-path hit (the
  2026-08-21 full-auto-merge amendment made that informational-only); the
  only real gate left is test status. A slice touching `router.py` will
  auto-merge on green tests with no human review step -- keep this
  campaign's diffs narrowly scoped to router.py/settings so a subtle bug
  isn't waved through on a passing but incomplete test.
- No preemption, ever (R5). A call already holding a domain's slot runs to
  completion untouched, even when a `chat` call is waiting. Priority is
  queue-order among *waiters* only.
- Slice N raises Proposals, never silently changes the cap. If N's
  diff writes directly to the System setting without going through the
  Proposal queue, that is a spec violation, not an optimization.
