"""Webpage ingest MCP plugin -- the Library's "Webpage" tab.

webpage_ingest(url, category): GET one public page, strip the page chrome,
and extract an idea from its text into the SHARED clusters/collections spine
(channel.extract_and_cluster) -- the same spine Videos/GitHub/Books ride
(ADR-0017/0018/0025). Each page is a source row (source_type='webpage',
channel=the page's host) in the shared videos table.

Also home of the two helpers the Reddit tab (plugins/reddit_ingest.py) shares:
``ingest_items`` (items -> rows -> extract) and ``source_panel`` (ingest form +
recent items + source-scoped clusters).

SAFETY: public GET only, no keys, no writes. Page text is untrusted input to
the extraction LLM -- contained, as with video transcripts, by the manual
commit-to-Memory gate.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse

import plugins.video as _video  # reuse the shared VideoStore singleton
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import channel as _channel
from cerebral.video import web_source as _ws

logger = logging.getLogger(__name__)

PLUGIN_NAME = "web_ingest"

REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"external_data_read"})

MIN_WORDS = 50  # below this a "page" is a login wall / JS shell, not content


def split_urls(raw: str) -> list[str]:
    """One or many URLs from a field -- split on whitespace/commas/newlines."""
    return [u for u in re.split(r"[\s,]+", (raw or "").strip()) if u]


async def ingest_items(store, items: list[dict], *, source_type: str, channel: str, category: str) -> dict:
    """[{url, title, text}] -> source rows -> extract each into shared clusters.

    Idempotent: unchanged + already-clustered items are skipped.
    """
    results, skipped = [], 0
    for it in items:
        existing = store.get_by_url(it["url"])
        if (
            existing is not None
            and (existing.transcript or "") == it["text"]
            and existing.stage in ("extracted", "verified")
        ):
            skipped += 1
            continue
        vid = store.upsert(
            it["url"], channel=channel, collection=category, title=it["title"][:200],
            transcript=it["text"], stage="transcribed", source_type=source_type,
        )
        try:
            final = await _channel.extract_and_cluster(
                store, video_id=vid, url=it["url"], transcript=it["text"],
                collection=category, verify=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] extraction failed for %s: %s", source_type, it["url"], exc)
            final = "transcribed"
        results.append({"title": it["title"], "stage": final})
    return {"items": len(items), "extracted": len(results), "skipped": skipped, "results": results}


def source_panel(title: str, source_type: str, ingest_widget: dict) -> dict:
    """Ingest form + recent items + the SHARED clusters holding >=1 item of this source."""
    store = _video._get_store()
    widgets: list[dict] = [ingest_widget]

    recent = store.list_recent_videos(limit=15, source_type=source_type)
    if recent:
        widgets.append({
            "type": "group",
            "label": "Recent",
            "count": str(len(recent)),
            "open": True,
            "widgets": [{"type": "list", "items": [
                {"title": r["title"] or r["url"], "subtitle": r["idea_text"] or r["stage"]}
                for r in recent
            ]}],
        })

    clusters = store.list_clusters(source_type=source_type)
    if not clusters:
        widgets.append({"type": "list", "items": []})
        return {"title": title, "widgets": widgets}

    all_collections = sorted(store.list_collections())
    by_collection: dict[str, list[dict]] = {}
    for c in clusters:
        by_collection.setdefault(c.get("collection") or "Uncategorised", []).append(c)
    ordered = sorted(by_collection.items(), key=lambda kv: len(kv[1]), reverse=True)
    for gi, (collection, members) in enumerate(ordered):
        children: list[dict] = []
        for c in members:
            n = c["member_count"]
            parts = [f"{n} item{'s' if n != 1 else ''}", _video.verdict_label(c["verdict"])]
            if c["confidence"] is not None:
                parts.append(f"{c['confidence']:.0%}")
            if c.get("memory_id"):
                parts.append("✓ Memory")
            children.append({
                "type": "cluster",
                "cluster_id": c["id"],
                "label": c["label"],
                "stats": " · ".join(parts),
                "collection": collection,
                "collections": all_collections,
                "move_tool": "video_move_cluster",
            })
            if c["verdict"] and not c.get("memory_id"):
                children.append({
                    "type": "action",
                    "id": f"{source_type}-commit-{c['id']}",
                    "label": f"Commit {c['label']} to Memory",
                    "tool": "video_commit",
                    "tool_args": {"cluster_id": c["id"]},
                })
        widgets.append({
            "type": "group",
            "label": collection[:1].upper() + collection[1:],
            "collection": collection,
            "count": f"{len(members)} cluster{'s' if len(members) != 1 else ''}",
            "open": gi == 0,
            "widgets": children,
        })
    return {"title": title, "widgets": widgets}


class WebIngestPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="webpage_ingest",
                description=(
                    "Read one or more public webpages (articles, docs, blog posts) and "
                    "extract an idea from each into a collection (category), filed "
                    "alongside video/GitHub/book ideas. No key -- a plain page fetch. "
                    "For Reddit links use reddit_ingest instead. Blank category -> "
                    "'Uncategorised'. Idempotent: unchanged pages are skipped."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "One or more page URLs (space/comma/newline separated).",
                        },
                        "category": {
                            "type": "string",
                            "description": "Collection to file the ideas under. Blank -> 'Uncategorised'.",
                        },
                    },
                    "required": ["url"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "webpage_ingest":
            return await self._webpage_ingest(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    def panel_spec(self, profile_id: "int | None" = None) -> dict:  # noqa: ARG002
        return source_panel("Webpage", "webpage", {
            "type": "action",
            "id": "webpage-ingest",
            "label": "Read page",
            "tool": "webpage_ingest",
            "tool_args": {},
            "input_arg": "url",
            "input_placeholder": "https://... (one or more page URLs)",
            "input_arg2": "category",
            "input_placeholder2": "collection (blank = Uncategorised)",
        })

    async def _webpage_ingest(self, args: dict) -> ToolResult:
        urls = split_urls(args.get("url") or "")
        category = (args.get("category") or "").strip() or "Uncategorised"
        if not urls:
            return ToolResult(content="url is required", is_error=True)
        store = _video._get_store()
        out = []
        for url in urls:
            if not url.lower().startswith(("http://", "https://")):
                out.append({"url": url, "error": "Not an http(s) URL"})
                continue
            try:
                page = _ws.fetch_webpage(url)
            except Exception as exc:  # noqa: BLE001
                out.append({"url": url, "error": f"Fetch failed: {exc}"})
                continue
            if len(page["text"].split()) < MIN_WORDS:
                out.append({"url": url, "error": "Page has almost no readable text (login wall or JS-only page?)"})
                continue
            res = await ingest_items(
                store, [{"url": url, "title": page["title"] or url, "text": page["text"]}],
                source_type="webpage", channel=urllib.parse.urlsplit(url).netloc, category=category,
            )
            out.append({"url": url, "title": page["title"], **res})
        is_error = all("error" in r for r in out)
        return ToolResult(content=json.dumps({"collection": category, "pages": out}), is_error=is_error)


def create() -> WebIngestPlugin:
    return WebIngestPlugin()
