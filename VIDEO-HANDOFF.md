# VIDEO-HANDOFF.md -- pick up the video-watching work here

Operational handoff for the ADR-0017 video-watching subsystem + the live
government-money analysis run. Deep design context is in auto-memory
`project-video-watching-campaign`; campaign driver is `VIDEO.md`.

1. **Live state:** A batch is running in Cerebral (`ws://localhost:7766`) over
   Matthew Lesko's government free-money shorts --
   `https://www.youtube.com/channel/UCwKJZfa7sWV_qKxQnLBUpjA/shorts` (~2427 videos).
   ~512+ verified, ~40 clusters of real government-handout ideas (grants,
   assistance claims, tax/medical/utility debt relief, free vocational programs).
   GPU whisper (cuda/int8 on the GTX 1080), resumable.

2. **Shipped & merged:** ADR-0017 slices S1-S11 + S13-S18; full test suite green.
   Only **S12** (make TikTok screen-capture actually extract) is open/optional.

3. **How it works:** each video -> whole transcript -> ONE idea -> videos with the
   same idea share a **cluster** -> one Budd verdict per cluster
   (legit/dubious/scam, no API key) -> gated **commit to Memory**. One idea/video.

4. **Operate it (Library -> Videos tab):** live auto-refreshes; **Stop batch** /
   **Resume batch** buttons; global hotkey **Ctrl+Alt+P** toggles pause/resume.
   Commit the legit clusters into Memory from there.

5. **After any Cerebral restart** (loses in-memory batch state): re-pin the `video`
   task -> **Budd** in the model-priority panel, then click **Resume batch** (it
   recovers the channel from the DB). To restart Felix: kill `electron`
   (OpenMind\tray) + `python` (cerebral.main), then run `scripts/launch-felix.ps1`
   -- but first prepend to PATH: `C:\Program Files\Tesseract-OCR` and the winget
   ffmpeg `bin`. (CUDA libs auto-load at runtime via `_setup_cuda_dll_path`.)

6. **Deps installed:** PATH -> yt-dlp, ffmpeg, tesseract. pip -> soundcard,
   soundfile, nvidia-cublas-cu12, nvidia-cudnn-cu12, nvidia-cuda-runtime-cu12.

7. **Known issues:** (a) YouTube 403-rate-limits after ~500 sequential downloads
   -> those mark `failed` (S18 stopped them from opening browser tabs).
   (b) ~6 junk rows from the pre-S18 capture bug -- safe to delete.
   (c) verdicts are knowledge-only until the OpenClaw gateway scope is approved
   (`openclaw devices approve --latest`).
   (d) the batch task has stopped once or twice on a transient error (no clear
   cause in the log; possibly a Budd 504 propagating past `_run_batch`'s per-video
   try). If `video_batch_status` shows `running:false` with rows still
   `enumerated`, just **Resume** (tab button / Ctrl+Alt+P / `video_batch_resume`)
   -- it continues from the DB. Candidate hardening: make `_run_batch` catch +
   continue on any per-video exception so one bad call can't end the run.

8. **Next wants (from the user):**
   1. DONE -- **`video_query`** tool (filter/sort clusters by talking to Felix --
      "show me the legit ones a solo person can do") + **cluster drill-in**
      (cluster_id -> member videos). Shipped in PR #678 (#677, S19); each listed
      cluster carries a `representative` video to watch. Awaiting merge.
   2. DONE -- committed the 18 government-money clusters to Felix's Memory
      (all legit, all solo/p=1, none require buying-first). Filters applied per
      the user: government-only, drop 2-person and buy-first. `video_clusters`
      now has 18 rows with a `memory_id`.
   3. TODO -- scrub the ~6 junk rows from the pre-S18 capture bug.
   4. Non-government legit clusters (17) were left uncommitted by design.

9. **Fresh-session note:** the prior session's `scratchpad/*.py` helpers won't
   exist. Check status by querying `cerebral/data/openmind.db` (tables `videos`,
   `video_clusters`, `video_ideas`) or sending
   `{"type":"plugins:test_call","data":{"tool_name":"video_batch_status","args":{}}}`
   to `ws://localhost:7766`.
