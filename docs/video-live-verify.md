# Video watching — live verification checklist

Behaviour only verifiable against real services or real binaries.
Complete these manually; do NOT perform them in an autonomous loop session.

## S1 — video primitive (store + pipeline + video_ingest/video_get)

- [ ] **yt-dlp present.** Run `yt-dlp --version` and confirm it is installed on this box.
- [ ] **ffmpeg present.** Run `ffmpeg -version` and confirm it is installed (yt-dlp needs it to extract audio).
- [ ] **faster-whisper present.** Run `python -c "import faster_whisper; print('ok')"` and confirm.
- [ ] **video_ingest live round-trip.** With Felix running (`python -m cerebral.main`), call
      `video_ingest(url="<a short public YouTube or TikTok URL>")` and verify:
      - A row appears in `openmind.db` `videos` table at `stage=transcribed`.
      - The transcript field contains recognisable speech from the video.
      - `duration` is non-zero.
- [ ] **Idempotency live check.** Call `video_ingest` on the same URL a second time and confirm
      the response contains `"skipped": true` and no second yt-dlp download occurs
      (observe absence of network activity or check the row's `updated_at` did not change).
- [ ] **video_get live check.** Using the id returned by the ingest above, call
      `video_get(id=<id>)` and verify the full transcript and metadata are returned.

## S2 -- visual escalation (OCR + vision on keyframes)

- [ ] **pytesseract + Pillow present.** Run `python -c "import pytesseract, PIL; print('ok')"`.
      If missing: `pip install pytesseract Pillow` and ensure Tesseract binary is on PATH.
- [ ] **Thin-transcript escalation.** Call `video_ingest` on a silent / music-only video (or one
      with very little speech). Confirm the returned row has `escalated=true`, a non-empty
      `ocr_text` (if on-screen text exists), and a `visual_summary` from the vision model.
- [ ] **Deictic-cue escalation.** Call `video_ingest` on a video whose transcript contains
      "as you can see" or "look at this". Confirm `escalated=true` in the result.
- [ ] **Normal-audio no escalation.** Call `video_ingest` on a rich-audio video (clear speech,
      no deictic phrases). Confirm `escalated=false` and `ocr_text` / `visual_summary` are empty.
- [ ] **Force-visual flag.** Call `video_ingest(url=<url>, visual=true)` on any video.
      Confirm `escalated=true` regardless of transcript content.
- [ ] **Keyframe count cap.** After a live ingest with escalation, check logs for ffmpeg output --
      confirm at most 12 frames were extracted (scene-change select=gt(scene,0.4), -frames:v 12).
- [ ] **Frames discarded.** Verify no `frame*.jpg` files remain under any temp directory after
      `video_ingest` completes (tmp dir is cleaned up automatically).
- [ ] **Vision model routing.** With Budd-VL up: confirm `visual_summary` is non-empty.
      With Budd-VL down (504): confirm `visual_summary` is empty and a warning is logged
      (no crash). Full model-priority routing (local fallback) is a post-S2 deepening.
- [ ] **Per-batch cap.** Import `EscalationBudget(cap=2)` from `cerebral.video.escalation`
      and pass it to `pipeline.run` for a sequence of 5 thin-transcript videos.
      Confirm only 2 rows end up with `escalated=true`.

## S3 -- channel batch runner (enumerate + resumable task)

- [ ] **yt-dlp flat-playlist enumerate.** Run `yt-dlp --flat-playlist --dump-json --no-warnings <channel_url>`
      against the target TikTok channel. Confirm it returns one JSON object per line (not truncated at 30)
      and that each line has a `webpage_url` or `url` field for the individual video.
- [ ] **video_batch_start live round-trip.** With Felix running, call
      `video_batch_start(url="<channel_url>", escalation_cap=3, sleep_secs=5)`.
      Confirm: response contains `"status": "started"` and `"enumerated": <N>` matching
      the number of videos in the channel; rows appear in `openmind.db` `videos` table at
      `stage=enumerated`.
- [ ] **Sequential processing + sleep.** After `video_batch_start`, observe logs: each video
      is processed one at a time with a sleep between them. Confirm no two yt-dlp downloads
      overlap (check process list during a run).
- [ ] **video_batch_status ETA.** After at least one video completes, call `video_batch_status()`.
      Confirm `stage_counts` reflects actual DB state and `eta_seconds` is a plausible number
      based on how long the first video took.
- [ ] **Kill mid-run and resume.** Start a batch, wait for 2-3 videos to complete, then
      restart Felix (`python -m cerebral.main`). Call `video_batch_start` with the same channel
      URL again. Confirm: already-transcribed rows are NOT re-downloaded (check their
      `updated_at` did not change); only the remaining `enumerated` rows are processed.
- [ ] **video_batch_stop halts between videos.** Start a batch, then call `video_batch_stop()`.
      Confirm: the current video finishes but no new downloads start; `video_batch_status()`
      shows `running: false`.
- [ ] **Escalation cap per batch.** Start a batch against a channel with several thin-transcript
      (silent/music) videos and `escalation_cap=2`. Confirm only 2 rows end up with
      `escalated=true` regardless of how many would qualify.

## S5 -- idea extraction + incremental clustering

- [ ] **Extraction seam wired.** After `python -m cerebral.main` starts, call `video_batch_start`
      on a 2-3 video channel. Confirm each processed row ends at `stage=extracted` in the
      `videos` table (not `transcribed`).
- [ ] **video_ideas populated.** After the batch, query `SELECT * FROM video_ideas` in
      `openmind.db`. Confirm one row per processed video with non-empty `idea_text` and a
      valid `cluster_id`.
- [ ] **Incremental clustering: repeated ideas share a cluster.** Process a channel where
      several videos pitch the same idea (e.g. dropshipping). Confirm multiple `video_ideas`
      rows share the same `cluster_id` and the matching `video_clusters.member_count` equals
      the number of videos in that cluster.
- [ ] **New cluster created for novel idea.** Confirm that a video with a genuinely different
      idea produces a new row in `video_clusters` with `member_count=1`.
- [ ] **Existing labels passed to LLM.** Enable debug logging and observe the extraction
      prompt: existing cluster labels must appear in the prompt sent to the LLM.
- [ ] **JSON-forced retry in practice.** Temporarily replace the extraction seam with a stub
      that returns bad JSON on the first call, valid JSON on the second. Confirm the idea is
      written and `stage=extracted` (retry succeeded).
- [ ] **Extraction failure leaves transcribed stage.** With no extraction seam wired (or a
      seam that always raises), run the batch. Confirm rows remain at `stage=transcribed`
      and the batch does not crash or leave orphaned rows.

## S6 -- validity verdict per cluster (strong model + web search)

- [ ] **ANTHROPIC_API_KEY present.** Confirm `ANTHROPIC_API_KEY` is set in the environment
      (`python -c "import os; print(bool(os.getenv('ANTHROPIC_API_KEY')))"` -> `True`).
- [ ] **Verdict seam wired.** After `python -m cerebral.main` starts, run `video_batch_start`
      on a 2-3 video channel. Confirm each processed row ends at `stage=verified` in the
      `videos` table (not `extracted`).
- [ ] **Cluster verdict populated.** After the batch, query
      `SELECT label, verdict, confidence, evidence_links FROM video_clusters` in `openmind.db`.
      Confirm each cluster has a non-null `verdict` (one of `legit/dubious/scam/unverifiable`),
      a `confidence` value between 0.0 and 1.0, and `evidence_links` containing 1-3 entries.
- [ ] **Verify once, inherit.** Process a channel where multiple videos share the same cluster
      label. Confirm `video_clusters.verdict` is non-null after the first video and that no
      second web-search call is made for later videos in the same cluster (check API call logs
      or add a counter to `_video_verify` temporarily).
- [ ] **JSON-forced retry in practice.** Enable debug logging and observe the verdict prompt.
      Confirm the model is called with the full prompt including cluster label and idea text,
      and the response is parsed as JSON. If the first response is malformed, confirm a retry
      occurs and the final verdict is written correctly.
- [ ] **Verdict failure leaves extracted stage.** Temporarily break the verify seam (raise an
      exception). Run the batch. Confirm rows stay at `stage=extracted`, no null verdict is
      written to `video_clusters`, and the batch does not crash.
- [ ] **Panel shows verdict.** Open the Videos panel in the Felix UI. Confirm the cluster
      table shows the verdict string and formatted confidence (e.g. "85%") instead of
      "pending" / "—". Confirm the per-cluster drill-in shows evidence links.

## S7 -- commit verified idea to Memory (video_commit)

- [ ] **Profile loaded.** Confirm Felix has an active profile loaded (the commit tool writes to
      that profile's ChromaDB). If no profile is set, `video_commit` will fail with
      "No active profile".
- [ ] **video_commit live round-trip.** After a batch run where at least one cluster has a
      `verdict` set, open the Videos panel and click "Commit to Memory" on a verified cluster.
      Confirm the response contains `"committed": true` and a non-null `memory_id` UUID.
- [ ] **Memory written.** After a successful commit, open the Memory panel (or run
      `memory_recall(query="money-making idea")`) and confirm the cluster's idea + verdict
      appears as a recalled fact.
- [ ] **Panel reflects committed state.** After committing, refresh the Videos panel.
      Confirm the "Commit to Memory" button is replaced by "Committed — In Memory"
      (the `detail` widget with `value: "In Memory"`).
- [ ] **Idempotency.** Call `video_commit(cluster_id=<id>)` a second time on the same
      cluster. Confirm the response contains `"already_committed": true` and the same
      `memory_id`. Confirm no second fact appears in Memory (no duplicate).
- [ ] **Un-committed cluster stays out of Memory.** After a batch run with multiple
      clusters, commit only one. Confirm only that cluster's idea appears in Memory recall;
      the others are absent.

## S8 #653 -- verdict de-bias + people-count
- [ ] Run a real verify on a @filthy.profit "unethical" item that is actually a
      government grant/benefit: verdict must reflect the METHOD (legit) and NOT
      inherit "scam/unethical" from the video's framing.
- [ ] Confirm `people_required` is populated on real extractions and that a
      genuinely two-person method lands in the "Requires two people" group.

## S9 #655 -- no API key: Budd + OpenClaw web_search
- [ ] Confirm ANTHROPIC_API_KEY is unset and a real verdict still runs (extraction
      + verdict route on task_type="video" -> Budd/local; pin "video" -> Budd in the
      model-priority panel).
- [ ] Confirm the verdict is grounded: web_search (OpenClaw) returns results that
      appear to inform the verdict + evidence URLs.
- [ ] Kill the OpenClaw search endpoint and confirm the verdict still completes
      (knowledge-only fallback), never crashes.

## S10 #658 -- screen-watch capture (browser + WASAPI loopback)
- [ ] Install the capture deps once: `pip install soundcard soundfile` (PIL/numpy already present).
- [ ] Ingest a TikTok URL that yt-dlp cannot download with capture forced
      (`video_ingest(url, capture=true)`): Felix opens the browser, plays it, and
      produces a transcript from the loopback audio.
- [ ] Confirm the automatic path: a normal `video_batch_start` on a TikTok channel
      now falls back to capture per-video when yt-dlp download raises (no more mass
      'failed').
- [ ] Confirm WASAPI loopback records the video's audio (not the mic) and that
      other system sounds are quiet during capture.
- [ ] Confirm sampled frames feed escalation (OCR/vision) for a thin/deictic clip.

## S14 #667 -- 2x audio speed-up for faster transcription
- [ ] Confirm a batch transcribes ~2x faster (video_transcribe_speed default 2.0) with
      idea extraction still landing correct clusters. Dial back via the
      video_transcribe_speed setting if accuracy drops on fast talkers.
- [ ] (Bigger win, separate) GPU whisper: install CUDA 12 runtime libs
      (cublas64_12.dll + cuDNN) so device="cuda" works on the 1080 (ctranslate2 sees it).

## S17 #673 -- GPU whisper (cuda/int8 on the 1080)
- [ ] Deps installed: pip nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12.
- [ ] After restart, cerebral.err.log shows "whisper model on GPU (cuda/int8)".
- [ ] nvidia-smi shows python using the GPU during a batch; per-video time drops sharply.
- [ ] On a box without the CUDA libs, it logs "GPU whisper unavailable ... using CPU" and still works.
