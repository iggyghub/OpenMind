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
        if tool_name == "video_batch_status":
            return self._video_batch_status()
        if tool_name == "video_commit":
            return await self._video_commit(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    async def _video_ingest(self, args: dict) -> ToolResult:
        url: str = args.get("url", "").strip()
        channel: str | None = args.get("channel")
        visual: bool = bool(args.get("visual", False))
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
            meta = await _pipeline.run(url, visual=visual)
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
        escalation_cap: int = int(args.get("escalation_cap", 10))
        sleep_secs: float = float(args.get("sleep_secs", 2.0))
        try:
            result = await _channel.batch_start(
                url,
                _get_store(),
                channel=channel,
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

    def _video_get(self, args: dict) -> ToolResult:
        try:
            video_id = int(args.get("id", 0))
        except (TypeError, ValueError):
            return ToolResult(content="id must be an integer", is_error=True)
        video = _get_store().get_by_id(video_id)
        if video is None:
            return ToolResult(content=f"No video with id {video_id}", is_error=True)
        return ToolResult(content=json.dumps(video.to_dict()))

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

        # Batch status detail.
        counts = status.get("stage_counts", {})
        eta_secs = status.get("eta_seconds")
        running = bool(status.get("running", False))
        channel_name = status.get("channel") or "—"

        status_fields: list[dict] = [
            {"label": "Channel", "value": channel_name},
            {"label": "Status",  "value": "Running" if running else "Idle"},
        ]
        for stage in ("enumerated", "downloaded", "transcribed", "escalated", "extracted", "verified"):
            n = counts.get(stage, 0)
            if n:
                status_fields.append({"label": stage.capitalize(), "value": str(n)})
        if eta_secs is not None:
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

        # Clusters table with verdict/confidence (S6 #644).
        if clusters:
            table_rows = []
            for c in clusters:
                verdict_val = c["verdict"] or "pending"
                confidence_val = (
                    f"{c['confidence']:.0%}" if c["confidence"] is not None else "—"
                )
                table_rows.append(
                    [c["label"], str(c["member_count"]), verdict_val, confidence_val]
                )
            widgets.append({
                "type": "table",
                "columns": ["Idea cluster", "Videos", "Verdict", "Confidence"],
                "rows": table_rows,
            })

            # Per-cluster drill-in: status, evidence links, up to 5 videos.
            for cluster in clusters:
                videos = store.list_videos_by_cluster(cluster["id"], limit=5)
                if not videos:
                    continue
                cluster_fields: list[dict] = [
                    {"label": "Cluster", "value": cluster["label"]},
                ]
                if cluster["verdict"]:
                    cluster_fields.append({"label": "Verdict", "value": cluster["verdict"]})
                if cluster["confidence"] is not None:
                    cluster_fields.append(
                        {"label": "Confidence", "value": f"{cluster['confidence']:.0%}"}
                    )
                if cluster["evidence"]:
                    for i, link in enumerate(cluster["evidence"], 1):
                        cluster_fields.append({"label": f"Evidence {i}", "value": str(link)})
                widgets.append({"type": "detail", "fields": cluster_fields})

                # Commit button: show when verified and not yet committed.
                if cluster["verdict"] and not cluster.get("memory_id"):
                    widgets.append({
                        "type": "action",
                        "id": f"video-commit-{cluster['id']}",
                        "label": "Commit to Memory",
                        "tool": "video_commit",
                        "tool_args": {"cluster_id": cluster["id"]},
                    })
                elif cluster.get("memory_id"):
                    widgets.append({
                        "type": "detail",
                        "fields": [{"label": "Committed", "value": "In Memory"}],
                    })

                items = []
                for v in videos:
                    title = v["title"] or v["url"]
                    idea = (v.get("idea_text") or "").strip()
                    idea_preview = (idea[:80] + "…") if len(idea) > 80 else idea
                    subtitle_parts = [v["stage"]]
                    if idea_preview:
                        subtitle_parts.append(idea_preview)
                    items.append({
                        "title": title,
                        "subtitle": " · ".join(subtitle_parts),
                    })
                widgets.append({"type": "list", "items": items})
        else:
            widgets.append({"type": "list", "items": []})

        return {"title": "Videos", "widgets": widgets}


def create() -> VideoPlugin:
    return VideoPlugin()
