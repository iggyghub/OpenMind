# ADR-0019: Route idea-extraction to Budd first, requeue per repo

**Date:** 2026-08-14
**Status:** Accepted (grill session)
**Extends:** ADR-0017 (video), ADR-0018 (github) — the shared idea-extraction spine.

## Context

Idea-extraction and validity-verdict for *both* sources run through two calls in
`cerebral/main.py`:

- `_video_extract` → `_router.complete(prompt, task_type="video")` (main.py:6222)
- `_video_verify`  → `_router.complete(prompt, task_type="video")` (main.py:6272)

`task_type="video"` is **pinned local-only** (`VIDEO_PREFERRED = ("ollama/qwen3:8b",
"ollama/qwen2.5:7b")`, router.py:131) so a long unattended batch never stalls on a
cloud 504. Local qwen3:8b is slow (~2–15 min/doc on the 8 GB box).

The user wants extraction to run on **Budd** instead — faster, better ideas — while
keeping local as a safety net because Budd is flaky (504s).

### Facts established this session (don't re-derive)

- **Budd = `custom/budd`**: kind `openai`, `https://bonsai.ai-dabs.com/v1`, model
  `hermes-agent`. Reached via `ClawBackend` → **stateless** `POST /v1/chat/completions`
  with a single-user-message payload (no history sent). One doc = one independent call.
- Budd is **already priority #0 + enabled** (the active model) for profile 4. Local
  `ollama/qwen3:8b` is enabled at #3. Cloud Claude entries disabled. Extraction runs
  local *only* because the `video` task-pin overrides the active model.
- The router already maps a flaky cloud tier's `504` → `ConnectionError`
  (`ClawBackend`), and `complete()` already has a graceful per-doc fallback path.
- ADR-0018 constraint: **doc-level granularity** — each doc is its own extraction
  unit. Extraction stays per-doc; a repo is never collapsed to one call.

## Decisions (from grill)

1. **"Session per repo" = the repo is the requeue/isolation unit, not a session
   object.** Because every doc is a stateless call, the token count resets each doc
   and never accumulates across a repo or across repos — the isolation the user
   wanted (cap the token count) is already the default. No session/thread machinery
   is built. (If the `hermes-agent` server turns out to keep server-side memory
   across calls, revisit: send a per-repo id to reset it. The chat-completions API
   we use has no session field, so nothing to reset by default.)

2. **Budd-first for everything** — github *and* video route to Budd first, local as
   fallback. (User: "as felix is setup everything should be through budd first.")

3. **On a Budd failure mid-repo, requeue the whole repo** — not per-doc fallback.
   The repo is abandoned where it stands (done docs already persisted, idempotent),
   and retried on Budd later. Rationale: a repo's ideas come from one consistent
   model, and we don't hammer a flaky Budd doc-by-doc. (User: "requeue the repo.")

4. **Local is the drain valve, not a silent per-doc fallback.** When Budd stays
   down (after N requeue attempts, or an explicit drain), finish the repo on local
   qwen so a permanently-down Budd can't wedge the collection forever.

## Design (minimal)

**Routing (router/main).** Stop pinning extraction to local-only. Route the two
extraction/verdict calls to a task that resolves to **Budd, and raises on Budd
failure** (so the batch layer can catch it — no silent fallback-to-active). Because
Budd is the active model, the simplest form is a dedicated `task_type="extraction"`
pinned to `custom/budd`; a Budd `ConnectionError` propagates out of `complete()`.

**Requeue (batch layer, where "repo" is meaningful).** `github_ingest._ingest_repo`
wraps its per-doc `extract_and_cluster`: a Budd `ConnectionError` aborts the repo
loop (done docs stay persisted), and the tool returns an explicit
`{"repo", "requeued": "budd_unavailable", "extracted": k, "remaining": n-k}` result
instead of a hard error. Re-running `github_ingest`/`github_reingest` on that repo
resumes (idempotent skip of done docs). Video's existing batch already has a
retry/resume nudge; GitHub gets the same via a small scheduled nudge (mirror
`scripts/video_week_resume.py`) that reingests incomplete repos Budd-first.

**Local drain.** The reingest path takes an opt to force local (pin the task to
`ollama/qwen3:8b` for that run). Triggered after N Budd requeues on a repo, or by
the user on demand.

**Why not the free per-doc router fallback?** Pinning `extraction → budd` with
active=local would auto-fall-back to local *per doc* for free — but that mixes two
models within one repo and contradicts decision 3. Requeue lives one layer up on
purpose.

## Resolved (grill)

- **O1 — routing.** New `task_type="extraction"` pinned to `custom/budd`. Both
  `_video_extract` and `_video_verify` use it. Independently controllable from the
  `video` pin.
- **O2 — drain trigger.** A repo drains to local qwen **after 3 Budd requeues**
  (requeued & retried on Budd three times without finishing → next pass forces
  local). Automatic, via the loop.
- **O3 — scope + build vehicle.** Cover **github and video together**. **Build it
  via the loop system** — the background dev loop that implements one slice at a
  time on an efficient model. So this ADR's job is to slice the work cleanly; the
  loop consumes the slices below. (The feature's *runtime* requeue still lives in
  the batch layer per the design above — "loop system" here means how the code
  gets written, not how requeue runs.)

## Build status

- **S1 — shipped** (#716, via Felix self-dev on Budd): router `task_type="extraction"`,
  Budd-first, wired into both extraction + verdict calls.
- **S2/S3/S4 — shipped together** (built directly — multi-file control-flow refactor
  beyond self-dev's search/replace edits; the user's "you do it with yours" fallback).
  Requeue counter + repo-grain requeue + drain-at-3 + retry nudge.
- **Video coverage:** video extraction is already Budd-first (S1 changed the shared
  `_video_extract`/`_video_verify`). On a Budd failure mid-batch it rides its existing
  `failed → video_batch_retry → resume` loop (per-video = per-part requeue). The
  repo-grain requeue counter + drain-at-3 are github-specific (a repo is a multi-doc
  "part"); a video is a single-doc part, so its existing retry already covers it.
  ponytail: wire the counter+drain to video too only if a persistently-down Budd is
  observed wedging video batches.

## Slices (loop consumes top-down; each is one small, tested, mergeable PR)

**S1 — Router `extraction` task_type (budd-first).**
`router.py`: add `EXTRACTION_TASK="extraction"`, `EXTRACTION_PREFERRED =
("custom/budd", "ollama/qwen3:8b", "ollama/qwen2.5:7b")`, `seed_extraction_default()`.
`main.py`: call the seed after `_restore_custom_models()` (line 186) alongside the
other seeds (line 219-222); switch `_video_extract` (6222) and `_video_verify`
(6272) to `task_type="extraction"`. Seed pins to the first *installed* preferred →
`custom/budd` when registered, else local qwen. Test: budd registered → pin=budd;
budd absent → pin=local. *Lands alone safely:* budd present → runs on budd; budd
failure raises → video loop already marks the row `failed` (retryable), github
tool errors on an idempotently-resumable repo. No regression, just no drain yet.

**S2 — Per-part Budd-requeue counter + repo-grain requeue.**
`store.py`: a `budd_requeues` count per part (repo for github; the video row for
video — a small column/table). `channel.py` / `github_ingest.py`: catch a Budd
`ConnectionError` mid-part → persist done docs, `+1` the counter, stop the part
(abandon the repo's remaining docs). `github_ingest` returns
`{"repo", "requeued": "budd_unavailable", "extracted": k, "remaining": n-k}`
instead of raising. Test: a stubbed budd-504 on doc 3 of 5 → 3 done, counter=1,
structured result.

**S3 — Drain to local at 3 requeues.**
`main.py`: a force-local path (pin `extraction → ollama/qwen3:8b` for that run).
Batch layer: when a part's `budd_requeues >= 3`, the next pass runs it force-local
and clears the requeue state. Test: counter at 3 → extraction call resolves to
local, part completes.

**S4 — Retry drive (github on the nudge; video reuses its retry).**
A scheduled nudge (mirror `scripts/video_week_resume.py`) reingests github repos
with `remaining > 0`, budd-first, honoring counter+drain; video reuses
`video_batch_retry` + `video_batch_resume`. Test: nudge picks only incomplete
repos and no-ops when none.
