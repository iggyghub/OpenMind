"""Channel batch runner -- ADR-0017 S3 #641.

Enumerates a channel's videos via yt-dlp --flat-playlist, inserts them at
stage=enumerated, then processes them sequentially with sleeps between
downloads (IP-block constraint, ADR-0017 decision 5/6).

All I/O is behind injectable seams: set_enumerate_fn for the yt-dlp call.
Tests stub this out; the loop never touches the network.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from cerebral.video import extraction as _extraction
from cerebral.video import pipeline as _pipeline
from cerebral.video import verdict as _verdict
from cerebral.video.escalation import EscalationBudget
from cerebral.video.store import VideoStore

logger = logging.getLogger(__name__)

# ── injectable seam: enumerate channel videos ─────────────────────────────────
# fn(channel_url) -> list[{"url": str, "title": str}]

_enumerate_fn: Optional[Callable[[str], list[dict]]] = None


def set_enumerate_fn(fn: "Callable[[str], list[dict]] | None") -> None:
    global _enumerate_fn
    _enumerate_fn = fn


def get_enumerate_fn() -> Callable[[str], list[dict]]:
    return _enumerate_fn or _prod_enumerate


def _prod_enumerate(channel_url: str) -> list[dict]:
    # ponytail: live-verify only -- never called in the loop's tests
    import json
    import subprocess

    from cerebral.video.ytdlp_cookies import cookies_cli_args

    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings",
         *cookies_cli_args(), channel_url],
        capture_output=True,
        text=True,
        check=True,
    )
    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
            url = info.get("webpage_url") or info.get("url", "")
            entries.append({"url": url, "title": info.get("title", "")})
        except json.JSONDecodeError:
            continue
    return entries


# ── batch state singleton ─────────────────────────────────────────────────────

class _BatchState:
    def __init__(self) -> None:
        self.task: Optional[asyncio.Task] = None
        self.stop_flag: bool = False
        self.channel_url: Optional[str] = None
        self.collection: str = ""
        self.verify: bool = True
        self.timings: list[float] = []  # per-video wall-clock seconds

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def request_stop(self) -> None:
        self.stop_flag = True

    def reset(self, channel_url: str, collection: str = "", verify: bool = True) -> None:
        self.stop_flag = False
        self.channel_url = channel_url
        self.collection = collection
        self.verify = verify
        self.timings = []


_state = _BatchState()


async def extract_and_cluster(
    store: VideoStore,
    *,
    video_id: int,
    url: str,
    transcript: str,
    ocr_text: str = "",
    visual_summary: str = "",
    collection: str = "",
    verify: bool = False,
) -> str:
    """Extract an idea, assign it to a cluster, and run a verdict -- shared by the
    channel batch AND single-video ingest so single videos can be categorised too.

    ``collection`` scopes clustering + the extraction/verdict category. Advances
    the video stage enumerated/transcribed -> extracted -> verified. Returns the
    final stage reached ('verified', 'extracted', or the caller's stage on failure).
    Never raises: extraction/verdict failures are logged and the stage is left
    where it got to (matching the batch's per-video best-effort behaviour).
    """
    try:
        # ADR-0018 S4: hand the extractor each existing cluster's label AND a
        # representative idea, so it merges on meaning across sources, not label
        # wording. Collection-scoped so github docs fold into existing video
        # clusters in the same collection.
        clusters = store.get_cluster_summaries(collection)
        extracted = await _extraction.extract_idea(
            transcript or "", ocr_text or "", visual_summary or "",
            clusters, category=collection,
        )
        cluster_id = store.get_or_create_cluster(
            extracted["cluster_label"], collection, extracted.get("people_required", 1),
        )
        store.upsert_idea(video_id, extracted["idea"], cluster_id)
        store.upsert(url, stage="extracted")
    except Exception as exc:  # noqa: BLE001
        logger.error("[video] extraction failed %s: %s", url, exc)
        return "transcribed"
    try:
        if store.get_cluster_verdict(cluster_id) is None:
            if verify:
                v = await _verdict.verify_cluster(
                    extracted["cluster_label"], extracted["idea"], category=collection,
                )
                store.set_cluster_verdict(
                    cluster_id, v["verdict"], v["confidence"], v["evidence"]
                )
            else:
                store.set_cluster_verdict(cluster_id, "skipped", None, [])
        store.upsert(url, stage="verified")
        return "verified"
    except Exception as exc:  # noqa: BLE001
        logger.error("[video] verdict failed %s: %s", url, exc)
        return "extracted"


# ── inner batch coroutine ─────────────────────────────────────────────────────

async def _run_batch(
    store: VideoStore,
    channel: str,
    budget: EscalationBudget,
    sleep_secs: float,
    collection: str = "",
    verify: bool = True,
) -> None:
    """Process enumerated rows one at a time; commit per video.

    ``collection`` scopes clustering + the extraction/verdict category.
    ``verify`` gates the validity check: when False, clusters get a "skipped"
    verdict sentinel so they're committable without a fact-check.
    """
    loop = asyncio.get_event_loop()
    while not _state.stop_flag:
        row = store.next_enumerated(channel=channel)
        if row is None:
            logger.info("[video/channel] batch complete for %s", channel)
            break
        t0 = time.monotonic()
        try:
            meta = await _pipeline.run(row.url, budget=budget)
            final_stage = "escalated" if meta["escalated"] else "transcribed"
            store.upsert(
                row.url,
                title=meta["title"] or row.title,
                duration=meta["duration"],
                transcript=meta["transcript"],
                ocr_text=meta["ocr_text"] or None,
                visual_summary=meta["visual_summary"] or None,
                escalated=meta["escalated"],
                stage=final_stage,
            )
            # S5 #642 / S6 #644: extract idea + cluster + verdict (shared with
            # single-video ingest). Best-effort: failures leave the stage as-is.
            await extract_and_cluster(
                store,
                video_id=row.id,
                url=row.url,
                transcript=meta["transcript"] or "",
                ocr_text=meta["ocr_text"] or "",
                visual_summary=meta["visual_summary"] or "",
                collection=collection,
                verify=verify,
            )
        except Exception as exc:
            logger.error("[video/channel] failed %s: %s", row.url, exc)
            store.upsert(row.url, stage="failed")
        _state.timings.append(time.monotonic() - t0)
        # strictly sequential: sleep between downloads (ADR-0017)
        await asyncio.sleep(sleep_secs)
    if _state.stop_flag:
        logger.info("[video/channel] batch stopped for %s", channel)


# ── public API ────────────────────────────────────────────────────────────────

async def batch_start(
    channel_url: str,
    store: VideoStore,
    *,
    channel: str | None = None,
    collection: str = "",
    verify: bool = True,
    escalation_cap: int = 10,
    sleep_secs: float = 2.0,
) -> dict:
    """Enumerate channel, insert enumerated rows, spawn the batch task.

    ``collection`` is the category the channel's ideas file under (scopes
    clusters + steers extraction/verdict).  ``verify`` toggles the validity check.
    Returns immediately; processing runs as a background asyncio task.
    """
    if _state.is_running():
        return {"status": "already_running", "channel": _state.channel_url}

    loop = asyncio.get_event_loop()
    entries: list[dict] = await loop.run_in_executor(
        None, get_enumerate_fn(), channel_url
    )

    channel_name = channel or channel_url
    for entry in entries:
        store.enumerate_video(
            entry["url"],
            channel=channel_name,
            title=entry.get("title") or None,
            collection=collection,
        )

    _state.reset(channel_name, collection, verify)
    budget = EscalationBudget(escalation_cap)
    _state.task = asyncio.create_task(
        _run_batch(store, channel_name, budget, sleep_secs, collection, verify)
    )

    return {
        "status": "started",
        "channel": channel_url,
        "collection": collection,
        "verify": verify,
        "enumerated": len(entries),
        "escalation_cap": escalation_cap,
    }


def batch_stop() -> dict:
    """Request the running batch to halt after the current video."""
    if not _state.is_running():
        return {"status": "not_running"}
    _state.request_stop()
    return {"status": "stop_requested", "channel": _state.channel_url}


def batch_resume(
    store: VideoStore, *, escalation_cap: int = 10, sleep_secs: float = 2.0
) -> dict:
    """Resume a paused batch on the stored channel WITHOUT re-enumerating.

    S13 #664: the rows are already in the DB, so resume just re-spawns the worker
    over the remaining ``enumerated`` rows -- no 1822-row re-scan. Returns
    ``no_channel`` if there is nothing to resume (e.g. Cerebral was restarted).
    """
    if _state.is_running():
        return {"status": "already_running", "channel": _state.channel_url}
    # After a Cerebral restart the in-memory channel is gone; recover it from the
    # DB (the channel with pending rows) so resume still works (S16 #671).
    channel = _state.channel_url or store.pending_channel()
    if not channel:
        return {"status": "no_channel"}
    _state.channel_url = channel  # re-establish for subsequent toggles/hotkey
    # Recover the collection from a pending row so a resumed batch keeps scoping.
    # ponytail: verify=off is not persisted across a full restart -- resume
    # defaults verify=on (safe: worst case is a fact-check that wasn't needed);
    # add a videos.verify column if that ceiling ever bites.
    pending = store.next_enumerated(channel=channel)
    if pending is not None and pending.collection is not None:
        _state.collection = pending.collection
    _state.stop_flag = False
    budget = EscalationBudget(escalation_cap)
    _state.task = asyncio.create_task(
        _run_batch(store, channel, budget, sleep_secs, _state.collection, _state.verify)
    )
    return {"status": "resumed", "channel": channel, "collection": _state.collection}


def batch_toggle(store: VideoStore, **kwargs) -> dict:
    """Pause a running batch, or resume a paused one (S13 #664 hotkey target).

    A graceful stop drains the current video before the task ends, so the task
    reports "running" for up to a video's length after a pause. A toggle in that
    window CANCELS the pending stop (resume intent) rather than re-requesting it.
    """
    if _state.is_running():
        if _state.stop_flag:
            _state.stop_flag = False  # un-pause before the drain finishes
            return {"action": "resumed", "status": "resumed", "channel": _state.channel_url}
        return {"action": "paused", **batch_stop()}
    return {"action": "resumed", **batch_resume(store, **kwargs)}


def batch_status(store: VideoStore) -> dict:
    """Return stage counts + measured-throughput ETA for the active channel."""
    counts = store.stage_counts(channel=_state.channel_url)
    running = _state.is_running()

    eta_secs = None
    enumerated_remaining = counts.get("enumerated", 0)
    if _state.timings and enumerated_remaining > 0:
        mean_secs = sum(_state.timings) / len(_state.timings)
        eta_secs = round(enumerated_remaining * mean_secs)

    return {
        "running": running,
        "channel": _state.channel_url,
        "collection": _state.collection,
        "stage_counts": counts,
        "eta_seconds": eta_secs,
    }
