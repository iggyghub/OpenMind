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

    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", channel_url],
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
        self.timings: list[float] = []  # per-video wall-clock seconds

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def request_stop(self) -> None:
        self.stop_flag = True

    def reset(self, channel_url: str) -> None:
        self.stop_flag = False
        self.channel_url = channel_url
        self.timings = []


_state = _BatchState()


# ── inner batch coroutine ─────────────────────────────────────────────────────

async def _run_batch(
    store: VideoStore,
    channel: str,
    budget: EscalationBudget,
    sleep_secs: float,
) -> None:
    """Process enumerated rows one at a time; commit per video."""
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
            # S5 #642: extract idea + assign cluster; stage advances to 'extracted'
            try:
                labels = store.get_cluster_labels()
                extracted = await _extraction.extract_idea(
                    meta["transcript"] or "",
                    meta["ocr_text"] or "",
                    meta["visual_summary"] or "",
                    labels,
                )
                cluster_id = store.get_or_create_cluster(
                    extracted["cluster_label"],
                    extracted.get("people_required", 1),
                )
                store.upsert_idea(row.id, extracted["idea"], cluster_id)
                store.upsert(row.url, stage="extracted")
                # S6 #644: verdict per cluster -- verify once, inherit on later videos
                try:
                    existing_verdict = store.get_cluster_verdict(cluster_id)
                    if existing_verdict is None:
                        v = await _verdict.verify_cluster(
                            extracted["cluster_label"], extracted["idea"]
                        )
                        store.set_cluster_verdict(
                            cluster_id, v["verdict"], v["confidence"], v["evidence"]
                        )
                    store.upsert(row.url, stage="verified")
                except Exception as exc:
                    logger.error("[video/channel] verdict failed %s: %s", row.url, exc)
                    # stage stays at extracted; batch continues
            except Exception as exc:
                logger.error("[video/channel] extraction failed %s: %s", row.url, exc)
                # stage stays at transcribed/escalated; batch continues
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
    escalation_cap: int = 10,
    sleep_secs: float = 2.0,
) -> dict:
    """Enumerate channel, insert enumerated rows, spawn the batch task.

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
        )

    _state.reset(channel_name)
    budget = EscalationBudget(escalation_cap)
    _state.task = asyncio.create_task(
        _run_batch(store, channel_name, budget, sleep_secs)
    )

    return {
        "status": "started",
        "channel": channel_url,
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
    _state.stop_flag = False
    budget = EscalationBudget(escalation_cap)
    _state.task = asyncio.create_task(
        _run_batch(store, channel, budget, sleep_secs)
    )
    return {"status": "resumed", "channel": channel}


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
        "stage_counts": counts,
        "eta_seconds": eta_secs,
    }
