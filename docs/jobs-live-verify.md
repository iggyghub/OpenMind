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
