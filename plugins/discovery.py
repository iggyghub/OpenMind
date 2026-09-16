"""Discovery plugin -- MCP tools for the autonomous discovery loop.
Extracted from plugins/scheduler.py per SCHEDULER-SPLIT.md S3 (#1211).
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading.discovery import (
    DiscoveryAttempts,
    DiscoveryWatchlist,
    _KNOWN_TICKERS,
    build_dynamic_universe,
    rank_for_day_trading,
    run_discovery_pass,
)
from cerebral.trading_ideas import Idea, judge_idea as _judge_idea
from cerebral.settings import SettingsStore
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "discovery"

_DEFAULT_DB = data_dir() / "openmind.db"

# fs_read: get_discovery_status reads settings; fs_write: start/stop_discovery
# mutate discovery_enabled and related keys in felix-settings.json.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})


class DiscoveryPlugin:
    name = PLUGIN_NAME

    # S27 (#880): the one recurring event title that means "run the discovery
    # loop", not a strategy dispatch.  Stays here (not on SchedulerPlugin) per
    # SCHEDULER-SPLIT.md S3 (#1211).
    DISCOVERY_EVENT_TITLE = "__autonomous_discovery__"

    def __init__(self, db_path=None, router=None, web_search_fn=None,
                 record_activity_fn=None, discovery_watchlist=None,
                 discovery_attempts=None, settings=None, scheduler=None):
        # scheduler: the live SchedulerPlugin instance whose events table
        # ensure_discovery_event() writes to and whose _run_gauntlet()
        # _run_discovery() dispatches through.
        self._scheduler = scheduler
        self._router = router
        self._web_search_fn = web_search_fn
        self._record_activity_fn = record_activity_fn

        path = db_path if db_path is not None else str(_DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        # Same isolation convention as SchedulerPlugin -- a tmp_path-scoped
        # DiscoveryPlugin(db_path=...) gets isolated watchlist/attempts DBs.
        if discovery_watchlist is not None:
            self._discovery_watchlist = discovery_watchlist
        elif path == ":memory:":
            self._discovery_watchlist = DiscoveryWatchlist(db_path=Path(":memory:"))
        else:
            self._discovery_watchlist = DiscoveryWatchlist(
                db_path=Path(path).parent / "discovery_watchlist.db"
            )

        if discovery_attempts is not None:
            self._discovery_attempts = discovery_attempts
        elif path == ":memory:":
            self._discovery_attempts = DiscoveryAttempts(db_path=Path(":memory:"))
        else:
            self._discovery_attempts = DiscoveryAttempts(
                db_path=Path(path).parent / "discovery_attempts.db"
            )

        if settings is not None:
            self._settings = settings
        elif path == ":memory:":
            self._settings = SettingsStore(path=Path(":memory:"))
        else:
            self._settings = SettingsStore(path=Path(path).parent / "felix-settings.json")

        # Wired post-construction by main.py (TradingStrategiesPlugin).
        self._gauntlet = None

    def list_tools(self):
        return [
            Tool(
                name="run_discovery",
                description=(
                    "S27/#880: one autonomous discovery-loop pass. Sources ideas via "
                    "web_search, screens pattern-general ones through judge_idea and the "
                    "growing ticker watchlist (ticker-specific ideas skip screening), and "
                    "dispatches accepted candidates to run_gauntlet with origin='discovered'. "
                    "Normally triggered by its own recurring scheduler event, not called "
                    "directly by the model."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Web-search queries to source ideas from (default: a day-trading-focused set).",
                        },
                        "interval": {
                            "type": "string",
                            "description": "Bar interval every dispatched candidate is backtested at, e.g. '15m', '5m', '1d' (default: '15m').",
                        },
                    },
                },
            ),
            Tool(
                name="start_discovery",
                description=(
                    "S31/#896: manually enable the discovery loop (default OFF -- it does "
                    "not run on its own until this is called). Pass duration_hours to "
                    "auto-stop after that many hours; omit it to run until stop_discovery is "
                    "called. Pass queries/interval to override run_discovery's own built-in "
                    "defaults for every subsequent pass (e.g. to focus on day-trading "
                    "strategies specifically) -- omitted or empty leaves the current stored "
                    "value unchanged, it does not reset to the built-in default."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Override the queries used for every future discovery pass.",
                        },
                        "interval": {
                            "type": "string",
                            "description": "Override the bar interval used for every future discovery pass.",
                        },
                        "duration_hours": {
                            "type": "number",
                            "description": "Auto-stop after this many hours. Omit to run indefinitely.",
                        },
                        "candidate_limit": {
                            "type": "integer",
                            "description": (
                                "How many candidate tickers a single accepted pattern-general "
                                "idea is tested against per pass (default 10). Omit to leave "
                                "the current stored value unchanged."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="stop_discovery",
                description="S31/#896: immediately disable the discovery loop and clear any auto-stop timer.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_discovery_status",
                description="S31/#896: current discovery enabled/stop_at/queries/interval state.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_discovery_source_performance",
                description="Rolls up discovery attempt outcomes by source domain. Returns {domain: {validated, unvalidated, total}}.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "run_discovery":
            return await self._run_discovery(args)
        if tool_name == "start_discovery":
            return self._start_discovery(args)
        if tool_name == "stop_discovery":
            return self._stop_discovery(args)
        if tool_name == "get_discovery_status":
            return self._get_discovery_status(args)
        if tool_name == "get_discovery_source_performance":
            return self._get_discovery_source_performance(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def ensure_discovery_event(self, recurrence: str = "1h") -> None:
        """Idempotent get-or-create for the one recurring discovery event.
        Delegates to the scheduler's events table (stays on SchedulerPlugin per
        SCHEDULER-SPLIT.md SAFETY; this plugin just owns the title constant)."""
        sched = self._scheduler
        if sched is None:
            logger.warning("[discovery] ensure_discovery_event called with no scheduler attached")
            return
        existing = sched._con.execute(
            "SELECT id FROM events WHERE title = ?", (self.DISCOVERY_EVENT_TITLE,)
        ).fetchone()
        if existing is not None:
            return
        sched._create_event({
            "title": self.DISCOVERY_EVENT_TITLE,
            "start_iso": datetime.now(timezone.utc).isoformat(),
            "recurrence": recurrence,
        })

    # ------------------------------------------------------------------
    # S27 (#880): autonomous discovery loop
    # ------------------------------------------------------------------

    async def _source_ideas(self, queries: list[str]) -> list["Idea"]:
        """web_search each query, turn hits into Ideas."""
        if self._web_search_fn is not None:
            search = self._web_search_fn
        else:
            from plugins.browser import BrowserPlugin
            browser = BrowserPlugin()

            async def search(query: str) -> list[dict]:
                result = await browser.call_tool("web_search", {"query": query, "max_results": 3})
                if result.is_error:
                    logger.warning("[discovery] web_search failed for %r: %s", query, result.content)
                    return []
                data = json.loads(result.content)
                hits = data.get("results", data) if isinstance(data, dict) else data
                return hits if isinstance(hits, list) else []

        ideas: list[Idea] = []
        for query in queries:
            try:
                hits = await search(query)
            except Exception as exc:
                logger.warning("[discovery] sourcing failed for %r: %s", query, exc)
                continue
            for hit in hits:
                url = hit.get("url") or hit.get("source_url")
                title = hit.get("title") or hit.get("page_title") or ""
                snippet = hit.get("snippet") or hit.get("text") or title
                if not snippet:
                    continue
                ideas.append(Idea(
                    source_url=url, page_title=title, claim_text=snippet,
                    provenance=f"url: {url}" if url else f"web_search: {query}",
                    author_claim_text=f"Author claims: {snippet}",
                ))
        return ideas

    async def _run_discovery(
        self, args: dict, *, strategy_store=None, fetch=None, broker=None
    ) -> ToolResult:
        """The discovery loop's one trigger: source -> screen -> dispatch.
        `strategy_store`/`fetch` are test-only injection seams threaded
        through to _run_gauntlet via self._scheduler._run_gauntlet."""
        queries = args.get("queries") or [
            "day trading strategy 5 minute chart backtest",
            "intraday scalping strategy that actually works",
            "site:fool.com top stock picks this week",
            "site:benzinga.com analyst top stock picks",
            "site:marketwatch.com stocks to watch this week",
            "site:zacks.com top stock picks",
            "site:cnbc.com stocks to watch",
        ]
        interval = args.get("interval") or "15m"

        async def run_gauntlet_fn(idea: Idea, ticker: str) -> dict:
            gauntlet_args = {
                "symbol": ticker,
                "hypothesis": idea.claim_text or "discovered hypothesis",
                "provenance": idea.provenance,
                "interval": idea.interval or interval,
            }
            if idea.source_url:
                gauntlet_args["url"] = idea.source_url
            result = await self._gauntlet._run_gauntlet(
                gauntlet_args, origin="discovered",
                strategy_store=strategy_store, fetch=fetch,
            )
            return {"ticker": ticker, "is_error": result.is_error, "result": result.content}

        async def judge_idea_fn(idea: Idea) -> "tuple[bool, str]":
            return await _judge_idea(idea, router=self._router)

        fetch_fn = fetch
        if fetch_fn is None:
            from cerebral.trading_data import fetch_ohlcv as fetch_fn

        def rank_fn(symbols: list) -> list:
            return rank_for_day_trading(symbols, fetch_fn)

        # DD3 (#1159): lazy-broker convention -- no broker reference held
        # on this plugin, so construct one only when no fake was injected.
        broker_obj = broker
        if broker_obj is None:
            from cerebral.trading.broker import AlpacaBrokerClient
            broker_obj = AlpacaBrokerClient(env="paper")
        known_tickers = set(build_dynamic_universe(broker_obj, fetch_fn))

        record_activity_fn = self._record_activity_fn

        async def record_attempt_fn(entry: dict) -> None:
            self._discovery_attempts.record(
                entry["symbol"], entry["verdict"],
                reason=entry.get("reason", ""), idea_url=entry.get("idea_url", ""),
            )

        try:
            ideas = await self._source_ideas(queries)
        except Exception as exc:
            logger.exception("[discovery] sourcing failed entirely: %s", exc)
            return ToolResult(content=f"Discovery sourcing failed: {exc}", is_error=True)

        candidate_limit = self._settings.get("discovery_candidate_limit")
        results = await run_discovery_pass(
            ideas,
            self._discovery_watchlist,
            run_gauntlet_fn,
            judge_idea_fn=judge_idea_fn,
            record_activity_fn=record_activity_fn,
            record_attempt_fn=record_attempt_fn,
            rank_fn=rank_fn,
            candidate_limit=candidate_limit,
            known_tickers=known_tickers,
        )
        return ToolResult(content=json.dumps({
            "sourced": len(ideas), "dispatched": len(results),
        }))

    # ------------------------------------------------------------------
    # S31 (#896): manual discovery start/stop + duration
    # ------------------------------------------------------------------

    def _start_discovery(self, args: dict) -> ToolResult:
        queries = args.get("queries") or []
        interval = (args.get("interval") or "").strip()
        duration_hours = args.get("duration_hours")
        candidate_limit = args.get("candidate_limit")

        self._settings.set("discovery_enabled", True)
        if duration_hours is not None:
            stop_at = (datetime.now(timezone.utc) + timedelta(hours=float(duration_hours))).isoformat()
            self._settings.set("discovery_stop_at", stop_at)
        else:
            self._settings.set("discovery_stop_at", "")
        if queries:
            self._settings.set("discovery_queries", list(queries))
        if interval:
            self._settings.set("discovery_interval", interval)
        if candidate_limit is not None:
            self._settings.set("discovery_candidate_limit", int(candidate_limit))

        return self._get_discovery_status({})

    def _stop_discovery(self, args: dict) -> ToolResult:
        self._settings.set("discovery_enabled", False)
        self._settings.set("discovery_stop_at", "")
        return self._get_discovery_status({})

    def _get_discovery_status(self, args: dict) -> ToolResult:
        return ToolResult(content=json.dumps({
            "enabled": self._settings.get("discovery_enabled"),
            "stop_at": self._settings.get("discovery_stop_at"),
            "queries": self._settings.get("discovery_queries"),
            "interval": self._settings.get("discovery_interval"),
            "candidate_limit": self._settings.get("discovery_candidate_limit"),
            "scheduler_heartbeat": self._settings.get("scheduler_heartbeat"),
        }))

    def _get_discovery_source_performance(self, args: dict) -> ToolResult:
        return ToolResult(content=json.dumps(self._discovery_attempts.get_source_performance()))


def create() -> DiscoveryPlugin:
    return DiscoveryPlugin()
