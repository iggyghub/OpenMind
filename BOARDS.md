# OpenMind — Job Boards campaign (autonomous)

Driver for `scripts/run-boards.ps1`. Read `CONTEXT.md` ("Job-application
pipeline" glossary), `docs/adr/0009-job-application-automation.md`, and
`CLAUDE.md` first, then this file. Each issue body is that slice's full spec.

Goal: replace the hardcoded `RRR_URL` job board with a user-configurable
**Job board** list (store + IPC + Job Search panel UI + multi-board fetch),
then a generic LLM posting extractor so non-RRR boards work with zero
per-site parser code. The board list seeds EMPTY — the user adds
ratracerebellion.com themselves after the campaign lands.

---

## Next slice — start here

Two tracer slices, one PR each, auto-merged to master in order — S2 builds on
S1's table + fetch loop, so they MUST land sequentially. After landing a
slice, **update this block**: tick the queue entry, set the next unticked
entry's `#N` + `Model:` as the active slice, and set `Status:` (`ready` while
slices remain; `done` after S2 lands).

Active slice: **S1 — #396**

Model: sonnet
Status: ready

(`Model:`/`Status:` are read directly by `scripts/run-boards.ps1`. Allowed:
haiku | sonnet | opus | fable. `Status: ready` = run the active slice;
`blocked` = needs a human; `done` = stop. Stop gracefully any time with
`scripts/stop-boards.ps1`.)

### Slice queue (work top-down; issue body = detail)

1. [ ] S1 — #396 Job board list: store + IPC + panel UI + multi-board fetch — Model: sonnet
2. [ ] S2 — #397 Generic LLM posting extractor fallback for non-RRR boards — Model: sonnet

### Landed PRs

(none yet)

## SAFETY

- Build and unit-test against FAKES only: saved HTML fixtures for board pages,
  a stubbed navigate fn, a stubbed LLM extractor. NO live fetch of any job
  board or ATS, no real credentials, no real submissions.
- Seam injection must target the orchestrator-loaded module — NEVER
  `from plugins.job_search import set_*` in cerebral/ (the #153/#385 trap;
  `cerebral/tests/test_jobs_seam_wiring.py` guards this).
- Store writes coerce LLM-shaped input (explicit nulls) and roll back on
  failure so a bad insert cannot wedge the process DB lock (#388 precedent).
- If the tray renders a section from a client-side list that mirrors a
  server-side list, comment the pairing on BOTH sides (#390 lesson).
- Anything only checkable live goes on the `docs/jobs-live-verify.md`
  checklist — do NOT perform it in the loop.
