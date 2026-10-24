# VIDEO-EDIT.md -- self_dev campaign driver: video projects (ADR-0041)

Design: `docs/adr/0041-video-projects.md` + CONTEXT.md (**Video project**, **Reel**,
**Quick trim**, **Vision trim**). Grilled 2026-10-23. Job order from the user: game reels,
then shorts, then general edits. This driver covers reels only; shorts get their own short
grill first (9:16, captions from the transcript, silence cutting) and reuse everything here.

Each slice is one GitHub issue whose body is the complete instruction self_dev receives:
exact files, exact contents or SEARCH/REPLACE text, acceptance checks. Built by Felix through
`self_dev_campaign`; Claude Code writes the specs and verifies each landed PR.

## Status: blocked -- V5 spec not written yet

## Next slice -- start here

- **Active:** V5 -- #0
- **Model:** self_dev-default

## Queue

Order matters: each slice needs the previous one merged. Only the Active slice has an issue;
the next one is specced after the previous lands (its exact SEARCH text depends on it).

- [x] V1 -- #1521 -- Shotcut setup: `scripts/setup-shotcut.ps1` (winget, machine-wide) + `plugins/video_project.py` with `find_shotcut`/`find_melt` + `video_status` + gate test + Help topic
- [x] V2 -- #1527 -- edit list -> `.mlt` serialiser (trusted code, never model-written XML) + filing in the Document library under `Videos/<YYYY>/<MM-DD>/` by capture date + fingerprint on write
- [x] V3 -- #1531 -- render with `melt` in the background (progress, out-of-date marking on `.mlt` change, render on request, export renders first)
- [x] V4 -- #1535 -- Quick trim: loudest audio stretch per clip, join in capture order, one-call "reel from these clips"
- [ ] V5 -- pointing + chat card: resolve "today's clips" / a folder to paths, card with Quick trim / Vision trim / Something else + comment box (skipped when the request already says what to do)
- [ ] V6 -- open in Shotcut + the fingerprint rule (edit in place while it matches, fork a new project once the user changed it) + snapshot-before-write
- [ ] V7 -- Vision trim: escalation for clips with no clear audio peak (capped per reel) and the explicit all-clips mode

## Landed PRs

- PR #1523 -- V1 (built by a Claude Haiku agent from the #1521 spec while Budd was down; diff verified byte-identical to the dry-run spec, full pytest 6,278 passed)
- PR #1524 -- V1 follow-up: Felix installed Shotcut itself via `pkg_install` at winget's per-user scope; detection now finds `%LOCALAPPDATA%\Programs\Shotcut`. Live-verified: `video_status` available, melt 7.41.0
- PR #1529 -- V2 (built by a Claude Haiku agent from the #1527 spec while Budd was down; all 6 files verified byte-identical to the spec applied to master; spec dry-run full pytest 6,293 passed / 0 failed; generated .mlt renders in melt and opens in Shotcut as a timeline)
- PR #1533 -- V3 (built by a Claude Haiku agent from the #1531 spec; all 4 files verified byte-identical to the spec applied to master; spec dry-run full pytest 6,301 passed / 0 failed; real melt render 4s+5s -> 9.003s with live progress, out_of_date flips on .mlt edit)
- PR #1537 -- V4 (built by a Claude Haiku agent from the #1535 spec while Budd was down; all 3 files verified byte-identical to the spec applied to master; spec dry-run full pytest 6,310 passed / 0 failed; real ffmpeg on generated clips: burst at 14-17s trimmed to 11-20s, flat + silent clips kept whole and flagged, melt render 39.02s)
