"""GitHub ingest MCP plugin -- ADR-0018 S3 (the tracer bullet).

github_ingest(repo_url, category): clone a repo (no API), read its curated
markdown docs, and extract one idea per doc into the SHARED clusters/collections
spine (channel.extract_and_cluster) -- so GitHub-sourced ideas sit alongside
video ones. Each doc becomes a source item row (source_type='github',
channel=repo_url) in the same videos table.

Acquisition seams live in cerebral.video.github_source (clone/head_sha/fetch),
all injectable so tests need no network or git. The store + extraction seam are
the video plugin's -- reused, not forked.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import plugins.video as _video  # reuse the shared VideoStore singleton
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import channel as _channel
from cerebral.video import github_source as _gh

logger = logging.getLogger(__name__)

PLUGIN_NAME = "github_ingest"

# Same posture as the video plugin: clone is external_data_read + fs_write.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"external_data_read", "fs_write"})


def _repo_name(repo_url: str) -> str:
    return repo_url.rstrip("/").split("/")[-1]


def _grounding(repo_url: str, meta: dict) -> str:
    """One-line repo context prepended to each doc before extraction (ADR-0018 4)."""
    desc = (meta.get("description") or "").strip()
    topics = meta.get("topics") or []
    parts = [f"Repo: {_repo_name(repo_url)}"]
    if desc:
        parts.append(f"-- {desc}")
    if topics:
        parts.append(f"(topics: {', '.join(topics)})")
    return " ".join(parts)


class GithubIngestPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="github_ingest",
                description=(
                    "Read a GitHub repository's docs and extract an idea from each "
                    "markdown doc into a collection (category), filed alongside "
                    "video-sourced ideas. No API key -- clones the repo (git) and "
                    "reads the curated docs (README + docs/**). Blank category -> "
                    "'Uncategorised'; re-file later with video_move_cluster. "
                    "Idempotent: unchanged docs are skipped on re-ingest."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "repo_url": {
                            "type": "string",
                            "description": "GitHub repo URL, e.g. https://github.com/owner/repo",
                        },
                        "category": {
                            "type": "string",
                            "description": (
                                "Collection to file the extracted ideas under "
                                "(e.g. 'harness improvement'). Blank -> 'Uncategorised'."
                            ),
                        },
                    },
                    "required": ["repo_url"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "github_ingest":
            return await self._github_ingest(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    async def _github_ingest(self, args: dict) -> ToolResult:
        repo_url: str = (args.get("repo_url") or "").strip()
        category: str = (args.get("category") or "").strip() or "Uncategorised"
        if not repo_url:
            return ToolResult(content="repo_url is required", is_error=True)

        store = _video._get_store()

        # ── acquire (all seam-injected: no network/git in tests) ──────────────
        with tempfile.TemporaryDirectory() as tmp:
            clone_dir = Path(tmp) / "repo"
            try:
                _gh.get_clone_fn()(repo_url, clone_dir)
            except Exception as exc:  # noqa: BLE001
                logger.error("[github] clone failed for %s: %s", repo_url, exc)
                return ToolResult(content=f"Clone failed: {exc}", is_error=True)

            head_sha = None
            try:
                head_sha = _gh.get_head_sha_fn()(clone_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[github] head_sha failed for %s: %s", repo_url, exc)

            try:
                meta = _gh.fetch_description(repo_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[github] description fetch failed for %s: %s", repo_url, exc)
                meta = {"description": "", "topics": []}

            docs = _gh.enumerate_docs(clone_dir)

        store.upsert_github_repo(
            repo_url, head_sha=head_sha, description=(meta.get("description") or None)
        )

        # ── extract each doc into the shared clusters ─────────────────────────
        grounding = _grounding(repo_url, meta)
        results = []
        skipped = 0
        for d in docs:
            doc_url = repo_url + "#" + d["relpath"]
            existing = store.get_by_url(doc_url)
            if (
                existing is not None
                and (existing.transcript or "") == d["text"]
                and existing.stage in ("extracted", "verified")
            ):
                skipped += 1
                continue  # unchanged + already clustered -> idempotent skip
            vid = store.upsert(
                doc_url,
                channel=repo_url,
                collection=category,
                title=d["relpath"],
                transcript=d["text"],
                stage="transcribed",
                source_type="github",
            )
            # Grounding is prepended for extraction only; the row stores raw text
            # so the dedup content-compare above stays stable.
            text = (grounding + "\n\n" + d["text"]) if grounding else d["text"]
            final = await _channel.extract_and_cluster(
                store, video_id=vid, url=doc_url, transcript=text,
                collection=category, verify=False,
            )
            results.append({"doc": d["relpath"], "stage": final})

        return ToolResult(content=json.dumps({
            "repo": repo_url,
            "collection": category,
            "docs": len(docs),
            "extracted": len(results),
            "skipped": skipped,
            "results": results,
        }))


def create() -> GithubIngestPlugin:
    return GithubIngestPlugin()
