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

Tracer slices, one PR each, auto-merged to master in order. S4 and S6
depend on S3's URL resolution; S7 depends on S4's filters; work strictly
top-down. After landing a slice, **update this block**: tick the queue
entry, set the next unticked entry's `#N` + `Model:` as the active slice,
and set `Status:` (`ready` while slices remain; `done` after S7 lands —
S8 already landed early by hand).

Active slice: **S5 — #391**

Model: sonnet
Status: ready

(`Model:`/`Status:` are read directly by `scripts/run-boards.ps1`. Allowed:
haiku | sonnet | opus | fable. `Status: ready` = run the active slice;
`blocked` = needs a human; `done` = stop. Stop gracefully any time with
`scripts/stop-boards.ps1`.)

### Slice queue (work top-down; issue body = detail)

1. [x] S1 — #396 Job board list: store + IPC + panel UI + multi-board fetch — Model: sonnet
2. [x] S2 — #397 Generic LLM posting extractor fallback for non-RRR boards — Model: sonnet
3. [x] S3 — #404 Resolve RRR self-link postings to the real ATS URL — Model: sonnet (PR #784, 7c9ca07)
4. [x] S4 — #405 ATS badge + detail-block host + appliable filter + ATS search — Model: sonnet (PR #785, d082b9d)
5. [ ] S5 — #391 Collapsible panel sections (Credentials, Job Search, etc.) — Model: sonnet
6. [x] S6 — #406 Ashby ATS detection + live-verify entry — Model: sonnet
7. [ ] S7 — #412 "Approve all visible" bulk action on the Shortlist — Model: sonnet
8. [x] S8 — #413 Apply button on approved Shortlist cards (non-blocking) — landed early by hand (PR #416; appliable-gating deferred into S4 #405)

### Landed PRs

- S3 — PR #784 (7c9ca07). Resolution happens inline in the fetch loop via
  `upsert_resolved(old_url, posting)`, which migrates `status` + `fit_score`
  onto the new url and deletes the stale RRR row. Those two are the ONLY
  user-decision columns on `job_postings` (verified against the schema).
  No separate backfill routine: the same path repairs a stuck row whenever
  the board re-lists that posting.
  CAVEAT for the live test — repair is therefore driven by the board's
  listing. A stuck posting that has since aged OFF the RRR listing page will
  not be re-surfaced and so will not self-repair. Check whether all 29 (and
  in particular the 2 approved ones) are still listed; any that aren't need
  a deliberate backfill pass.

- PR #399 — S1: user-configurable Job board list (store + IPC + panel UI + multi-board fetch)
- PR #400 — S2: generic LLM posting extractor fallback for non-RRR boards
- PR #416 — S8 (early, by hand): Apply button on approved Shortlist cards
- PR #415 — (unqueued bugfix) #414 browser-session dual-module trap in the jobs seams

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
