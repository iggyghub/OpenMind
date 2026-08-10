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
