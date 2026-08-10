# ADR-0017: Video watching — Felix watches videos to understand and learn from them

**Date:** 2026-08-10
**Status:** Accepted (grill session)

## Context

Felix can read pages, mail, and documents, but a video is opaque to it. The
motivating want, in the user's words: a "video watcher/transcript so it can
learn/copy things." The first concrete use is a single TikTok channel (~200
short videos) that pitches money-making ideas — Felix should watch each one,
work out the idea, judge whether the idea is actually valid, and let the user
commit the good ones into Felix's memory.

A rough draft framed this as six throwaway scripts against one channel. That is
the wrong altitude. The reusable thing is not "a TikTok money-idea analyzer" —
it is **the act of watching a video and understanding it**. The money-idea
channel is the *first caller*, not the purpose. Build the primitive; let the
first channel ride on it.

Two facts about the box shape every decision. The target is a GTX 1080 (8 GB)
desktop: one model resident at a time, no real GPU parallelism. And `yt-dlp`
downloads must stay strictly sequential with sleeps — parallel requests get the
IP blocked (this is a hard external constraint, not a tuning knob).

## Decision

1. **A video-watching primitive, not a script.** The unit is `URL → {transcript,
   on-screen text, visual summary, source metadata}` for one video, resumable,
   stored in the shared `openmind.db`. Logic lives in `cerebral/video/`
   (`pipeline.py`, `store.py`, `channel.py`), mirroring how `insights/` and
   `memory/` are modules with thin plugin wrappers. `plugins/video.py` is the MCP
   surface (`video_ingest`, `video_get`, `video_list`, batch controls) plus the
   panel spec. The money-idea extraction and validity verdict are **concrete code
   for this first channel** — not a job-config framework. When a second kind of
   channel appears, factor out what is shared *then* (YAGNI).

2. **Three signal layers, lazily gated.** (a) Audio transcript always —
   `faster-whisper small/int8/CPU/vad_filter=True`, covers most videos. (b)
   On-screen text via OCR over scene-change keyframes. (c) A short visual
   description of those keyframes via a vision model. Layers (b)/(c) are the
   expensive ones and run only on **escalation**, never by default.

3. **Two escalation triggers, both cheap to detect.** The visual layers fire when
   the audio admits it is not enough: (i) **thin transcript** — empty, very short,
   or high whisper no-speech probability (music-only, silent demo); (ii)
   **deictic reference** — the transcript points at the screen ("look at this
   model", "as you can see", "shown here") via a curated cue-phrase scan, no LLM.
   Plus a `visual=True` force flag, and a **per-batch escalation cap** so a
   200-video sweep cannot silently balloon into 200 vision passes.

4. **Everything is resumable at the row.** Each video is a row with a `stage`
   status (`enumerated → downloaded → transcribed → [escalated] → extracted →
   verified`). The runner processes rows below the target stage and **commits per
   video**. Killed at video 140, it resumes at 141 — never re-downloads, never
   re-transcribes. No stage holds 200 rows in memory.

5. **Batch as one DB-backed background task (in-app start/stop).** A single
   Cerebral asyncio task loops the primitive over a channel's rows, heavy work in
   `to_thread`. Because all state is in `openmind.db`, the task holds nothing:
   `video_batch_start(url)` enumerates then spawns it, `video_batch_stop()` sets a
   flag checked between videos, `video_batch_status()` is a `COUNT(*)` per stage
   for the tab's progress bar and ETA. Felix has no generic job queue
   (`action_queue` is the consent queue, `session_worker` is the computer-use
   actuator), so this is a small new task — small *because* the DB carries the
   state.

6. **"Adaptive" means measured, not detected.** Downloads are forced-sequential
   and 8 GB fits one model, so there is no concurrency to size. What adapts:
   per-video wall time is *measured* after the first few videos to drive the tab's
   ETA (never read VRAM to guess a batch — drivers and thermals make specs lie);
   and model choice **routes by availability** through Felix's existing model
   priority — Budd-VL for frame description when up, local qwen fallback when Budd
   504s, defer when nothing is up. The pipeline degrades; it does not crash.

7. **Every watched video ends with a verdict; verification is paid once per
   idea.** Ideas are **clustered incrementally** — extraction assigns each video's
   idea to an existing cluster label or a new one *in the same cheap LLM call*, no
   separate batch pass, no vector math (ChromaDB is available if labels ever
   sprawl). The **validity verdict lives on the cluster**: the first video in a
   cluster runs the strong-model + web-search validity pass once; every later
   video in that cluster **inherits** the verdict instantly. So "any video gets a
   verdict after it's watched" holds literally, but "dropshipping" is verified
   once, not fourteen times. A verdict is `legit / dubious / scam / unverifiable`
   with confidence and 1–3 evidence links. Per-video re-verify is a manual button.

8. **Learn/copy is gated promotion, never an automatic dump.** The video store
   holds all ~200 videos' understanding and verdicts, browsable. It does **not**
   touch Memory on its own — dumping 200 scraped claims would drown the memory
   that matters, and Felix's posture is already "propose, user decides." A
   **commit** action (per cluster/idea, from the tab) writes the verified idea
   into **Memory** as a durable fact with its verdict attached. Felix ends up
   *knowing* the handful you chose, each carrying its validity judgment; the rest
   stay in the store, un-committed.

9. **The tab is a declarative panel, no new vocabulary.** Per ADR-0012, `video.py`
   returns a `{list, table, form, detail, text}` widget tree: `form`s to ingest a
   URL / start a channel, a `detail` for batch status + stop + ETA, a `table` of
   clusters (`label · count · verdict · confidence`), drill-in `detail`/`text` for
   per-video transcript / on-screen text / visual summary / evidence, and a commit
   button bound via `tool`/`tool_args` (the pattern the doc text widget uses for
   `doc_write`). Its own nav entry.

10. **JSON-forced, validate-and-retry on the LLM stages.** Extraction and
    verification force JSON-only output and validate/retry on parse failure. A
    malformed response never silently writes a null row (carried verbatim from the
    draft spec — the one rule there worth keeping literally).

## Consequences

- Felix gains a general comprehension capability; the money-idea channel is one
  caller, and "summarize these cooking shorts" or "what teaching method does this
  channel use" is new *calling code* over the same primitive, not a new pipeline.
- Two new binaries are required regardless of any later choice: `yt-dlp` and
  `ffmpeg` (whisper needs it to decode audio). `faster-whisper`, `anthropic`, and
  ChromaDB are already present.
- Verdicts are per-idea, not per-video-nuance: a video with a genuinely novel
  twist on a clustered idea inherits the cluster verdict unless re-verified by
  hand. Accepted trade for a 200-video skim.
- The `{list, table, form, detail, text}` vocabulary is the ceiling (ADR-0012); if
  the video detail ever needs something it cannot express, the pressure lands on
  the shared vocabulary, not per-plugin code.
- Verification is the only expensive stage and the only one with real API cost;
  the cluster-cache is what keeps a 200-video run affordable.

## Amendment — S9 #655: no Anthropic key; Budd + OpenClaw web search

Decision 7 named "a strong model + web search"; in practice the box runs with no
Anthropic API key. Two facts made that clean: `_router.complete()` is text-only
and local-first (cloud is an off-by-default fallback, so the key was never
required), and the "search the web" instruction was never a real tool call.
S9 makes grounding real without a key: `_video_verify` calls Felix's own
`web_search` (OpenClaw gateway, keyless) and feeds the results into the model.
Extraction + verdict route on a dedicated `task_type="video"` so Budd can be
pinned for video without disturbing the jobs pipeline's `quality` route; a search
outage degrades the verdict to knowledge-only rather than failing.
