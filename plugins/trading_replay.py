"""Historical Replay tools (RP6, REPLAY.md).

Introspection (`list_strategies`) and the replay entry points
(`simulate_period`, `replay_report`) over the RP1-RP5 replay engine. Kept as
its own plugin rather than added to plugins/scheduler.py, which already
hosts more trading tools than its own stale docstring admits (see REPLAY.md
D8) -- extracting THAT is a separate, larger job, not done here.

No planner change needed: cerebral/llm/planner.py's _build_tool_catalog
already injects every registered tool's name + one-liner into the system
prompt automatically, so registering these tools here IS the introspection
wiring.

REQUIRED_CAPABILITIES mirrors plugins/scheduler.py's own declaration for
the same shape of work (reading/writing local SQLite stores, fetching
market data over the network via run_replay -> bar_cache) -- scheduler.py's
run_gauntlet does the same kind of fetch and only declares fs_read/fs_write,
so this does too rather than inventing a stricter policy for identical work.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Optional

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading import bar_cache
from cerebral.trading.discovery import DiscoveryWatchlist
from cerebral.trading.strategy_store import StrategyStore
from cerebral.trading.replay import run_replay
from cerebral.trading.replay_store import ReplayStore

logger = logging.getLogger(__name__)

PLUGIN_NAME = "trading_replay"

REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})


class TradingReplayPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="list_strategies",
                description=(
                    "List Felix's registered trading strategies, optionally "
                    "filtered by symbol and/or interval. Read-only "
                    "introspection over StrategyStore -- returns id/symbol/"
                    "interval/qty per strategy."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Filter to one ticker symbol."},
                        "interval": {"type": "string", "description": "Filter to one bar interval, e.g. '1d'."},
                    },
                },
            ),
            Tool(
                name="simulate_period",
                description=(
                    "Replay strategies against cached historical bars over "
                    "[start, end] and record results. Reuses the exact "
                    "vectorized execution path live/backtest already use -- "
                    "reads history and writes a results row per strategy, "
                    "places no order, real or paper. Returns a run_id plus a "
                    "compact summary."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "start": {"type": "string", "description": "Window start date, YYYY-MM-DD."},
                        "end": {"type": "string", "description": "Window end date, YYYY-MM-DD."},
                        "symbols": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Restrict to these symbols (default: all registered strategies).",
                        },
                        "interval": {"type": "string", "description": "Restrict to this bar interval."},
                    },
                    "required": ["start", "end"],
                },
            ),
            Tool(
                name="replay_report",
                description=(
                    "Full per-strategy results for a previous simulate_period "
                    "run, plus a flat_reason census -- how many strategies "
                    "failed with which error signature, grouped by prefix."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string", "description": "run_id returned by simulate_period."},
                    },
                    "required": ["run_id"],
                },
            ),
            Tool(
                name="start_cache_warm",
                description=(
                    "Start a sequential background batch job to warm the "
                    "intraday/daily bar cache for a universe of symbols. "
                    "Defaults to interval='1d' and a computed universe "
                    "(live strategies + discovery watchlist). Returns a "
                    "status message. Use stop_cache_warm to halt it."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "interval": {
                            "type": "string",
                            "description": "Bar interval, e.g. '1d' or '15m'. Default '1d'.",
                        },
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional explicit symbols (defaults to universe).",
                        },
                    },
                },
            ),
            Tool(
                name="stop_cache_warm",
                description=(
                    "Signal the running cache-warm background task to finish "
                    "its current symbol and stop. Returns a status message."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "list_strategies":
            return self._list_strategies(args)
        if tool_name == "simulate_period":
            return self._simulate_period(args)
        if tool_name == "replay_report":
            return self._replay_report(args)
        if tool_name == "start_cache_warm":
            return ToolResult(content=await start_cache_warm(args.get("interval", "1d"), args.get("symbols")))
        if tool_name == "stop_cache_warm":
            return ToolResult(content=await stop_cache_warm())
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _list_strategies(self, args: dict) -> ToolResult:
        symbol: Optional[str] = args.get("symbol")
        interval: Optional[str] = args.get("interval")

        # Matches plugins/scheduler.py's own convention: StrategyStore() is a
        # cheap per-call SQLite connection, never explicitly closed (relies
        # on GC) -- not held across calls, so closing it here would be wrong
        # for the same reason scheduler.py doesn't: nothing owns it beyond
        # this one call, and closing a caller-injected instance (as tests do)
        # would break that instance for its next use.
        store = StrategyStore()
        specs = store.list_all()

        rows = [
            {"id": s.strategy_id, "symbol": s.symbol, "interval": s.interval, "qty": s.qty}
            for s in specs
            if (symbol is None or s.symbol == symbol) and (interval is None or s.interval == interval)
        ]
        return ToolResult(content=json.dumps(rows))

    def _simulate_period(self, args: dict) -> ToolResult:
        start = args.get("start")
        end = args.get("end")
        if not start or not end:
            return ToolResult(content="'start' and 'end' are required", is_error=True)
        symbols: Optional[list] = args.get("symbols")
        interval: Optional[str] = args.get("interval")

        store = StrategyStore()
        specs = [
            s for s in store.list_all()
            if (symbols is None or s.symbol in symbols)
            and (interval is None or s.interval == interval)
        ]

        run_id = run_replay(specs, start, end)

        replay_store = ReplayStore()
        results = replay_store.get_results(run_id)

        flat_reason_count = sum(1 for r in results if r["flat_reason"])
        returns = [r["net_return"] for r in results if r["net_return"] is not None]
        mean_return = sum(returns) / len(returns) if returns else 0.0

        return ToolResult(content=json.dumps({
            "run_id": run_id,
            "total_strategies_replayed": len(specs),
            "flat_reason_count": flat_reason_count,
            "aggregate_mean_net_return": mean_return,
        }))

    def _replay_report(self, args: dict) -> ToolResult:
        run_id = args.get("run_id")
        if not run_id:
            return ToolResult(content="'run_id' is required", is_error=True)

        replay_store = ReplayStore()
        try:
            run = replay_store.get_run(run_id)
            if run is None:
                return ToolResult(content=f"No such run: {run_id!r}", is_error=True)
            results = replay_store.get_results(run_id)
        except Exception as exc:
            logger.warning("[trading_replay] replay_report failed for %s: %s", run_id, exc, exc_info=True)
            return ToolResult(content=f"Could not read run {run_id!r}: {exc}", is_error=True)

        census: dict[str, int] = {}
        rows = []
        for r in results:
            reason = r["flat_reason"]
            if reason:
                prefix = reason.split(":")[0].strip()
                census[prefix] = census.get(prefix, 0) + 1
            rows.append({
                "strategy_id": r["strategy_id"],
                "symbol": r["symbol"],
                "gross_return": r["gross_return"],
                "net_return": r["net_return"],
                "n_trades": r["n_trades"],
                "max_drawdown": r["max_drawdown"],
                "sharpe": r["sharpe"],
                "flat_reason": reason,
            })

        return ToolResult(content=json.dumps({
            "run_id": run_id,
            "total_strategies": len(rows),
            "rows": rows,
            "flat_reason_census": census,
        }))


# Background task state for cache warm
_cache_warm_task: Optional[asyncio.Task] = None
_cache_warm_stop_flag = False


def _compute_universe(symbols: Optional[list[str]]) -> list[str]:
    if symbols is not None:
        return list(symbols)
    strat_syms = {s.symbol for s in StrategyStore().list_all()}
    # symbols() is an instance method, not a classmethod/staticmethod --
    # DiscoveryWatchlist.symbols() on the bare class raises TypeError
    # (missing self). It also returns a List[str], not a set, so `|` needs
    # both sides as sets.
    disc_syms = set(DiscoveryWatchlist().symbols())
    return sorted(strat_syms | disc_syms)


async def start_cache_warm(interval: str = "1d", symbols: Optional[list[str]] = None) -> str:
    global _cache_warm_task, _cache_warm_stop_flag
    if _cache_warm_task is not None and not _cache_warm_task.done():
        return "Cache warm already running."
    _cache_warm_stop_flag = False
    _cache_warm_task = asyncio.create_task(_run_cache_warm(interval, symbols))
    return "Cache warm started."


async def stop_cache_warm() -> str:
    global _cache_warm_task
    if _cache_warm_task is None or _cache_warm_task.done():
        return "No cache warm running."
    global _cache_warm_stop_flag
    _cache_warm_stop_flag = True
    await _cache_warm_task
    return "Cache warm stopped."


async def _run_cache_warm(interval: str, symbols: Optional[list[str]]) -> None:
    default_start = "2016-01-01"
    today = datetime.date.today().isoformat()

    universe = _compute_universe(symbols)

    loop = asyncio.get_event_loop()
    for symbol in universe:
        if _cache_warm_stop_flag:
            break
        # ponytail: fixed 3 attempts, no jitter, no framework — upgrade to a real backoff library only if this measurably proves insufficient
        for attempt in range(3):
            try:
                # bar_cache.get_bars is a plain synchronous function (sqlite
                # + a real network call) -- calling it directly here would
                # block the WHOLE event loop for its duration on every
                # fetch, exactly the class of bug ADR-0026 already recorded
                # as a real live incident (a book-ingestion stall). Running
                # it in the default executor keeps this coroutine
                # cooperative even though the underlying call isn't.
                await loop.run_in_executor(None, bar_cache.get_bars, symbol, default_start, today, interval)
                break
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.warning("Cache warm failed for %s after 3 attempts", symbol)
        await asyncio.sleep(0)  # yield control between symbols


def create() -> TradingReplayPlugin:
    return TradingReplayPlugin()
