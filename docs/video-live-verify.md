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
