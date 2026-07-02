# OpenMind — Job-Application Pipeline (autonomous campaign)

Driver for `scripts/run-jobs.ps1`. Read `CONTEXT.md` ("Job-application pipeline"
glossary), `docs/adr/0009-job-application-automation.md`, and `CLAUDE.md` first,
then this file. The locked design lives in those docs; each issue body = that
slice's detail.

---

## Next slice — start here

**Job-application pipeline epic.** 7 core tracer slices (+1 optional), one PR
each, auto-merged to master in order — successive slices build on the same new
files (`plugins/job_search.py`, the SQLite tables, the Job Search panel,
`cerebral/main.py` wiring), so they MUST land sequentially. Work strictly
top-down; the order already satisfies every `Blocked by`. After landing a slice,
**update this block**: tick the queue entry, set the next unticked entry's
`#N` + `Model:` as the active slice here, and set `Status:` (`ready` while slices
remain; `done` after S7 lands — S8 is optional and left for a human to trigger).

Active slice: **S1 — #334**

Model: sonnet
Status: ready

(`Model:`/`Status:` are read directly by `scripts/run-jobs.ps1`. Allowed:
haiku | sonnet | opus | fable — prefer haiku/sonnet, this is meant to run on an
efficient model. `Status: ready` = run the active slice; `blocked` = needs a
human; `done` = stop. Stop gracefully any time with `scripts/stop-jobs.ps1`.)

### Slice queue (work top-down; spec = CONTEXT.md + ADR-0009; issue body = detail)

1. [ ] S1 — #334 Job board -> Job postings in a Job Search panel — Model: sonnet
2. [ ] S2 — #335 Resume ingestion -> Applicant dossier — Model: sonnet
3. [ ] S3 — #336 Shortlist: fit-score + user approval — Model: sonnet
4. [ ] S4 — #337 Apply to ONE clean ATS end-to-end (spine), review-before-submit — Model: sonnet
5. [ ] S5 — #338 Answer bank + ChromaDB semantic matching + notify-and-learn — Model: sonnet
6. [ ] S6 — #339 Account-creation + email verification — Model: sonnet
7. [ ] S7 — #340 Gated auto-submit (ADR-0009) — supervised ramp + zero-guessed — Model: sonnet
8. [ ] S8 — #341 (OPTIONAL — human triggers) Undrivable-ATS bail-and-notify — Model: haiku

### Landed PRs

_(none yet)_

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
