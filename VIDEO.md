# VIDEO.md -- Video-watching capability campaign driver

Design: `docs/adr/0017-video-watching.md` + CONTEXT.md ("Watching", "Escalation",
"Verdict", "Commit"). Grilled 2026-08-10. Felix watches a video -> transcript
(+ visual on escalation) -> extracts the idea -> validity verdict -> gated commit
to Memory. First channel: a ~200-video TikTok money-ideas channel. The
money-idea extraction is concrete code for this first channel, NOT a job-config
framework (build the second kind's abstraction when it exists).

## Status: ready

## Next slice -- start here

- **Active:** S4 -- #643 -- Videos tab: declarative panel (list/table/form/detail/text)
- **Model:** sonnet

## Queue

- [x] S1 -- #639 -- video primitive: store.py + pipeline.py (yt-dlp + whisper) + video_ingest/video_get
- [x] S2 -- #640 -- visual escalation: OCR + vision on keyframes, thin-transcript/deictic triggers + cap
- [x] S3 -- #641 -- channel batch runner: flat-playlist enumerate + resumable asyncio task + start/stop/status
- [x] S5 -- #642 -- idea extraction + incremental clustering (JSON-forced, validate/retry)
- [ ] S4 -- #643 -- Videos tab: declarative panel (list/table/form/detail/text)
- [ ] S6 -- #644 -- validity verdict per cluster (strong model + web search; seam-built, live-verify tail)
- [ ] S7 -- #645 -- commit verified idea to Memory (video_commit)

Order note: S4 depends on S3; S5 depends on S1 (runs in parallel with S2/S3);
S6 depends on S5; S7 depends on S6. The queue above is a valid linear order.

Per-slice model: sonnet unless the queue entry says otherwise. When ticking a
slice, set the next entry's model on the `Model:` line above.

## Landed PRs

- PR #646 — S1 #639 — video primitive: store + pipeline + video_ingest/video_get
- PR #647 — S2 #640 -- visual escalation: OCR + vision on keyframes, thin-transcript/deictic triggers + cap
- PR #648 — S3 #641 -- channel batch runner: enumerate + resumable asyncio task + start/stop/status
- PR #649 — S5 #642 -- idea extraction + incremental clustering (JSON-forced, validate/retry)

## SAFETY

- NEVER hit the network or a real service in the loop: no real TikTok/yt-dlp
  fetch, no real web search, no real Anthropic API call. yt-dlp, ffmpeg,
  faster-whisper are installed on this box, but the loop's tests and smoke runs
  go through injectable seams (the `job_search.py` `set_*_fetch_fn` pattern) and
  are stubbed/deterministic. Behaviour only checkable against the real channel,
  a real web search, or a real vision/LLM call -> APPEND a checklist item to
  docs/video-live-verify.md instead of performing it.
- NEVER install software in a loop session (no winget, no pip installs, no
  downloads). The binaries are already present; a session that needs a new one
  writes it to docs/video-live-verify.md for the user to run.
- Seam rule (#153/#385): no `from plugins.<x> import ...` inside cerebral/ --
  wire through `_wire_plugin_seams` against `_orc.get_plugin_module`. Add a
  cerebral/tests/test_video_seam_wiring.py and keep it passing.
- One model resident at a time (8GB GPU): never load whisper + a vision model
  concurrently. Stages serialize per video.
- Downloads stay STRICTLY sequential with sleeps -- no threading/asyncio in the
  download path (IP-block constraint, ADR-0017).
- ANTHROPIC_API_KEY is NOT required by the loop (verify is seam-stubbed). The
  real web-search verify and the ~200-video live run are human live-verify steps.
- Tray renderer is no-nodeIntegration: new panel logic uses the UMD-ish dual-mode
  wrapper in tray/lib/*.js (PR #203 pattern). Panel is declarative spec only --
  no plugin-authored HTML/JS (ADR-0012).
- Operator .ps1 scripts: ASCII-only bodies, pause-on-exit + -NoPause switch
  (CLAUDE.md rules).
- Commit to master ONLY VIDEO.md. Everything else lands via a per-issue PR.
