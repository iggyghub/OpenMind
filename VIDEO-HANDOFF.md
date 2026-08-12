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

8. **Review campaign status (2026-08-12):**
   1. DONE -- **S19 `video_query`** (filter/sort clusters + cluster drill-in) --
      PR #678 merged. List mode filters verdict/max_people/min_confidence/
      min_members/uncommitted, sorts members|confidence, each cluster carries a
      `representative` video; drill-in via `cluster_id`.
   2. DONE -- **S20 Memory categories** -- PR #680 merged. `remember(fact,
      category="")` stores category in Chroma metadata; `video_commit` tags facts
      `category="money-making idea"`; Memory page groups by category under
      collapsible headers ("General" for uncategorised). The pre-existing 18
      committed memories were **re-tagged** in Chroma (profile_4) to carry the
      category.
   3. DONE -- **22 clusters committed to Memory** under "money-making idea": the
      original 18 government + #912 Information Reselling, #997 Business Program
      Curation, #899 Wealth Consulting, #903 AI Automation. Curated to the user's
      rules: solo, minimal/no direct people contact (none preferred), no upfront
      spend, legit. `video_clusters.memory_id` set on all 22.
   4. Review board artifact (all 37 kept clusters, passive/active + contact +
      setup + Felix profit estimates, sorted passive-first/least-contact):
      https://claude.ai/code/artifact/8cdd9c58-2d77-4ef2-ac62-585d5771c806
   5. KEY FINDING: the government debt-relief/assistance family monetizes mainly
      as client consulting ($100-400/hr = contact). Genuinely passive winners are
      the curation/content/digital-product ideas (#919, #912, #997, #915, #909).
   6. Corpus: only **541 of 2427** videos watched (22%); 1882 enumerated-unwatched,
      batch paused. One channel only (Lesko shorts). Resuming would add clusters.
   7. TODO/candidates: scrub the ~4-6 junk rows (pre-S18 bug + "Missing Input"
      cluster #1098); a **`video_uncommit`** tool (memory `forget()` already
      exists) to prune the 4 contact-heavy committed gov ideas (#914 coaching,
      #957 consulting, #972 referral, #932 contract-delivery) that fail the later
      "no contact" rule.

9. **Fresh-session note:** the prior session's `scratchpad/*.py` helpers won't
   exist. Check status by querying `cerebral/data/openmind.db` (tables `videos`,
   `video_clusters`, `video_ideas`) or sending
   `{"type":"plugins:test_call","data":{"tool_name":"video_batch_status","args":{}}}`
   to `ws://localhost:7766`.
