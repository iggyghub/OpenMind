# OpenMind — Job-Application Pipeline (autonomous campaign)

Driver for `scripts/run-jobs.ps1`. Read `CONTEXT.md` ("Job-application pipeline"
glossary), `docs/adr/0009-job-application-automation.md`, and `CLAUDE.md` first,
then this file. The locked design lives in those docs; each issue body = that
slice's detail.

---

## Next slice — start here

**Job boards v2 campaign** (grilled 2026-07-24). Generalized board sourcing:
paste any job-site URL, Felix classifies it (Greenhouse/Lever JSON APIs, JobSpy
big-board search, generic scrape fallback) — plus a duplicate-application
guard. 4 slices, one PR each, auto-merged to master strictly top-down —
successive slices build on the same files (`plugins/job_search.py`, its SQLite
tables, the Job Search panel), so they MUST land sequentially. After landing a
slice, **update this block**: tick the queue entry, set the next unticked
entry's `#N` + `Model:` as the active slice here, and set `Status:` (`ready`
while unticked queue entries remain; `done` when the queue is complete).

Active slice: **B1 — #508**

Model: sonnet
Status: ready

(`Model:`/`Status:` are read directly by `scripts/run-jobs.ps1`. Allowed:
haiku | sonnet | opus | fable — prefer haiku/sonnet, this is meant to run on an
efficient model. `Status: ready` = run the active slice; `blocked` = needs a
human; `done` = stop. Stop gracefully any time with `scripts/stop-jobs.ps1`.)

### Slice queue (work top-down; spec = CONTEXT.md + ADR-0009; issue body = detail)

1. [ ] B1 — #508 Generalized board input + provider seam — Model: sonnet
2. [ ] B2 — #509 Greenhouse/Lever postings-API providers — Model: sonnet
3. [ ] B3 — #510 Duplicate-application guard — Model: sonnet
4. [ ] B4 — #511 JobSpy big-board search provider (logged-out only) — Model: sonnet

### Previous campaign — Job-application pipeline epic (landed)

1. [x] S1 — #334 Job board -> Job postings in a Job Search panel — Model: sonnet
2. [x] S2 — #335 Resume ingestion -> Applicant dossier — Model: sonnet
3. [x] S3 — #336 Shortlist: fit-score + user approval — Model: sonnet
4. [x] S4 — #337 Apply to ONE clean ATS end-to-end (spine), review-before-submit — Model: sonnet
5. [x] S5 — #338 Answer bank + ChromaDB semantic matching + notify-and-learn — Model: sonnet
6. [x] S6 — #339 Account-creation + email verification — Model: sonnet
7. [x] S7 — #340 Gated auto-submit (ADR-0009) — supervised ramp + zero-guessed — Model: sonnet
8. [ ] S8 — #341 (OPTIONAL — human triggers) Undrivable-ATS bail-and-notify — Model: haiku

### Landed PRs

- S1 #334 — PR #342 (feat(jobs): S1 — Job board -> Job postings in a Job Search panel)
- S2 #335 — PR #343 (feat(jobs): S2 — Resume ingestion -> Applicant dossier)
- S3 #336 — PR #344 (feat(jobs): S3 — Shortlist: fit-score Job postings + user approval)
- S4 #337 — PR #345 (feat(jobs): S4 — Apply spine: Greenhouse/Lever guest-apply, review-before-submit)
- S5 #338 — PR #346 (feat(jobs): S5 — Answer bank + ChromaDB semantic field-matching + notify-and-learn)
- S6 #339 — PR #347 (feat(jobs): S6 -- Account-creation + email verification)
- S7 #340 — PR #348 (feat(jobs): S7 -- Gated auto-submit (ADR-0009): supervised ramp + zero-guessed exception)

---

## SAFETY — read before every slice

This campaign builds code that will one day drive **real job applications**. The
autonomous loop must NEVER perform a real application. Hard rules:

- **Build + unit-test against FAKES only.** Reuse the browser harness's
  `FakeDriver` seam; stub the LLM/embeddings; use saved HTML/PDF fixtures. NO
  live network, NO real ATS, NO real submission, NO real account creation, NO
  real credentials, NO real inbox reads, NO real logins seeded.
- **Live verification is a HUMAN step.** For any slice whose real behaviour can
  only be checked live (S4 real Greenhouse/Lever run, S6 real account creation),
  append a checklist item to `docs/jobs-live-verify.md` — do NOT perform it.
- **Never** run `plugins/discord_user.py` or any self-bot path (ADR-0006
  real-account-ban risk). No paid APIs, no real messages/calls.
- If a slice genuinely cannot be completed without a human (a real login, a live
  ATS), set `Status: blocked` with a one-line reason and stop.
