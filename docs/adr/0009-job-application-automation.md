# ADR-0009: Job-application automation posture

**Date:** 2026-07-02
**Status:** Accepted

## Context

Felix applies to jobs on the user's behalf: read a **Job board** (Rat Race Rebellion, readable logged-out), rank openings against the user's résumé, and — for approved ones — drive the employer **ATS** to fill and **submit** an application. The design was locked in a grill-me session 2026-07-02 (see `CONTEXT.md` "Job-application pipeline"). Two things force an ADR rather than just issues:

1. **Submitting an application is irreversible and conflicts with ADR-0005 as written.** You cannot un-submit an application; a wrong or half-filled one permanently burns that employer. Submit is therefore an `irreversible` external action. But ADR-0005 says irreversible-flagged calls **force a modal every time** and are **never bypassable by a session/persistent grant** (the 2026-06-15 Recipe amendment reinforces this: "a Recipe saves the plan, never a grant"). The user's explicit goal is *autonomous* submission once trust is established. Full unattended auto-submit and ADR-0005's irreversible rule cannot both hold unchanged — the tension has to be resolved deliberately, not by quietly not-flagging submit.

2. **Mass auto-apply carries ToS and reputational risk** — the same shape as ADR-0006's Discord self-bot posture. Indiscriminate spray-applying violates ATS/job-board terms, reads as spam to employers, and burns the applicant's email reputation.

The user wants autonomy but independently established a correctness guard during the grill: the **zero-guessed rule** (never fill a field with an inferred value). That guard is what makes bounded autonomy defensible.

## Decision

**Submit is `irreversible=True` and routes through the ADR-0005 modal by default.** The **supervised ramp** *is* that modal: for the first N applications per profile (default ~5–10), Felix fills everything and stops at the ADR-0005 irreversible modal so the user validates its field-mapping before trusting it.

**One deliberate, opt-in, tool-scoped exception to ADR-0005's irreversible-always-modal rule.** The tool `job_apply_submit` — and only it — may proceed **without** the modal when **all** of the following hold:

1. the user has completed the ramp and explicitly enabled **auto-submit** in the Job Search panel (opt-in, per profile);
2. the **zero-guessed rule** holds for that application — every filled field is a **Known value** (Applicant dossier or Answer bank), nothing inferred;
3. no eligibility/knockout question (work authorization, sponsorship, relocation, start date, salary) was newly encountered.

If any condition fails, `job_apply_submit` falls back to the standard ADR-0005 irreversible modal, or to the notify-and-wait escalation for an unknown required field. This is the **only** place in the system where an irreversible action may run unattended, and it is justified by a machine-checkable correctness precondition (zero-guessed) that generic irreversible actions lack, plus a bounded blast radius (job applications under a dedicated jobs email; no money movement, no data deletion).

**Credentials reuse the existing model.** The jobs email and each login-requiring ATS are **Connected accounts** (per-profile, keyring — ADR-0005 2026-05-18). Account passwords are Felix-generated and stored in the ADR-0005 `password` `SECRET_FIELD` (2026-06-25 amendment), whose scope extends from `google_web` to job-application ATS providers — consistent with that amendment's "dedicated secondary account, never primary identity" rationale (Felix-created throwaway ATS logins qualify). No new capability class; no new `SECRET_FIELD`.

**Anti-spam is the Shortlist, not a rate limiter alone.** Applications only ever run against user-approved **Shortlist** entries (v1), which supplies real per-job human intent. Combined with the zero-guessed rule (no garbage submissions) and bail-and-notify on undrivable ATSes, indiscriminate mass auto-apply is structurally out of scope, not merely discouraged.

## Considered and rejected

- **Human-in-the-loop only (submit always modal, no exception).** Safest, and it needs no ADR-0005 carve-out — but it contradicts the user's stated goal of autonomy once trust is earned, and wastes the Answer-bank machinery whose whole point is to converge toward hands-off submission. The zero-guessed gate + ramp make bounded autonomy defensible enough to justify the narrow exception.
- **Full auto-submit without flagging submit `irreversible`.** Dishonest: submit *is* irreversible, and un-flagging it removes the ADR-0005 safety net for exactly the failure cases (a mis-mapped field slips through). The exception must sit *on top of* the irreversible flag, gated by a precondition — not delete the flag.
- **Draft-only (Felix assembles, user fills/submits).** Under-delivers on the goal; the dossier + generic filler are mostly wasted.
- **A generic "persistent grant bypasses irreversible" setting.** Rejected outright — it would blow a hole in ADR-0005 for *all* irreversible tools (send money, delete files). The exception is deliberately scoped to a single tool with a machine-checkable precondition, not a global loosening.
- **Per-ATS adapter code for reliability.** Rejected in favor of the generic LLM-driven filler (per-site data, not code) so coverage scales; unreliable sites bail-and-notify rather than being hand-coded one by one.

## Consequences

- ADR-0005's "irreversible always modals, never persistent-bypassed" invariant now has **exactly one** deliberate, tool-scoped exception (`job_apply_submit`), gated on `auto-submit` opt-in **and** the zero-guessed precondition **and** no new eligibility question. Recorded here so a future reader sees it was chosen, not overlooked. Any second such exception is a fresh ADR-level decision.
- The 16-class capability vocabulary is **unchanged**. Submit is `external_data_write` + `irreversible`; reading the jobs inbox for account-verification links is `external_data_read`; the résumé PDF read is `fs_read`; ATS/email password reads are `secrets_read`. A new plugin (`plugins/job_search.py`) owns the pipeline and is the sole carrier of the exception on its `job_apply_submit` tool.
- Autonomy grows monotonically with the **Answer bank**: the more it fills, the more applications satisfy zero-guessed and flow without a modal. Early runs are heavily modal-gated by construction; that is the intended ramp, not a defect.
- The `password` `SECRET_FIELD`'s scope is now "dedicated secondary accounts, including job-application ATS logins" — still never a primary identity. `delete_credential`'s tuple-sweep already covers it.
- **Post-v1 loosenings are not granted here.** Scheduled-daily cadence and threshold auto-selection (dropping the Shortlist approval step) would each further weaken the human gate and require their own ADR-level decision; v1 stays on-demand with Shortlist approval.

## Amendment — 2026-07-24 (B3 duplicate guard + B4 big-board scraping)

**B3 duplicate guard.** A suspected duplicate application (same company, title SequenceMatcher ratio >= 0.8 against any existing application) always forces the ADR-0005 modal — it is an explicit gate-FAILURE condition even when all S7 auto-submit conditions would otherwise pass. Suspected duplicates never auto-submit.

**B4 big-board scraping posture.** Pasting `linkedin.com`, `indeed.com`, or `glassdoor.com` as a board makes Felix search those sites via `python-jobspy`, which issues unauthenticated HTTP requests only — no cookies, no credentials, no authenticated session are ever passed to the scraper. Worst-case outcome is IP rate-limiting, which degrades gracefully (per-board error, other boards continue). LinkedIn "Easy Apply"-only postings (those with no external direct-apply URL) are out of scope and are silently skipped: applying through LinkedIn itself requires a LinkedIn account, which falls outside the logged-out-only posture of this scraping path. Only postings that carry an external ATS apply URL enter the pipeline and can eventually be submitted. Big-board scraping is discovery-only; all submission paths remain under the same zero-guessed + supervised-ramp gate established in the original decision.
