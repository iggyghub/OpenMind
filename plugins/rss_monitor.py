"""
RSS Monitor MCP plugin — Issue #91.

Tools: rss_subscribe, rss_unsubscribe, rss_list_subscriptions, rss_check.

Monitors subscribed RSS/Atom feeds and surfaces only entries *new since the
last check* — distinct from plugins/news.py, which does point-in-time
aggregation. The differentiator is a per-feed cursor (`last_seen_id`) persisted
in SQLite (the shared openmind.db), mirroring plugins/scheduler.py.

RSS parsing is delegated to feedparser via an injectable parse_fn so tests run
without network or feedparser installed. The default parse_fn imports
feedparser lazily — `pip install feedparser>=6.0` to enable.
"""
import json
import logging
import sqlite3
from pathlib import Path
from typing import Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "rss_monitor"

# ADR-0005 / Issue #44 — rss_check fetches feeds from the public internet
# (network_egress_cloud + external_data_read) and reads/writes the per-feed
# cursor in SQLite (fs_read + fs_write); subscribe/unsubscribe mutate the
# rss_feeds table (fs_write). The DB file is never unlinked, so fs_delete is
# not needed (scheduler.py precedent: row DELETE is fs_write, not fs_delete).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_read",
    "network_egress_cloud",
    "fs_read",
    "fs_write",
})

_DEFAULT_DB = Path(__file__).parent.parent / "cerebral" / "data" / "openmind.db"

_DEFAULT_MAX_NEW = 50

ParseFn = Callable[[str], object]


def _default_parse(url: str):
    """Lazy-import feedparser. Raises ImportError with install hint if missing.

    The import MUST stay inside this function — module top-level execution
    happens in the plugin-audit fan-out (test_orchestrator exec_module) where
    feedparser is not installed.
    """
    try:
        import feedparser  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "feedparser is required for the RSS Monitor plugin — "
            "pip install feedparser>=6.0"
        ) from exc
    return feedparser.parse(url)


def _entry_field(entry, field: str, default: str = "") -> str:
    """feedparser entries may be dict-like or attr-like — read either form."""
    if isinstance(entry, dict):
        return entry.get(field, default)
    return getattr(entry, field, default)


def _entry_key(entry) -> str:
    """Stable per-entry identity: id (Atom <id> / RSS <guid>, normalised by
    feedparser) → link → title. First non-empty wins."""
    for field in ("id", "link", "title"):
        value = _entry_field(entry, field)
        if value:
            return value
    return ""


class RSSMonitorPlugin:
    name = PLUGIN_NAME

    def __init__(self, db_path=None, parse_fn: ParseFn | None = None) -> None:
        path = db_path if db_path is not None else str(_DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._parse = parse_fn or _default_parse
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS rss_feeds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT     NOT NULL UNIQUE,
                url             TEXT     NOT NULL,
                last_seen_id    TEXT,
                last_checked_at DATETIME,
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._con.commit()

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="rss_subscribe",
                description=(
                    "Subscribe to an RSS/Atom feed for monitoring. The first "
                    "rss_check baselines it silently (you monitor from now on)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Unique feed name."},
                        "url": {"type": "string", "description": "Feed URL."},
                    },
                    "required": ["name", "url"],
                },
            ),
            Tool(
                name="rss_unsubscribe",
                description="Stop monitoring a feed and forget its cursor.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Feed name to remove."},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="rss_list_subscriptions",
                description="List monitored feeds and whether each is baselined.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="rss_check",
                description=(
                    "Check subscribed feeds and return only entries new since "
                    "the last check. Omit 'name' to check every feed."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Restrict to a single feed.",
                        },
                        "max_new": {
                            "type": "integer",
                            "description": f"Max new entries per feed (default {_DEFAULT_MAX_NEW}).",
                        },
                    },
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "rss_subscribe":
            return self._subscribe(args)
        if tool_name == "rss_unsubscribe":
            return self._unsubscribe(args)
        if tool_name == "rss_list_subscriptions":
            return self._list_subscriptions(args)
        if tool_name == "rss_check":
            return self._check(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    def _subscribe(self, args: dict) -> ToolResult:
        name = (args.get("name") or "").strip()
        url = (args.get("url") or "").strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        if not url:
            return ToolResult(content="url is required", is_error=True)

        try:
            cur = self._con.execute(
                "INSERT INTO rss_feeds (name, url) VALUES (?, ?)",
                (name, url),
            )
        except sqlite3.IntegrityError:
            return ToolResult(
                content=f"Feed '{name}' is already subscribed", is_error=True
            )
        self._con.commit()
        return ToolResult(content=json.dumps(
            {"id": cur.lastrowid, "name": name, "baselined": False}
        ))

    def _unsubscribe(self, args: dict) -> ToolResult:
        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)

        row = self._con.execute(
            "SELECT id FROM rss_feeds WHERE name=?", (name,)
        ).fetchone()
        if not row:
            return ToolResult(content=f"Feed '{name}' not found", is_error=True)

        self._con.execute("DELETE FROM rss_feeds WHERE name=?", (name,))
        self._con.commit()
        return ToolResult(content=json.dumps({"name": name, "deleted": True}))

    def _list_subscriptions(self, args: dict) -> ToolResult:
        rows = self._con.execute(
            "SELECT name, url, last_checked_at, last_seen_id "
            "FROM rss_feeds ORDER BY name"
        ).fetchall()
        feeds = [
            {
                "name": r["name"],
                "url": r["url"],
                "last_checked_at": r["last_checked_at"],
                "monitoring": r["last_seen_id"] is not None,
            }
            for r in rows
        ]
        return ToolResult(content=json.dumps({"feeds": feeds}))

    def _check(self, args: dict) -> ToolResult:
        name_filter = (args.get("name") or "").strip() or None
        max_new = int(args.get("max_new") or _DEFAULT_MAX_NEW)

        if name_filter:
            rows = self._con.execute(
                "SELECT * FROM rss_feeds WHERE name=?", (name_filter,)
            ).fetchall()
            if not rows:
                return ToolResult(
                    content=f"Feed '{name_filter}' not found", is_error=True
                )
        else:
            rows = self._con.execute(
                "SELECT * FROM rss_feeds ORDER BY name"
            ).fetchall()

        results: list[dict] = []
        for row in rows:
            results.append(self._check_one(row, max_new))
        return ToolResult(content=json.dumps({"results": results}))

    def _check_one(self, row: sqlite3.Row, max_new: int) -> dict:
        name = row["name"]
        url = row["url"]
        last_seen = row["last_seen_id"]

        try:
            feed = self._parse(url)
        except Exception as exc:
            logger.warning("[rss_monitor] feed '%s' failed: %s", name, exc)
            # Failed check: advance NEITHER cursor NOR last_checked_at.
            return {"name": name, "new": [], "error": str(exc)}

        entries = list(getattr(feed, "entries", []) or [])
        newest_key = _entry_key(entries[0]) if entries else None

        # Successful parse → stamp last_checked_at regardless of new count.
        self._con.execute(
            "UPDATE rss_feeds SET last_checked_at=CURRENT_TIMESTAMP WHERE name=?",
            (name,),
        )

        if last_seen is None:
            # First check: baseline silently to the newest entry.
            if newest_key is not None:
                self._con.execute(
                    "UPDATE rss_feeds SET last_seen_id=? WHERE name=?",
                    (newest_key, name),
                )
            self._con.commit()
            return {"name": name, "new": [], "baselined": True}

        new_entries: list[dict] = []
        for entry in entries:
            if _entry_key(entry) == last_seen:
                break
            new_entries.append({
                "title": _entry_field(entry, "title"),
                "url": _entry_field(entry, "link"),
                "summary": _entry_field(entry, "summary"),
                "published": _entry_field(entry, "published"),
                "id": _entry_key(entry),
            })

        if newest_key is not None and newest_key != last_seen:
            self._con.execute(
                "UPDATE rss_feeds SET last_seen_id=? WHERE name=?",
                (newest_key, name),
            )
        self._con.commit()
        return {"name": name, "new": new_entries[:max_new]}


def create(db_path=None, parse_fn: ParseFn | None = None) -> RSSMonitorPlugin:
    return RSSMonitorPlugin(db_path=db_path, parse_fn=parse_fn)
