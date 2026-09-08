# ADMISSION-CONTROL.md -- Admission control campaign driver

Consumed by Felix's own `self_dev` loop overnight (ADR-0015), not a
Claude-Code-run `run-*.ps1` loop. Each slice = one issue = one self_dev PR
(clone -> edit -> sandbox test gate -> PR), merged before the next
dependent slice starts. See docs/adr/0036-admission-control.md.

## Status: ready

## Next slice -- start here

- **Active:** M -- #1135
- **Model:** self_dev's `task_type="self_dev"` router pin (local/cloud/connected server, per ADR-0015).

## Queue

- [x] L -- #1134 -- per-Failure-domain semaphore in router.py, cap=1, chat-priority queue. A self_dev attempt (PR #1154, closed) got the right file but keyed the cap by model_id instead of by host/Failure domain, only wrapped `complete()` (leaving `complete_with_tools` -- the actual hot path -- and `complete_with_images` uncapped), had a genuine race in the release handoff, and added no tests. Hand-implemented instead (commit 1e6803e): `_DomainSemaphore` keyed by `backend.url`, covers all three router entry points, race-free slot handoff, 4 new concurrency tests in test_router.py.
- [ ] M -- #1135 -- expose the cap as a System setting (depends on L -- done)
- [ ] N -- #1136 -- Felix proposes cap changes from observed stalls (depends on L, M) -- most cuttable slice; L+M alone already close the "zero admission control" gap

## Landed PRs

- L -- hand-implemented on master (1e6803e); PR #1154 closed unmerged

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
