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
