# VIDEO-EDIT.md -- self_dev campaign driver: video projects (ADR-0041)

Design: `docs/adr/0041-video-projects.md` + CONTEXT.md (**Video project**, **Reel**,
**Quick trim**, **Vision trim**). Grilled 2026-10-23. Job order from the user: game reels,
then shorts, then general edits. This driver covers reels only; shorts get their own short
grill first (9:16, captions from the transcript, silence cutting) and reuse everything here.

Each slice is one GitHub issue whose body is the complete instruction self_dev receives:
exact files, exact contents or SEARCH/REPLACE text, acceptance checks. Built by Felix through
`self_dev_campaign`; Claude Code writes the specs and verifies each landed PR.

## Status: ready

## Next slice -- start here

- **Active:** V1 -- #1521
- **Model:** self_dev-default

## Queue

Order matters: each slice needs the previous one merged. Only the Active slice has an issue;
the next one is specced after the previous lands (its exact SEARCH text depends on it).

- [ ] V1 -- #1521 -- Shotcut setup: `scripts/setup-shotcut.ps1` (winget, machine-wide) + `plugins/video_project.py` with `find_shotcut`/`find_melt` + `video_status` + gate test + Help topic
- [ ] V2 -- edit list -> `.mlt` serialiser (trusted code, never model-written XML) + filing in the Document library under `Videos/<YYYY>/<MM-DD>/` by capture date + fingerprint on write
- [ ] V3 -- render with `melt` in the background (progress, out-of-date marking on `.mlt` change, render on request, export renders first)
- [ ] V4 -- Quick trim: loudest audio stretch per clip, join in capture order, one-call "reel from these clips"
- [ ] V5 -- pointing + chat card: resolve "today's clips" / a folder to paths, card with Quick trim / Vision trim / Something else + comment box (skipped when the request already says what to do)
- [ ] V6 -- open in Shotcut + the fingerprint rule (edit in place while it matches, fork a new project once the user changed it) + snapshot-before-write
- [ ] V7 -- Vision trim: escalation for clips with no clear audio peak (capped per reel) and the explicit all-clips mode

## Landed PRs
