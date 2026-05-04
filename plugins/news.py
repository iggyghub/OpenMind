"""
News MCP plugin — Issue #25.

Tools: news_headlines, news_list_sources.

Aggregates configurable RSS feeds. Default sources (BBC, Reuters, Hacker News)
are injected at construction. RSS parsing is delegated to feedparser via an
injectable parse_fn so tests run without network or feedparser installed.

The default parse_fn imports feedparser lazily — `pip install feedparser>=6.0`
to enable.
"""
import json
import logging
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "news"
_DEFAULT_LIMIT = 10

_DEFAULT_SOURCES: dict[str, str] = {
    "BBC": "http://feeds.bbci.co.uk/news/rss.xml",
    "Reuters": "https://www.reutersagency.com/feed/",
    "Hacker News": "https://hnrss.org/frontpage",
}

ParseFn = Callable[[str], object]


def _default_parse(url: str):
    """Lazy-import feedparser. Raises ImportError with install hint if missing."""
    try:
        import feedparser  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "feedparser is required for the News plugin — pip install feedparser>=6.0"
        ) from exc
    return feedparser.parse(url)


def _entry_field(entry, field: str, default: str = "") -> str:
    """feedparser entries may be dict-like or attr-like — read either form."""
    if isinstance(entry, dict):
        return entry.get(field, default)
    return getattr(entry, field, default)


class NewsPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        sources: dict[str, str] | None = None,
        parse_fn: ParseFn | None = None,
    ) -> None:
        self._sources = dict(sources) if sources is not None else dict(_DEFAULT_SOURCES)
        self._parse = parse_fn or _default_parse

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="news_headlines",
                description=(
                    "Fetch top news headlines from configured RSS feeds. "
                    "Optionally filter by topic substring or by source name."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Substring filter (case-insensitive) on title and summary.",
                        },
                        "source": {
                            "type": "string",
                            "description": "Restrict to a single source name.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum headlines to return (default {_DEFAULT_LIMIT}).",
                        },
                    },
                },
            ),
            Tool(
                name="news_list_sources",
                description="List configured RSS news sources.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "news_headlines":
            return await self._headlines(args)
        if tool_name == "news_list_sources":
            return await self._list_sources(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    async def _list_sources(self, args: dict) -> ToolResult:
        return ToolResult(content=json.dumps({
            "sources": [
                {"name": name, "url": url}
                for name, url in self._sources.items()
            ]
        }))

    async def _headlines(self, args: dict) -> ToolResult:
        source_filter = args.get("source")
        topic = (args.get("topic") or "").lower()
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)

        if source_filter and source_filter not in self._sources:
            return ToolResult(
                content=f"Unknown source: '{source_filter}'", is_error=True
            )

        target_sources = (
            {source_filter: self._sources[source_filter]}
            if source_filter
            else self._sources
        )

        headlines: list[dict] = []
        successes = 0
        failures = 0
        for name, url in target_sources.items():
            try:
                feed = self._parse(url)
            except Exception as exc:
                logger.warning("[news] feed '%s' failed: %s", name, exc)
                failures += 1
                continue
            successes += 1
            entries = getattr(feed, "entries", []) or []
            for entry in entries:
                title = _entry_field(entry, "title")
                summary = _entry_field(entry, "summary")
                if topic and topic not in title.lower() and topic not in summary.lower():
                    continue
                headlines.append({
                    "title": title,
                    "url": _entry_field(entry, "link"),
                    "summary": summary,
                    "published": _entry_field(entry, "published"),
                    "source": name,
                })

        if successes == 0 and failures > 0:
            return ToolResult(
                content="All news sources failed to load",
                is_error=True,
            )

        return ToolResult(content=json.dumps({
            "headlines": headlines[:max_results],
        }))


def create(
    sources: dict[str, str] | None = None,
    parse_fn: ParseFn | None = None,
) -> NewsPlugin:
    return NewsPlugin(sources=sources, parse_fn=parse_fn)
