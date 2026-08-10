"""Video MCP plugin — ADR-0017 S1 #639.

Tools: video_ingest(url), video_get(id).

All heavy I/O (download + transcribe) is behind injectable seams so the
test suite never hits the network or real binaries.  See SAFETY in VIDEO.md.

Seams exposed for _wire_plugin_seams:
  set_download_fn(fn)   — injected into cerebral.video.pipeline
  set_transcribe_fn(fn) — injected into cerebral.video.pipeline
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import pipeline as _pipeline
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


# ── plugin class ──────────────────────────────────────────────────────────────

class VideoPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="video_ingest",
                description=(
                    "Download and transcribe a video from a URL (YouTube, TikTok, etc.). "
                    "Stores the transcript in the video store and returns the video id. "
                    "Idempotent: re-running on the same URL skips download/transcription."
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
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="video_get",
                description="Get a stored video by its id. Returns metadata and transcript.",
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
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "video_ingest":
            return await self._video_ingest(args)
        if tool_name == "video_get":
            return self._video_get(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    async def _video_ingest(self, args: dict) -> ToolResult:
        url: str = args.get("url", "").strip()
        channel: str | None = args.get("channel")
        if not url:
            return ToolResult(content="url is required", is_error=True)

        store = _get_store()

        # Idempotency: skip if already transcribed.
        existing = store.get_by_url(url)
        if existing and existing.stage == "transcribed":
            return ToolResult(
                content=json.dumps({
                    "id": existing.id,
                    "stage": existing.stage,
                    "title": existing.title,
                    "transcript_length": len(existing.transcript or ""),
                    "skipped": True,
                })
            )

        # Persist enumerated row first so a crash before download is resumable.
        video_id = store.upsert(url, channel=channel, stage="enumerated")

        try:
            meta = await _pipeline.run(url)
        except Exception as exc:
            logger.error("[video] ingest failed for %s: %s", url, exc)
            return ToolResult(content=f"Ingest failed: {exc}", is_error=True)

        # Commit transcribed state.
        store.upsert(
            url,
            channel=channel,
            title=meta["title"],
            duration=meta["duration"],
            transcript=meta["transcript"],
            stage="transcribed",
        )

        video = store.get_by_url(url)
        return ToolResult(
            content=json.dumps({
                "id": video.id if video else video_id,
                "stage": "transcribed",
                "title": meta["title"],
                "duration": meta["duration"],
                "transcript_length": len(meta["transcript"]),
            })
        )

    def _video_get(self, args: dict) -> ToolResult:
        try:
            video_id = int(args.get("id", 0))
        except (TypeError, ValueError):
            return ToolResult(content="id must be an integer", is_error=True)
        video = _get_store().get_by_id(video_id)
        if video is None:
            return ToolResult(content=f"No video with id {video_id}", is_error=True)
        return ToolResult(content=json.dumps(video.to_dict()))


def create() -> VideoPlugin:
    return VideoPlugin()
