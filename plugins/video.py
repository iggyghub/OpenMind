"""Video MCP plugin -- ADR-0017.

Tools: video_ingest(url, visual=False), video_get(id),
       video_batch_start(url), video_batch_stop(), video_batch_status(),
       video_commit(cluster_id).

All heavy I/O (download, transcribe, OCR, vision, enumerate) is behind injectable
seams so the test suite never hits the network or real binaries.  See SAFETY in VIDEO.md.

Seams exposed for _wire_plugin_seams:
  set_download_fn(fn)   -- injected into cerebral.video.pipeline
  set_transcribe_fn(fn) -- injected into cerebral.video.pipeline
  set_keyframe_fn(fn)   -- injected into cerebral.video.escalation   S2 #640
  set_ocr_fn(fn)        -- injected into cerebral.video.escalation   S2 #640
  set_vision_fn(fn)     -- injected into cerebral.video.escalation   S2 #640
  set_enumerate_fn(fn)  -- injected into cerebral.video.channel       S3 #641
  set_extract_fn(fn)    -- injected into cerebral.video.extraction    S5 #642
  set_verify_fn(fn)     -- injected into cerebral.video.verdict       S6 #644
  set_commit_fn(fn)     -- async fn(cluster_id, idea_text, cluster) -> memory_id  S7 #645
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import channel as _channel
from cerebral.video import escalation as _escalation
from cerebral.video import extraction as _extraction
from cerebral.video import pipeline as _pipeline
from cerebral.video import screen_capture as _screen_capture
from cerebral.video import verdict as _verdict
from cerebral.video.store import VideoStore

logger = logging.getLogger(__name__)

PLUGIN_NAME = "video"

# ADR-0005 capabilities: video download is external_data_read + fs_write (audio cache).
# Transcription is local compute only.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "fs_write",
})

# Module-level store; tests swap this via set_store.
_store: Optional[VideoStore] = None


def _get_store() -> VideoStore:
    global _store
    if _store is None:
        _store = VideoStore()
    return _store


# ── seam setters (called by _wire_plugin_seams in main.py) ───────────────────

def set_store(store: VideoStore) -> None:
    global _store
    _store = store


def set_download_fn(fn: Callable) -> None:
    _pipeline.set_download_fn(fn)


def set_transcribe_fn(fn: Callable) -> None:
    _pipeline.set_transcribe_fn(fn)


def set_keyframe_fn(fn: Callable) -> None:  # S2 #640
    _escalation.set_keyframe_fn(fn)


def set_ocr_fn(fn: Callable) -> None:  # S2 #640
    _escalation.set_ocr_fn(fn)


def set_vision_fn(fn: Callable) -> None:  # S2 #640
    _escalation.set_vision_fn(fn)


def set_enumerate_fn(fn: Callable) -> None:  # S3 #641
    _channel.set_enumerate_fn(fn)


def set_capture_fn(fn: Callable) -> None:  # S10 #658
    _screen_capture.set_capture_fn(fn)


def set_extract_fn(fn: Callable) -> None:  # S5 #642
    _extraction.set_extract_fn(fn)


def set_verify_fn(fn: Callable) -> None:  # S6 #644
    _verdict.set_verify_fn(fn)


# S7 #645: commit_fn writes a verified idea to Memory.
# Signature: async fn(cluster_id: int, idea_text: str, cluster: dict) -> str (memory_id)
_commit_fn: Optional[Callable] = None


def set_commit_fn(fn: Callable) -> None:  # S7 #645
    global _commit_fn
    _commit_fn = fn


# ── plugin class ──────────────────────────────────────────────────────────────

class VideoPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="video_ingest",
                description=(
                    "Download and transcribe a video from a URL (YouTube, TikTok, etc.). "
                    "Stores the transcript (and optional visual layers) in the video store "
                    "and returns the video id. "
                    "Idempotent: re-running on the same URL skips completed stages. "
                    "Set visual=true to force OCR + vision even on rich-audio videos."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL of the video to ingest.",
                        },
                        "channel": {
                            "type": "string",
                            "description": "Optional channel name for grouping.",
                        },
                        "visual": {
                            "type": "boolean",
                            "description": (
                                "Force visual layers (OCR + vision on keyframes) "
                                "regardless of transcript content."
                            ),
                        },
                        "capture": {
                            "type": "boolean",
                            "description": (
                                "Force screen-watch capture (open browser, play, "
                                "record audio + frames) instead of yt-dlp. Use for "
                                "sources yt-dlp can't download, e.g. TikTok. yt-dlp "
                                "failures already fall back to capture automatically."
                            ),
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="video_get",
                description=(
                    "Get a stored video by its id. "
                    "Returns metadata, transcript, and visual data if escalated."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset({"external_data_read"}),
                schema={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "The video id returned by video_ingest.",
                        },
                    },
                    "required": ["id"],
                },
            ),
            Tool(
                name="video_batch_start",
                description=(
                    "Enumerate all videos from a channel URL and start processing them "
                    "sequentially in the background (download + transcribe + optional escalation). "
                    "Idempotent per video: already-processed rows are skipped on resume. "
                    "Returns immediately; use video_batch_status to track progress."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Channel URL (e.g. TikTok or YouTube channel page).",
                        },
                        "channel": {
                            "type": "string",
                            "description": "Human-readable channel name for grouping (defaults to url).",
                        },
                        "category": {
                            "type": "string",
                            "description": (
                                "Category this channel's ideas file under, e.g. "
                                "'money-making idea' or 'harness improvement'. Scopes the "
                                "clusters and becomes the Memory category on commit. "
                                "Defaults to 'money-making idea'."
                            ),
                        },
                        "verify": {
                            "type": "boolean",
                            "description": (
                                "Run a validity/soundness check on each idea before it can "
                                "be committed (default true). Turn off for trusted how-to "
                                "channels where a fraud/soundness verdict adds no value."
                            ),
                        },
                        "escalation_cap": {
                            "type": "integer",
                            "description": "Max videos that may trigger visual escalation in this run.",
                        },
                        "sleep_secs": {
                            "type": "number",
                            "description": "Seconds to sleep between each video download (anti-block).",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="video_batch_stop",
                description="Request the running channel batch to stop after the current video.",
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset(),
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="video_batch_toggle",
                description=(
                    "Pause the running channel batch, or resume a paused one on the same "
                    "channel without re-enumerating. The global pause/resume hotkey target."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset(),
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="video_batch_resume",
                description=(
                    "Resume the channel batch after a restart -- continues the pending "
                    "('enumerated') videos found in the store, no re-enumeration needed."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset(),
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="video_batch_clear",
                description=(
                    "Clear the batch queue: delete unwatched ('enumerated') videos so a "
                    "new channel can start clean. Watched clusters and committed ideas "
                    "are kept. Stops the running batch first. Optional channel scopes it."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset(),
                schema={
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Only clear this channel's pending rows (default: all channels).",
                        },
                    },
                },
            ),
            Tool(
                name="video_batch_status",
                description=(
                    "Return the batch runner status: stage counts per stage and an ETA in seconds "
                    "based on measured throughput. ETA is null until at least one video completes."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset({"external_data_read"}),
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="video_query",
                description=(
                    "Filter, sort, and drill into the idea clusters by conversation "
                    "(\"show me the legit ones a solo person can do\"). "
                    "List mode: returns matching clusters, each with a representative "
                    "video to watch. Drill-in mode: pass cluster_id to get that "
                    "cluster's member videos."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset({"external_data_read"}),
                schema={
                    "type": "object",
                    "properties": {
                        "cluster_id": {
                            "type": "integer",
                            "description": "Drill in: return this cluster's member videos instead of the list.",
                        },
                        "collection": {
                            "type": "string",
                            "description": (
                                "Keep only clusters in this collection/category "
                                "(e.g. 'money-making idea', 'harness improvement')."
                            ),
                        },
                        "verdict": {
                            "type": "string",
                            "description": "Keep only clusters with this verdict (e.g. 'legit', 'dubious').",
                        },
                        "max_people": {
                            "type": "integer",
                            "description": "Keep only clusters doable by this many people or fewer (1 = solo).",
                        },
                        "min_confidence": {
                            "type": "number",
                            "description": "Keep only clusters at or above this verdict confidence (0-1).",
                        },
                        "min_members": {
                            "type": "integer",
                            "description": "Keep only clusters with at least this many source videos.",
                        },
                        "uncommitted": {
                            "type": "boolean",
                            "description": "Keep only clusters not yet committed to Memory.",
                        },
                        "sort": {
                            "type": "string",
                            "enum": ["members", "confidence"],
                            "description": "Sort order (default: members, most videos first).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Cap the number of results (drill-in: member videos).",
                        },
                    },
                },
            ),
            Tool(
                name="video_commit",
                description=(
                    "Commit a verified idea cluster to Memory as a durable fact with its verdict attached. "
                    "The cluster must have a verdict before committing. "
                    "Idempotent: committing the same cluster twice returns the existing memory entry."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=frozenset({"external_data_read"}),
                schema={
                    "type": "object",
                    "properties": {
                        "cluster_id": {
                            "type": "integer",
                            "description": "The cluster id to commit to Memory.",
                        },
                    },
                    "required": ["cluster_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "video_ingest":
            return await self._video_ingest(args)
        if tool_name == "video_get":
            return self._video_get(args)
        if tool_name == "video_batch_start":
            return await self._video_batch_start(args)
        if tool_name == "video_batch_stop":
            return self._video_batch_stop()
        if tool_name == "video_batch_toggle":
            return ToolResult(content=json.dumps(_channel.batch_toggle(_get_store())))
        if tool_name == "video_batch_resume":
            return ToolResult(content=json.dumps(_channel.batch_resume(_get_store())))
        if tool_name == "video_batch_status":
            return self._video_batch_status()
        if tool_name == "video_batch_clear":
            return self._video_batch_clear(args)
        if tool_name == "video_query":
            return self._video_query(args)
        if tool_name == "video_commit":
            return await self._video_commit(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    async def _video_ingest(self, args: dict) -> ToolResult:
        url: str = args.get("url", "").strip()
        channel: str | None = args.get("channel")
        visual: bool = bool(args.get("visual", False))
        capture: bool = bool(args.get("capture", False))  # S10 #658: force screen-watch
        if not url:
            return ToolResult(content="url is required", is_error=True)

        store = _get_store()

        existing = store.get_by_url(url)

        # Already escalated (or further) -- skip entirely.
        if existing and existing.stage in ("escalated", "extracted", "verified"):
            return ToolResult(
                content=json.dumps({
                    "id": existing.id,
                    "stage": existing.stage,
                    "title": existing.title,
                    "transcript_length": len(existing.transcript or ""),
                    "ocr_text_length": len(existing.ocr_text or ""),
                    "skipped": True,
                })
            )

        # Already transcribed: run escalation only if needed, skip otherwise.
        if existing and existing.stage == "transcribed":
            transcript = existing.transcript or ""
            if not _escalation.needs_escalation(transcript, visual=visual):
                return ToolResult(
                    content=json.dumps({
                        "id": existing.id,
                        "stage": "transcribed",
                        "title": existing.title,
                        "transcript_length": len(transcript),
                        "skipped": True,
                    })
                )
            # Escalate without re-downloading/transcribing.
            return await self._escalate_existing(existing.id, url, transcript, store)

        # Fresh ingest (enumerated or earlier).
        video_id = store.upsert(url, channel=channel, stage="enumerated")

        try:
            meta = await _pipeline.run(url, visual=visual, force_capture=capture)
        except Exception as exc:
            logger.error("[video] ingest failed for %s: %s", url, exc)
            return ToolResult(content=f"Ingest failed: {exc}", is_error=True)

        final_stage = "escalated" if meta["escalated"] else "transcribed"
        store.upsert(
            url,
            channel=channel,
            title=meta["title"],
            duration=meta["duration"],
            transcript=meta["transcript"],
            ocr_text=meta["ocr_text"] or None,
            visual_summary=meta["visual_summary"] or None,
            escalated=meta["escalated"],
            stage=final_stage,
        )

        video = store.get_by_url(url)
        return ToolResult(
            content=json.dumps({
                "id": video.id if video else video_id,
                "stage": final_stage,
                "title": meta["title"],
                "duration": meta["duration"],
                "transcript_length": len(meta["transcript"]),
                "ocr_text_length": len(meta["ocr_text"]),
                "escalated": meta["escalated"],
            })
        )

    async def _escalate_existing(
        self, video_id: int, url: str, transcript: str, store: VideoStore
    ) -> ToolResult:
        """Run escalation on a video that was already transcribed."""
        import tempfile
        from pathlib import Path as _Path

        try:
            with tempfile.TemporaryDirectory() as tmp:
                vis = await _escalation.run(url, _Path(tmp))
        except Exception as exc:
            logger.error("[video] escalation failed for %s: %s", url, exc)
            return ToolResult(content=f"Escalation failed: {exc}", is_error=True)

        store.upsert(
            url,
            ocr_text=vis["ocr_text"] or None,
            visual_summary=vis["visual_summary"] or None,
            escalated=True,
            stage="escalated",
        )
        video = store.get_by_id(video_id)
        return ToolResult(
            content=json.dumps({
                "id": video_id,
                "stage": "escalated",
                "title": video.title if video else None,
                "transcript_length": len(transcript),
                "ocr_text_length": len(vis["ocr_text"]),
                "escalated": True,
            })
        )

    async def _video_batch_start(self, args: dict) -> ToolResult:
        url: str = args.get("url", "").strip()
        if not url:
            return ToolResult(content="url is required", is_error=True)
        channel: str | None = args.get("channel")
        category: str = (args.get("category") or "money-making idea").strip()
        verify: bool = bool(args.get("verify", True))
        escalation_cap: int = int(args.get("escalation_cap", 10))
        sleep_secs: float = float(args.get("sleep_secs", 2.0))
        try:
            result = await _channel.batch_start(
                url,
                _get_store(),
                channel=channel,
                collection=category,
                verify=verify,
                escalation_cap=escalation_cap,
                sleep_secs=sleep_secs,
            )
        except Exception as exc:
            logger.error("[video] batch_start failed: %s", exc)
            return ToolResult(content=f"batch_start failed: {exc}", is_error=True)
        return ToolResult(content=json.dumps(result))

    def _video_batch_stop(self) -> ToolResult:
        return ToolResult(content=json.dumps(_channel.batch_stop()))

    def _video_batch_status(self) -> ToolResult:
        return ToolResult(content=json.dumps(_channel.batch_status(_get_store())))

    def _video_batch_clear(self, args: dict) -> ToolResult:  # S21
        # Stop the runner first so it can't re-insert / race the delete.
        _channel.batch_stop()
        channel = args.get("channel") or None
        cleared = _get_store().clear_pending(channel=channel)
        return ToolResult(content=json.dumps({"cleared": cleared, "channel": channel}))

    def _video_get(self, args: dict) -> ToolResult:
        try:
            video_id = int(args.get("id", 0))
        except (TypeError, ValueError):
            return ToolResult(content="id must be an integer", is_error=True)
        video = _get_store().get_by_id(video_id)
        if video is None:
            return ToolResult(content=f"No video with id {video_id}", is_error=True)
        return ToolResult(content=json.dumps(video.to_dict()))

    def _video_query(self, args: dict) -> ToolResult:  # S19
        """Filter/sort clusters, or drill into one cluster's videos."""
        store = _get_store()

        # Drill-in mode: cluster_id given -> that cluster + its member videos.
        if args.get("cluster_id") is not None:
            try:
                cluster_id = int(args["cluster_id"])
            except (TypeError, ValueError):
                return ToolResult(content="cluster_id must be an integer", is_error=True)
            cluster = store.get_cluster_by_id(cluster_id)
            if cluster is None:
                return ToolResult(content=f"No cluster with id {cluster_id}", is_error=True)
            limit = int(args.get("limit", 5))
            cluster["videos"] = store.list_videos_by_cluster(cluster_id, limit=limit)
            return ToolResult(content=json.dumps(cluster))

        # List mode: filter + sort.
        clusters = store.list_clusters(collection=args.get("collection"))
        verdict = args.get("verdict")
        if verdict:
            clusters = [c for c in clusters if c["verdict"] == verdict]
        if args.get("max_people") is not None:
            mp = int(args["max_people"])
            clusters = [c for c in clusters if (c.get("people_required") or 1) <= mp]
        if args.get("min_confidence") is not None:
            mc = float(args["min_confidence"])
            clusters = [c for c in clusters if (c.get("confidence") or 0) >= mc]
        if args.get("min_members") is not None:
            mm = int(args["min_members"])
            clusters = [c for c in clusters if c["member_count"] >= mm]
        if args.get("uncommitted"):
            clusters = [c for c in clusters if not c.get("memory_id")]

        if args.get("sort") == "confidence":
            clusters.sort(key=lambda c: (c.get("confidence") or 0, c["member_count"]), reverse=True)
        # else: list_clusters already returns member_count desc

        if args.get("limit") is not None:
            clusters = clusters[: int(args["limit"])]

        # Attach one representative video per cluster so Felix can offer a watch.
        for c in clusters:
            rep = store.list_videos_by_cluster(c["id"], limit=1)
            c["representative"] = rep[0] if rep else None

        return ToolResult(content=json.dumps({"count": len(clusters), "clusters": clusters}))

    async def _video_commit(self, args: dict) -> ToolResult:  # S7 #645
        import asyncio as _asyncio
        try:
            cluster_id = int(args.get("cluster_id", 0))
        except (TypeError, ValueError):
            return ToolResult(content="cluster_id must be an integer", is_error=True)

        store = _get_store()
        cluster = store.get_cluster_by_id(cluster_id)
        if cluster is None:
            return ToolResult(content=f"No cluster with id {cluster_id}", is_error=True)

        # Idempotent: already committed
        if cluster.get("memory_id"):
            return ToolResult(content=json.dumps({
                "cluster_id": cluster_id,
                "already_committed": True,
                "memory_id": cluster["memory_id"],
            }))

        if not cluster.get("verdict"):
            return ToolResult(
                content=(
                    f"Cluster {cluster['label']!r} has no verdict yet — "
                    "run the batch or verify manually first"
                ),
                is_error=True,
            )

        idea_text = store.get_cluster_idea_text(cluster_id) or cluster["label"]

        fn = _commit_fn
        if fn is None:
            return ToolResult(
                content="commit_fn not wired — ensure _wire_plugin_seams ran",
                is_error=True,
            )

        try:
            result = fn(cluster_id, idea_text, cluster)
            if _asyncio.iscoroutine(result):
                result = await result
            memory_id = str(result)
        except Exception as exc:
            logger.error("[video] commit failed for cluster %d: %s", cluster_id, exc)
            return ToolResult(content=f"Commit failed: {exc}", is_error=True)

        store.set_cluster_committed(cluster_id, memory_id)
        return ToolResult(content=json.dumps({
            "cluster_id": cluster_id,
            "label": cluster["label"],
            "verdict": cluster["verdict"],
            "memory_id": memory_id,
            "committed": True,
        }))

    def panel_spec(self, profile_id: "int | None") -> dict:  # noqa: ARG002
        """Declarative Videos panel (ADR-0017 decision 9, ADR-0012)."""
        store = _get_store()
        status = _channel.batch_status(store)
        clusters = store.list_clusters()

        widgets: list[dict] = []

        # Ingest and batch-start action forms.
        widgets.append({
            "type": "action",
            "id": "video-ingest",
            "label": "Ingest video",
            "tool": "video_ingest",
            "tool_args": {},
            "input_arg": "url",
            "input_placeholder": "https://tiktok.com/... or YouTube URL",
        })
        widgets.append({
            "type": "action",
            "id": "video-batch-start",
            "label": "Start channel batch",
            "tool": "video_batch_start",
            "tool_args": {},
            "input_arg": "url",
            "input_placeholder": "https://tiktok.com/@channel or YouTube channel URL",
        })

        # ── Batch status (compact): Status + meaningful counts only. S11 #659 ──
        counts = status.get("stage_counts", {})
        eta_secs = status.get("eta_seconds")
        running = bool(status.get("running", False))

        verified = counts.get("verified", 0)
        failed = counts.get("failed", 0)
        pending = sum(
            counts.get(s, 0)
            for s in ("enumerated", "downloaded", "transcribed", "escalated", "extracted")
        )

        # Global totals (survive a restart that cleared the active-channel state) --
        # so the volume of work + resumable backlog show even when idle. S16 #671.
        all_counts = store.stage_counts()
        processed_total = sum(
            all_counts.get(s, 0) for s in ("transcribed", "escalated", "extracted", "verified")
        )
        pending_total = store.total_pending()

        status_fields: list[dict] = [
            {"label": "Status", "value": "Running" if running else "Idle"},
        ]
        if processed_total:
            status_fields.append({"label": "Processed", "value": str(processed_total)})
        # The active channel is only informative while a batch is in flight; when
        # idle it's a stale single-video URL, so drop it (S11 #659).
        if running and status.get("channel"):
            status_fields.append({"label": "Channel", "value": status["channel"]})
        if verified:
            status_fields.append({"label": "Verified", "value": str(verified)})
        if pending:
            status_fields.append({"label": "Pending", "value": str(pending)})
        elif pending_total:
            status_fields.append({"label": "Pending", "value": str(pending_total)})
        if failed:
            status_fields.append({"label": "Failed", "value": str(failed)})
        if running and eta_secs is not None:
            mins, secs = divmod(int(eta_secs), 60)
            status_fields.append({
                "label": "ETA",
                "value": f"{mins}m {secs}s" if mins else f"{secs}s",
            })

        widgets.append({"type": "detail", "fields": status_fields})

        if running:
            widgets.append({
                "type": "action",
                "id": "video-batch-stop",
                "label": "Stop batch",
                "tool": "video_batch_stop",
                "tool_args": {},
            })
        elif pending_total:
            # S16 #671: after a restart the batch is idle but has pending rows --
            # a one-click resume that recovers the channel from the DB.
            widgets.append({
                "type": "action",
                "id": "video-batch-resume",
                "label": f"Resume batch ({pending_total} pending)",
                "tool": "video_batch_resume",
                "tool_args": {},
            })

        # S21: clear the unwatched queue (keeps watched clusters + committed ideas).
        if not running and pending_total:
            widgets.append({
                "type": "action",
                "id": "video-batch-clear",
                "label": f"Clear queue ({pending_total} unwatched)",
                "tool": "video_batch_clear",
                "tool_args": {},
            })

        # ── Results: clusters table is the single view (no per-cluster wall) ──
        if clusters:
            widgets.append({
                "type": "table",
                "columns": ["Idea cluster", "Videos", "People", "Verdict", "Confidence", "In Memory"],
                "rows": [
                    [
                        c["label"],
                        str(c["member_count"]),
                        str(c.get("people_required") or 1),
                        c["verdict"] or "pending",
                        f"{c['confidence']:.0%}" if c["confidence"] is not None else "—",
                        "✓" if c.get("memory_id") else "",
                    ]
                    for c in clusters
                ],
            })

            # S8 #653: two-person ideas grouped together (kept).
            two_person = [c for c in clusters if (c.get("people_required") or 1) == 2]
            if two_person:
                widgets.append({
                    "type": "detail",
                    "fields": [{"label": "Group", "value": "Requires two people"}],
                })
                widgets.append({
                    "type": "table",
                    "columns": ["Idea cluster", "Videos", "Verdict"],
                    "rows": [
                        [c["label"], str(c["member_count"]), c["verdict"] or "pending"]
                        for c in two_person
                    ],
                })

            # Consolidated commit: one button per verified-and-uncommitted cluster.
            for c in clusters:
                if c["verdict"] and not c.get("memory_id"):
                    widgets.append({
                        "type": "action",
                        "id": f"video-commit-{c['id']}",
                        "label": f"Commit “{c['label']}” to Memory",
                        "tool": "video_commit",
                        "tool_args": {"cluster_id": c["id"]},
                    })
        else:
            widgets.append({"type": "list", "items": []})

        # ── Recent videos: one capped list, not per-cluster walls. S11 #659 ──
        recent = store.list_recent_videos(limit=8)
        if recent:
            items = []
            for v in recent:
                idea = (v.get("idea_text") or "").strip()
                idea_preview = (idea[:70] + "…") if len(idea) > 70 else idea
                sub = " · ".join(p for p in (v["stage"], idea_preview) if p)
                items.append({"title": v["title"] or v["url"], "subtitle": sub})
            widgets.append({"type": "list", "items": items})

        return {"title": "Videos", "widgets": widgets}


def create() -> VideoPlugin:
    return VideoPlugin()
