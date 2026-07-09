# OpenMind — Job Boards campaign (autonomous)

Driver for `scripts/run-boards.ps1`. Read `CONTEXT.md` ("Job-application
pipeline" glossary), `docs/adr/0009-job-application-automation.md`, and
`CLAUDE.md` first, then this file. Each issue body is that slice's full spec.

Round 2 goal (from the 2026-07-06 first live fetch): make the fetched
postings actionable — resolve RRR self-links to real ATS URLs, surface each
posting's ATS in the panel/search/detail block, collapsible panel sections,
and Ashby ATS support.

---

## Next slice — start here

Four tracer slices, one PR each, auto-merged to master in order. S4 and S6
depend on S3's URL resolution; work strictly top-down. After landing a
slice, **update this block**: tick the queue entry, set the next unticked
entry's `#N` + `Model:` as the active slice, and set `Status:` (`ready` while
slices remain; `done` after S6 lands).

Active slice: **S3 — #404**

Model: sonnet
Status: ready

(`Model:`/`Status:` are read directly by `scripts/run-boards.ps1`. Allowed:
haiku | sonnet | opus | fable. `Status: ready` = run the active slice;
`blocked` = needs a human; `done` = stop. Stop gracefully any time with
`scripts/stop-boards.ps1`.)

### Slice queue (work top-down; issue body = detail)

1. [x] S1 — #396 Job board list: store + IPC + panel UI + multi-board fetch — Model: sonnet
2. [x] S2 — #397 Generic LLM posting extractor fallback for non-RRR boards — Model: sonnet
3. [ ] S3 — #404 Resolve RRR self-link postings to the real ATS URL — Model: sonnet
4. [ ] S4 — #405 ATS badge + detail-block host + appliable filter + ATS search — Model: sonnet
5. [ ] S5 — #391 Collapsible panel sections (Credentials, Job Search, etc.) — Model: sonnet
6. [ ] S6 — #406 Ashby ATS support (detect, gate, fixture-tested mapping) — Model: sonnet

### Landed PRs

- PR #399 — S1: user-configurable Job board list (store + IPC + panel UI + multi-board fetch)
- PR #400 — S2: generic LLM posting extractor fallback for non-RRR boards

## SAFETY

- Build and unit-test against FAKES only: saved HTML fixtures for board and
  post pages, a stubbed navigate fn, a stubbed LLM extractor. NO live fetch
  of any job board or ATS, no real credentials, no real submissions.
- Seam injection must target the orchestrator-loaded module — NEVER
  `from plugins.job_search import set_*` in cerebral/ (the #153/#385 trap;
  `cerebral/tests/test_jobs_seam_wiring.py` guards this). Seams resolve at
  CALL time, never coalesced in __init__ (the #401 init-capture trap;
  regression test in test_plugin_job_search.py).
- Store writes coerce LLM-shaped input (explicit nulls) and roll back on
  failure so a bad insert cannot wedge the process DB lock (#388 precedent).
- If the tray renders a section from a client-side list that mirrors a
  server-side list, comment the pairing on BOTH sides (#390 lesson).
- The user has LIVE data in job_postings (100 rows, 5 shortlisted with
  status/fit_score) — migrations and the S3 URL repair must preserve
  existing rows' status and fit_score. Test the backfill against a fixture
  store seeded with scored/shortlisted rows.
- Anything only checkable live goes on the `docs/jobs-live-verify.md`
  checklist — do NOT perform it in the loop.
