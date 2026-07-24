# Job-Application Pipeline — human live-verify checklist

The autonomous `run-jobs.ps1` loop builds and unit-tests every slice against
fakes/fixtures only (see `JOBS.md` "SAFETY"). It never drives a real ATS, submits
a real application, creates a real account, or uses real credentials.

Anything that can only be confirmed against the live world goes here, for a human
to run by hand once the code has landed. Each slice appends its live checks below.

## How to run a live check safely

- Use the dedicated jobs email + a throwaway test posting where possible.
- Nothing is truly submitted until you click the review-before-submit modal.
- If a check fails, open an issue rather than editing the loop mid-campaign.

---

## Getting started — your first live run

**Do the checks in this order:** S4 (guest-apply) first — it needs no jobs email
and no account, so it is the cheapest way to prove the spine. Only then S5
(answer bank), then S6 (account-creation, which needs the jobs Gmail), then S7
(auto-submit, which needs ~5 reviewed submits first).

### One-time prerequisites

1. **Start Cerebral:** `python -m cerebral.main`. Open Felix from the tray
   (`Open Felix`).
2. **Upload your resume:** in the Conversation, attach the single-page PDF and
   say "store this as my resume". This populates the **Applicant dossier**
   (name, contact, work history) that fills form fields. Verify it under the
   **Job Search panel** (or ask Felix "what's in my applicant dossier?").
3. **(S6 only) Create the dedicated jobs Gmail** and seed it as the `jobs_email`
   Connected account in the **Credentials panel** (attended login, same flow as
   `scripts/seed_browser_login.py`). Not needed for S4/S5 guest-apply.

### Running S4 (guest-apply) — the safe first test

1. Open the **Job Search panel** → **Check for new jobs**. Felix reads the RRR
   feed, scores postings, and shows a **Shortlist**.
2. **Approve one** posting whose apply link is a **Greenhouse or Lever
   guest-apply** form (open the RRR link in a browser first to confirm it does
   NOT ask you to create an account).
3. Trigger the apply. Felix opens the ATS, fills fields from the dossier, and
   uploads the resume. **Walk the S4 checklist below** against what you see.
4. **STOP at the review-before-submit modal to verify everything WITHOUT sending
   a real application.** Reaching the modal proves fetch → score → fill → upload
   → required-field detection. Do NOT click confirm unless you actually want to
   apply to that job.

> **These are REAL applications to REAL employers.** Clicking the
> review-before-submit modal sends a genuine application. For verification,
> reach the modal and cancel; only confirm on jobs you truly want. There is no
> "undo".

### After S4 passes

- **S5:** repeat on a posting with a custom question; answer once, confirm it is
  reused (and semantically matched) next time.
- **S6:** with the jobs Gmail seeded, pick a login-gated ATS and verify
  account-creation + email verification.
- **S7:** only after ~5 reviewed submits (the supervised ramp), enable
  **auto-submit** in the panel and confirm it still stops on any guessed field
  or new eligibility question.

---

## Checklist

_(slices append their live-verify items here as they land)_

### S4 -- #337: Apply spine (Greenhouse/Lever guest-apply, review-before-submit)

- [ ] Open a real Greenhouse guest-apply posting: call `browser_open_session`, then
      `jobs_apply_start` with its URL. Verify the form is navigated to and text fields
      are filled from the Applicant dossier (name, email, phone, location) without
      submitting.
- [ ] Verify the resume PDF is attached to the file input (`upload_file` called on
      `input[type=file]` or the ATS-specific selector).
- [ ] Confirm a required field not in the dossier (e.g. a custom ATS question) causes
      `jobs_apply_start` to return `awaiting-input` with the missing field listed, and
      the application is logged as `awaiting-input` in SQLite -- NOT submitted.
- [ ] Open a real Lever guest-apply posting and repeat the above.
- [ ] Verify a Workday or bespoke ATS URL causes immediate bail: `jobs_apply_start`
      returns `failed`, the Application row is logged as `failed`, and no form is
      partially filled.
- [ ] Call `jobs_apply_submit` on a filled-but-not-submitted pending application and
      confirm the ADR-0005 irreversible modal fires in the tray before anything is sent.
- [ ] After modal confirmation, verify the form is submitted (network request visible
      in browser DevTools) and the Application row flips to `submitted` with a
      `submitted_at` timestamp in SQLite.
- [ ] Verify that re-running `jobs_apply_start` on a URL that already has a `submitted`
      Application row (same ATS URL) either warns or upserts -- never produces a second
      row (URL dedup invariant).

### S2 (boards campaign) -- #397: Generic LLM posting extractor fallback

- [ ] Add a non-RRR job board in the Job Search panel (e.g. a real work-from-home
      aggregator whose HTML the static parser cannot match). Trigger "Check for new
      jobs" and confirm Cerebral logs "LLM extractor called for <board URL>" and
      at least one posting appears in the panel with a valid https:// ATS URL.
- [ ] Verify the per-board result in the IPC response includes `fetched` > 0 for the
      non-RRR board (postings reached `store.upsert` via the LLM path).
- [ ] Add the RRR board as well. Trigger fetch again and confirm the RRR board uses the
      static parser (check logs — no "LLM extractor" line for the RRR URL).
- [ ] Add a board whose HTML the LLM also cannot parse (e.g. a login-gated page).
      Confirm the fetch completes without error and the per-board entry shows
      `"note": "0 postings (unrecognised layout)"` in the IPC payload.

### S6 -- #339: Account-creation + email verification

- [ ] Seed the jobs-email Connected account via the Credentials panel (provider
      `jobs_email`): set the email address. Confirm it appears as `jobs_email_configured:
      true` in the `jobs_update` event the tray receives.
- [ ] Open a Greenhouse posting that requires an account (login-gated, not guest-apply):
      call `browser_open_session`, then `jobs_apply_start` with its URL. Verify the
      `apply_driver_fn` returns `needs_login: true` and `_ensure_ats_login` is invoked.
- [ ] Confirm that `_ensure_ats_login` creates an ATS account using the jobs email +
      a Felix-generated password, and the `ats_accounts` SQLite row is written with
      `status="created"`.
- [ ] Confirm that the verification email arrives in the jobs inbox, `_read_verify_link_fn`
      extracts the link, and `_click_verify_link_fn` navigates to it successfully.
- [ ] Confirm the `ats_accounts` row updates to `status="verified"` after verification.
- [ ] Confirm that the password is stored in the keyring under the per-provider provider
      name (e.g. `greenhouse`) via `CredentialStore.get_secret(profile_id, "greenhouse", "password")`.
- [ ] After verification, confirm control returns to `jobs_apply_start`, the driver
      re-navigates to the ATS URL now logged in, and the application proceeds to
      `ready_to_submit` (or `awaiting-input` for unknown fields).
- [ ] Run `jobs_apply_start` a second time on the same URL: confirm `_ensure_ats_login`
      detects the existing `verified` account and skips account-creation entirely.
- [ ] Confirm that a Lever login-gated posting follows the same flow end-to-end.

## B2 #509 — Greenhouse/Lever postings-API providers

- [ ] Paste a real Greenhouse company board URL (e.g. `https://boards.greenhouse.io/<slug>`) in
      the Job Search panel "Add board" field. Confirm the board appears with provider=greenhouse
      and config={"slug": "<slug>"} in the panel (or via SQLite).
- [ ] Click "Check for new jobs" with the Greenhouse board enabled. Confirm postings appear in
      the panel with working apply URLs (absolute_url values from the Greenhouse API). Confirm
      no browser window opens (pure HTTP GET, no OpenClaw navigate).
- [ ] Paste a real Lever company board URL (e.g. `https://jobs.lever.co/<slug>`) and repeat:
      fetch, confirm postings appear with hostedUrl apply URLs.
- [ ] Verify that re-fetching the same Greenhouse/Lever board does not duplicate postings
      (URL-dedup: same count on second fetch as on first).
- [ ] Paste a Greenhouse board with 50+ live postings; confirm at most 50 are stored per fetch.
