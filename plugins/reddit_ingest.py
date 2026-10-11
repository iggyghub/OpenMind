"""Reddit ingest MCP plugin -- the Library's "Reddit" tab.

reddit_ingest(url, category): Reddit is a post + comment thread, not a page,
so it gets its own reader (cerebral.video.web_source.fetch_reddit, public Atom
feeds, no key). A thread link -> one item (post + comments); a subreddit link
-> one item per recent post (bodies only). Items ride the shared
extract/cluster spine as source_type='reddit', channel='r/<sub>'.

Unauthenticated Reddit allows ~1 request/minute; each call here is exactly one
request, and a 429 comes back as a plain "try again in a minute".
"""
from __future__ import annotations

import json
import urllib.error

import plugins.video as _video
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import web_source as _ws
from plugins.web_ingest import ingest_items, source_panel, split_urls

PLUGIN_NAME = "reddit_ingest"

REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"external_data_read"})


class RedditIngestPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="reddit_ingest",
                description=(
                    "Read Reddit and extract ideas into a collection (category). A thread "
                    "link (reddit.com/r/<sub>/comments/...) reads the post plus its "
                    "comments as one item; a subreddit link (reddit.com/r/<sub>) reads its "
                    "recent posts, one item each. No key. Reddit rate-limits to about one "
                    "link per minute. Blank category -> 'Uncategorised'."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "A Reddit thread or subreddit URL."},
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
        if tool_name == "reddit_ingest":
            return await self._reddit_ingest(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    def panel_spec(self, profile_id: "int | None" = None) -> dict:  # noqa: ARG002
        return source_panel("Reddit", "reddit", {
            "type": "action",
            "id": "reddit-ingest",
            "label": "Read Reddit",
            "tool": "reddit_ingest",
            "tool_args": {},
            "input_arg": "url",
            "input_placeholder": "reddit.com/r/<sub> or a thread link",
            "input_arg2": "category",
            "input_placeholder2": "collection (blank = Uncategorised)",
        })

    async def _reddit_ingest(self, args: dict) -> ToolResult:
        urls = split_urls(args.get("url") or "")
        category = (args.get("category") or "").strip() or "Uncategorised"
        if len(urls) != 1:
            return ToolResult(content="Give exactly one Reddit link (Reddit allows ~1 request/minute).", is_error=True)
        parsed = _ws.parse_reddit_url(urls[0])
        if parsed is None:
            return ToolResult(content="Not a Reddit URL -- expected reddit.com/r/<sub> or a /comments/ thread link.", is_error=True)
        try:
            items = _ws.fetch_reddit(urls[0])
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return ToolResult(content="Reddit rate limit hit -- try again in about a minute.", is_error=True)
            return ToolResult(content=f"Reddit fetch failed: HTTP {exc.code}", is_error=True)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(content=f"Reddit fetch failed: {exc}", is_error=True)
        res = await ingest_items(
            _video._get_store(), items, source_type="reddit",
            channel=f"r/{parsed['sub']}", category=category,
        )
        return ToolResult(content=json.dumps({"url": parsed["url"], "kind": parsed["kind"], "collection": category, **res}))


def create() -> RedditIngestPlugin:
    return RedditIngestPlugin()
