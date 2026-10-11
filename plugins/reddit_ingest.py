"""Reddit ingest MCP plugin -- the Library's "Reddit" tab.

reddit_ingest(url, category): Reddit is posts + comment discussions, not
pages, so it gets its own reader (cerebral.video.web_source, public Atom feeds,
no key). The field takes:

- a thread link   -> that post + its discussion (up to 100 comments), now;
- a subreddit link or ``r/<sub>`` -> its current threads;
- keywords (``breadth thrust``, or ``r/algotrading breadth thrust`` to stay in
  one subreddit) -> Reddit search's best-matching threads.

For a subreddit/search, Felix opens EACH thread and reads its whole discussion.
Unauthenticated Reddit allows ~1 request/minute, so that crawl runs in the
background, one thread per REQUEST_GAP seconds, and the panel shows progress.
Every thread is extracted into the shared clusters as soon as it is read
(source_type='reddit', channel='r/<sub>'). Threads already read are skipped
on a crawl -- paste the thread link directly to re-read one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error

import plugins.video as _video
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import web_source as _ws
from plugins.web_ingest import ingest_items, source_panel, url_and_category

logger = logging.getLogger(__name__)

PLUGIN_NAME = "reddit_ingest"

REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"external_data_read"})

REQUEST_GAP = 62          # seconds between Reddit requests (x-ratelimit: 1/min)
RETRY_429 = 3
DEFAULT_THREADS = 10
MAX_THREADS = 25

_sleep = asyncio.sleep    # tests swap in a no-op
_crawl_task: "asyncio.Task | None" = None
_CRAWL: dict = {"running": False, "label": "", "done": 0, "total": 0, "current": "", "errors": 0}


async def _fetch(url: str) -> str:
    """One paced-by-caller request; backs off and retries on 429."""
    for attempt in range(RETRY_429 + 1):
        try:
            return await asyncio.to_thread(_ws._fetch_fn, url)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == RETRY_429:
                raise
            await _sleep(REQUEST_GAP)
    raise RuntimeError("unreachable")


async def _read_thread(store, thread_url: str, sub: str, category: str) -> dict:
    item = _ws.thread_from_feed(await _fetch(_ws.thread_feed_url(thread_url)), sub)
    item["url"] = thread_url  # canonical (parse_reddit_url), so "already read" checks match
    return await ingest_items(store, [item], source_type="reddit", channel=f"r/{sub}", category=category)


async def _crawl(threads: list[dict], category: str) -> None:
    store = _video._get_store()
    try:
        for t in threads:
            _CRAWL["current"] = t["title"]
            await _sleep(REQUEST_GAP)
            try:
                await _read_thread(store, t["url"], t["sub"], category)
            except Exception as exc:  # noqa: BLE001 -- one bad thread must not end the crawl
                _CRAWL["errors"] += 1
                logger.warning("[reddit] thread failed %s: %s", t["url"], exc)
            _CRAWL["done"] += 1
    finally:
        _CRAWL.update(running=False, current="")


class RedditIngestPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="reddit_ingest",
                description=(
                    "Read Reddit discussions and extract ideas into a collection (category). "
                    "'url' takes a thread link (reads the post + up to 100 comments now), a "
                    "subreddit link or 'r/<sub>' (reads its current threads), or search "
                    "keywords, optionally 'r/<sub> keywords' (reads the best-matching "
                    "threads). For a subreddit/search Felix opens every thread and reads "
                    "its whole discussion in the background at ~1 thread/minute (Reddit's "
                    "no-account limit). No key. Blank category -> 'Uncategorised'."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Thread link, subreddit link / 'r/<sub>', or search keywords.",
                        },
                        "category": {
                            "type": "string",
                            "description": "Collection to file the ideas under. Blank -> 'Uncategorised'.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Threads to read for a subreddit/search (default {DEFAULT_THREADS}, max {MAX_THREADS}).",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="reddit_stop",
                description="Stop an in-progress Reddit crawl. Threads already read are kept.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "reddit_ingest":
            return await self._reddit_ingest(args)
        if tool_name == "reddit_stop":
            if _crawl_task is not None and not _crawl_task.done():
                _crawl_task.cancel()
            return ToolResult(content=json.dumps({"status": "stopped", **_CRAWL}))
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    def panel_spec(self, profile_id: "int | None" = None) -> dict:  # noqa: ARG002
        spec = source_panel("Reddit", "reddit", {
            "type": "action",
            "id": "reddit-ingest",
            "label": "Read Reddit",
            "tool": "reddit_ingest",
            "tool_args": {},
            "input_arg": "url",
            "input_placeholder": "Link, r/<sub>, or search words",
            "input_arg2": "category",
            "input_placeholder2": "Collection name (optional)",
        })
        if _CRAWL["running"]:
            spec["widgets"].insert(1, {"type": "list", "items": [{
                "title": f"Reading {_CRAWL['label']}: {_CRAWL['done']} / {_CRAWL['total']} threads",
                "subtitle": (f"Now: {_CRAWL['current']}" if _CRAWL["current"] else "")
                + " · about 1 thread per minute (Reddit's limit)",
            }]})
            spec["widgets"].insert(2, {
                "type": "action", "id": "reddit-stop", "label": "Stop reading",
                "tool": "reddit_stop", "tool_args": {},
            })
        return spec

    async def _reddit_ingest(self, args: dict) -> ToolResult:
        global _crawl_task
        raw, category = url_and_category(args)
        limit = max(1, min(MAX_THREADS, int(args.get("limit") or DEFAULT_THREADS)))
        target = _ws.reddit_target(raw)
        if target is None:
            return ToolResult(
                content="Give a Reddit thread link, a subreddit (r/<sub>), or search keywords.",
                is_error=True)
        store = _video._get_store()

        if target["kind"] == "thread":
            try:
                res = await _read_thread(store, target["url"], target["sub"], category)
            except urllib.error.HTTPError as exc:
                return ToolResult(content=f"Reddit fetch failed: HTTP {exc.code}", is_error=True)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(content=f"Reddit fetch failed: {exc}", is_error=True)
            return ToolResult(content=json.dumps({"url": target["url"], "collection": category, **res}))

        if _CRAWL["running"]:
            return ToolResult(
                content=f"Already reading {_CRAWL['label']} ({_CRAWL['done']}/{_CRAWL['total']}). "
                        "Stop it first or wait for it to finish.", is_error=True)
        try:
            listing = _ws.threads_in_listing(await _fetch(_ws.listing_url(target, limit)))
        except urllib.error.HTTPError as exc:
            return ToolResult(content=f"Reddit listing failed: HTTP {exc.code}", is_error=True)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(content=f"Reddit listing failed: {exc}", is_error=True)

        fresh = [t for t in listing[:limit] if store.get_by_url(t["url"]) is None]
        label = (f"r/{target['sub']}" if target["kind"] == "subreddit"
                 else f"search '{target['query']}'" + (f" in r/{target['sub']}" if target["sub"] else ""))
        if not fresh:
            return ToolResult(content=json.dumps({
                "status": "nothing_new", "label": label, "found": len(listing)}))
        _CRAWL.update(running=True, label=label, done=0, total=len(fresh), current="", errors=0)
        _crawl_task = asyncio.create_task(_crawl(fresh, category))
        return ToolResult(content=json.dumps({
            "status": "started", "label": label, "threads": len(fresh),
            "already_read": len(listing[:limit]) - len(fresh), "collection": category,
            "eta_minutes": len(fresh) + 1,
            "note": "Reading each thread's discussion in the background (~1/min). Progress shows in Library > Reddit.",
        }))


def create() -> RedditIngestPlugin:
    return RedditIngestPlugin()
