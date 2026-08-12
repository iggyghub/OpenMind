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

9. **NEXT SESSION -- start here (grill-first, then build S22 -> S23 -> new batch):**

   State as of 2026-08-12 handoff:
   1. Lesko queue CLEARED -- the 1882 enumerated rows were deleted (user
      authorised). DB now: 541 watched videos, 40 clusters, 0 pending. 22
      clusters committed to Memory under category "money-making idea".
   2. S21 `video_batch_clear` merged? CHECK -- PR #682 was OPEN at handoff. If not
      merged, merge it; the "Clear queue" button + tool go live on next restart.
   3. Restarts this session already happened; a fresh restart is needed for S21 +
      any S22/S23 code. Restart recipe is in section 5 above (PATH: Tesseract +
      winget ffmpeg bin; kill electron+python; scripts/launch-felix.ps1).

   Build order:
   1. **S22 -- channel-scoped clusters + keyword.** Add a `collection` TEXT column
      to `video_clusters`; change the UNIQUE(label) to UNIQUE(collection,label) and
      key `get_or_create_cluster` on (collection,label). The batch runner passes the
      active channel's collection keyword (a short user-supplied tag, default from
      the channel). `video_query` + `list_clusters` + the panel gain a `collection`
      filter. Backfill the existing 40 clusters as collection='lesko'. This is what
      lets a NEW channel keep its own cluster set AND lets the user jump back to a
      prior set by keyword (user's exact ask). Seam rule + a test as always.
   2. **S23 -- review board inside Felix (the artifact, but native).** The verdict
      step (cerebral/video/verdict.py + main._video_verify) should also emit, per
      cluster: `contact_level` (none/minimal/some/high), `mode` (passive/active),
      `setup` (easy/medium/hard), `profit_estimate` (short string). Store as columns
      on `video_clusters`. The Videos-tab `panel_spec` renders them and sorts
      passive-first then least-contact (the user's ranking). Backfill the current 40
      -- the classifications already exist as a Claude artifact review board:
      https://claude.ai/code/artifact/8cdd9c58-2d77-4ef2-ac62-585d5771c806 (its
      `D=[...]` JS array has fit/contact/mode/setup/profit per cluster id -- reuse it
      for the backfill). NOTE: contact/mode/setup/profit were HAND-computed via Felix
      drill-ins this session; S23 makes the pipeline generate them going forward.
   3. **Then start the new-channel batch** with its collection keyword, once S22 is
      live so clusters stay separate. USER STILL OWES THE CHANNEL URL -- they said
      they want "a channel that works with the harness + ways to improve it"; harness
      sweet spot is YouTube spoken-word (regular or shorts), NOT TikTok (S12 open).
      Confirm the URL + a short keyword before `video_batch_start(url, channel=<kw>)`.

   Chat-log incident (2026-08-12, RESOLVED): the Main-window chat was flooded
   with `-> video_batch_status / <- result` -- ~12,660 turns. Root cause was NOT
   Felix: a `/loop` in ANOTHER Claude session (`a0d6d71e`) ran `vdrive.py status`
   (a `plugins:test_call video_batch_status` poller) every ~7s for ~13h. Fixes:
   S24 (PR #685, merged) makes `plugins:test_call` a true debug hook -- it no
   longer records transcript turns or emits the transient tool_result broadcast
   (`record=False` in `_dispatch_tray_call_tool`); and the transcript now only
   sticks to the bottom on a new turn if you're already near it (`isNearBottom`).
   The 12,660 junk turns were purged; `vdrive.py` was neutralised to a no-op. USER
   STILL NEEDS to stop the `/loop` in session a0d6d71e (it keeps firing a no-op).
   Also filed: Felix wrongly told the user `self_dev` can't touch the frontend --
   but `self_dev` clones the whole repo incl. `tray/windows/main.html`, so UI fixes
   (like this auto-scroll one) ARE in scope; the model just doesn't realise it.

   Harness-improvement backlog surfaced this session (candidate slices; user is
   interested in these):
   1. `video_uncommit` -- prune a committed cluster from Memory (memory `forget()`
      already exists); needed to remove the 4 contact-heavy gov ideas (#914/#957/
      #972/#932) that fail the later "no contact" rule.
   2. Harden `_run_batch` -- catch+continue on ANY per-video exception so one bad
      Budd/504 can't end the run (section 7d).
   3. YouTube 403 rate-limit after ~500 sequential downloads (section 7a) -- longer/
      jittered sleeps or cookie/session rotation so long channels finish.
   4. Scrub junk clusters: "Missing Input" #1098 (30 vids, no extractable idea) +
      commentary #916/#986; a failed-extraction bucket shouldn't become a cluster.
   5. S12 (#663) still open -- real TikTok extraction via the browser harness.

10. **Fresh-session note:** the prior session's `scratchpad/*.py` helpers won't
   exist. Check status by querying `cerebral/data/openmind.db` (tables `videos`,
   `video_clusters`, `video_ideas`) or sending
   `{"type":"plugins:test_call","data":{"tool_name":"video_batch_status","args":{}}}`
   to `ws://localhost:7766`.
