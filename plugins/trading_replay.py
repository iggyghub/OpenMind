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
import time
from typing import Optional

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading import bar_cache
from cerebral.trading.discovery import DiscoveryWatchlist
from cerebral.trading.strategy_store import StrategyStore
from cerebral.trading.replay import run_replay
from cerebral.trading.replay_store import ReplayStore
from cerebral.trading.cross_stock_replay import build_pairs, rollup_consistency, run_pair
from cerebral.trading.cross_stock_store import CrossStockStore
from cerebral.trading.cross_stock_universe import BASKET
from cerebral.settings import SettingsStore

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
            Tool(
                name="start_batch_replay",
                description=(
                    "Begin (or resume) a standing background sweep through the "
                    "strategy portfolio's full available history in 1-month "
                    "chunks. Defaults to start_date='2016-01-01'. Resumes from "
                    "the persisted cursor if a previous sweep is incomplete. "
                    "Use stop_batch_replay to halt it after the current month."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "Window start date, YYYY-MM-DD. Defaults to '2016-01-01'."},
                    },
                },
            ),
            Tool(
                name="stop_batch_replay",
                description=(
                    "Signal the running batch-replay background task to finish "
                    "its current month and stop. Returns a status message."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_batch_replay_status",
                description=(
                    "Read-only status of the batch replay sweep: running state, "
                    "cursor position, and progress."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="start_cross_stock_replay",
                description=(
                    "CROSS-STOCK-VALIDATION S2 (#1235): begin (or resume) a standing "
                    "background sweep testing every generic strategy's rule against "
                    "the full 100-stock basket (5-year window per pair) -- a "
                    "different question from batch replay's own sweep (that one "
                    "tracks one strategy against its own one registered symbol over "
                    "calendar time; this one checks whether a strategy's edge holds "
                    "up across many stocks, not just the one it happened to register "
                    "against). Resumes from the persisted (strategy, symbol) cursor "
                    "if a previous sweep is incomplete."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="stop_cross_stock_replay",
                description=(
                    "Signal the running cross-stock sweep to finish its current "
                    "pair and stop. Returns a status message."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_cross_stock_replay_status",
                description=(
                    "Read-only status of the cross-stock sweep: running state, "
                    "cursor pair, and pairs done/total."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="start_permutation_baseline",
                description=(
                    "#1251 axis 2: for every look-ahead-screened strategy, on the stocks where it "
                    "trades most, compare its return to the SAME position pattern shifted to random "
                    "dates (same trades, holding periods, exposure, 2 bps cost). Runs in the "
                    "background and resumes; results appear in get_cross_stock_replay_status. "
                    "Informational only."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="start_stress_windows",
                description=(
                    "Regime stress test: every causal daily strategy on ~30 large caps over the 2008 "
                    "crash, the 2010s, the 2022 bear and the main 5-year window (2 bps cost), scored on "
                    "return AND drawdown vs buy-and-hold. Long-history bars come from yfinance. Runs in "
                    "the background and resumes; results appear in get_cross_stock_replay_status "
                    "under `stress`. Informational only."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="start_intraday_research",
                description=(
                    "Day-trading research: eight hand-authored 5-minute rules (opening-range breakout, VWAP "
                    "reversion/trend, gap-and-go, gap fade, intraday Bollinger, half-hour momentum, EMA+VWAP) on "
                    "~30 large caps over 2020-today (regular session, flat by the close, 2 bps cost), scored "
                    "per regime on absolute net return. Fetches Alpaca 5m bars, runs in the background and "
                    "resumes; results appear in get_cross_stock_replay_status under `intraday`. "
                    "Informational only."
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
        if tool_name == "start_batch_replay":
            return ToolResult(content=await start_batch_replay(args.get("start_date")))
        if tool_name == "stop_batch_replay":
            return ToolResult(content=await stop_batch_replay())
        if tool_name == "get_batch_replay_status":
            return ToolResult(content=await get_batch_replay_status())
        if tool_name == "start_cross_stock_replay":
            return ToolResult(content=await start_cross_stock_replay())
        if tool_name == "stop_cross_stock_replay":
            return ToolResult(content=await stop_cross_stock_replay())
        if tool_name == "get_cross_stock_replay_status":
            return ToolResult(content=await get_cross_stock_replay_status())
        if tool_name == "start_permutation_baseline":
            return ToolResult(content=await start_permutation_baseline())
        if tool_name == "start_stress_windows":
            return ToolResult(content=await start_stress_windows())
        if tool_name == "start_intraday_research":
            return ToolResult(content=await start_intraday_research())
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
                "news_event_count": r["news_event_count"],
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

# Background task state for the batch-replay sweep
_batch_replay_task: Optional[asyncio.Task] = None
_batch_replay_stop_flag = False


def _rollup_worst_drawdowns() -> None:
    """BATCH-REPLAY S4 (#1227): recompute every strategy's worst
    accumulated-replay drawdown and persist it onto its StrategyStore spec.
    Synchronous (called via run_in_executor from the async batch loop) --
    plain sqlite calls, no I/O worth making async."""
    worst_by_strategy = ReplayStore().get_worst_drawdown_by_strategy()
    store = StrategyStore()
    for strategy_id, worst in worst_by_strategy.items():
        store.update_worst_drawdown(strategy_id, worst)


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


async def start_batch_replay(start_date: Optional[str] = None) -> str:
    global _batch_replay_task, _batch_replay_stop_flag
    if start_date is None:
        start_date = "2016-01-01"
    if _batch_replay_task is not None and not _batch_replay_task.done():
        return "Batch replay already running."
    _batch_replay_stop_flag = False
    _batch_replay_task = asyncio.create_task(_run_batch_replay(start_date))
    return "Batch replay started."


async def stop_batch_replay() -> str:
    global _batch_replay_task
    if _batch_replay_task is None or _batch_replay_task.done():
        return "No batch replay running."
    global _batch_replay_stop_flag
    _batch_replay_stop_flag = True
    try:
        await asyncio.wait_for(asyncio.shield(_batch_replay_task), timeout=_CROSS_STOCK_STOP_TIMEOUT_S)
    except asyncio.TimeoutError:
        _batch_replay_task.cancel()
        try:
            await _batch_replay_task
        except asyncio.CancelledError:
            pass
        logger.warning(
            "[trading_replay] Batch replay did not stop in %.0fs after stop flag set; cancelled.",
            _CROSS_STOCK_STOP_TIMEOUT_S,
        )
    return "Batch replay stopped."


async def get_batch_replay_status() -> str:
    settings = SettingsStore()
    running = settings.get("batch_replay_running") or False
    cursor = settings.get("batch_replay_cursor") or "N/A"
    start = settings.get("batch_replay_start") or "N/A"
    today = datetime.date.today().isoformat()
    
    try:
        start_dt = datetime.date.fromisoformat(start) if start != "N/A" else datetime.date.today()
        cursor_dt = datetime.date.fromisoformat(cursor) if cursor != "N/A" else start_dt
        total_months = int((datetime.date.today() - start_dt).days / 30) + 1
        done_months = int((cursor_dt - start_dt).days / 30)
    except (ValueError, TypeError):
        total_months = 0
        done_months = 0

    return json.dumps({
        "running": running,
        "cursor_date": cursor,
        "start_date": start,
        "end_date": today,
        "months_done": done_months,
        "months_total": total_months
    })


async def _run_batch_replay(start_date: str) -> None:
    global _batch_replay_task, _batch_replay_stop_flag
    settings = SettingsStore()

    # Load persisted state. batch_replay_start is only set the FIRST time a
    # sweep begins -- boot-resume calls start_batch_replay() with no args,
    # which defaults start_date to "2016-01-01" regardless of what the sweep
    # actually started from; unconditionally overwriting batch_replay_start
    # here would silently corrupt get_batch_replay_status's progress-percent
    # math on every restart. "" (not None) is the "unset" sentinel -- matches
    # discovery_stop_at's own convention (settings.py), since a still-unset
    # key's default value must match its declared str type.
    cursor = settings.get("batch_replay_cursor") or start_date
    if not settings.get("batch_replay_start"):
        settings.set("batch_replay_start", start_date)
    # Persisted eagerly, not only after the first month completes: if the
    # process dies before month 1 finishes, a resume must still know where
    # this sweep actually starts from, not rely on start_batch_replay's own
    # "2016-01-01" default happening to match.
    settings.set("batch_replay_cursor", cursor)
    settings.set("batch_replay_running", True)
    
    today = datetime.date.today().isoformat()
    loop = asyncio.get_event_loop()
    
    while True:
        if _batch_replay_stop_flag:
            break
            
        # Compute current month's [start, end] window from the cursor
        start_dt = datetime.date.fromisoformat(cursor)
        if start_dt.month == 12:
            next_month_start = datetime.date(start_dt.year + 1, 1, 1)
        else:
            next_month_start = datetime.date(start_dt.year, start_dt.month + 1, 1)
        
        month_end = (next_month_start - datetime.timedelta(days=1)).isoformat()
        if month_end > today:
            month_end = today
            
        if cursor > today:
            break
            
        logger.info("[trading_replay] Batch replay month: %s to %s", cursor, month_end)
        
        try:
            # Reuse the exact run_replay path from simulate_period internals
            specs = StrategyStore().list_all()
            await loop.run_in_executor(None, run_replay, specs, cursor, month_end)
            # BATCH-REPLAY S4 (#1227): roll up accumulated drawdown evidence
            # into each strategy's worst_drawdown after every month -- feeds
            # StrategyLifecycle.check_graduation's refusal gate. A full
            # rollup over ALL accumulated replay_results, not just this
            # month's, so it stays correct regardless of which strategies
            # this particular month actually touched.
            await loop.run_in_executor(None, _rollup_worst_drawdowns)
        except Exception as exc:
            logger.warning("[trading_replay] Batch replay failed for %s/%s: %s", cursor, month_end, exc)
            
        # Advance the cursor by one calendar month (next_month_start, not a
        # single day -- a day-only advance re-replays nearly the same
        # month on every iteration and never actually reaches today),
        # persist immediately so a crash loses at most one month.
        cursor = next_month_start.isoformat()
        settings.set("batch_replay_cursor", cursor)
        
        await asyncio.sleep(0)  # yield control
        
        if cursor >= today:
            break
            
    settings.set("batch_replay_running", False)
    logger.info("[trading_replay] Batch replay finished.")


# CROSS-STOCK-VALIDATION S2 (#1235): resumable (strategy, symbol) pair sweep.
_cross_stock_task: Optional[asyncio.Task] = None
_cross_stock_stop_flag = False
_CROSS_STOCK_WINDOW_YEARS = 5
# ponytail: 120s comfortably above a warm pair (~1.5s); upgrade path is a timeout inside bar_cache.get_bars itself
_CROSS_STOCK_STOP_TIMEOUT_S = 120


async def start_cross_stock_replay() -> str:
    global _cross_stock_task, _cross_stock_stop_flag
    if _cross_stock_task is not None and not _cross_stock_task.done():
        return "Cross-stock replay already running."
    _cross_stock_stop_flag = False
    _cross_stock_task = asyncio.create_task(_run_cross_stock_replay())
    return "Cross-stock replay started."


async def stop_cross_stock_replay() -> str:
    global _cross_stock_task
    if _cross_stock_task is None or _cross_stock_task.done():
        return "No cross-stock replay running."
    global _cross_stock_stop_flag
    _cross_stock_stop_flag = True
    try:
        await asyncio.wait_for(asyncio.shield(_cross_stock_task), timeout=_CROSS_STOCK_STOP_TIMEOUT_S)
    except asyncio.TimeoutError:
        _cross_stock_task.cancel()
        try:
            await _cross_stock_task
        except asyncio.CancelledError:
            pass
        logger.warning(
            "[cross_stock] Sweep did not stop in %.0fs after stop flag set; cancelled.",
            _CROSS_STOCK_STOP_TIMEOUT_S,
        )
    return "Cross-stock replay stopped."


async def get_cross_stock_replay_status() -> str:
    settings = SettingsStore()
    running = settings.get("cross_stock_running") or False

    specs = StrategyStore().list_all()
    pairs = build_pairs(specs, BASKET)
    store = CrossStockStore()
    pairs_done = store.get_done_count()

    # S5 (#1238): "most consistent across stocks" list. Ranked by
    # cross_stock_consistency, but ALWAYS paired with how many stocks that
    # score is based on -- a 100% consistency off 2 tested stocks reads
    # very differently than off 80, and showing the bare fraction alone
    # would misrepresent a small, early sample as a settled result.
    interval_by_strategy = {s.strategy_id: s.interval for s in specs}
    tested_counts = store.get_tested_count_by_strategy(interval_by_strategy)
    excess_returns = store.get_mean_excess_return_by_strategy()
    ranked = sorted(
        (s for s in specs if s.cross_stock_consistency is not None),
        key=lambda s: s.cross_stock_consistency, reverse=True,
    )
    top_consistent = [
        {
            "strategy_id": s.strategy_id,
            "consistency": s.cross_stock_consistency,
            "stocks_tested": tested_counts.get(s.strategy_id, 0),
            "mean_excess_return": excess_returns.get(s.strategy_id),
        }
        for s in ranked[:5]
    ]

    # #1250 decision (2026-09-19): rank by beat-buy-and-hold share + median excess, screened for
    # look-ahead, with BH-adjusted significance across all ranked strategies (#1251 axis 3).
    from cerebral.trading.cross_stock_stats import CAVEAT, summarize_vs_benchmark
    vs_benchmark = summarize_vs_benchmark(store.get_pair_returns_by_strategy(interval_by_strategy))

    return json.dumps({
        "running": running,
        "pairs_done": pairs_done,
        "pairs_total": len(pairs),
        "last_run_processed": settings.get("cross_stock_last_run_processed") or 0,
        "last_run_rate_per_hour": settings.get("cross_stock_last_run_rate_per_hour") or 0.0,
        "top_consistent": top_consistent,
        "top_vs_benchmark": vs_benchmark[:5],
        "vs_benchmark_ranked": len(vs_benchmark),
        "vs_benchmark_significant": sum(1 for r in vs_benchmark if r["significant"]),
        "vs_benchmark_caveat": CAVEAT,
        **_permutation_status_fields(store),
        "stress": _stress_status_fields(store),
        "intraday": _intraday_status_fields(store),
        "needs_review": store.get_review_rows(),
    })


# ── #1251 axis 2: random-timing (permutation) baseline ────────────────────────
_perm_task: Optional[asyncio.Task] = None
_PERM_STOCKS = 8       # per strategy: the stocks where it trades most (its best shot at an edge)
_PERM_WORKERS = 4


def _permutation_status_fields(store) -> dict:
    from cerebral.trading.cross_stock_stats import PERM_CAVEAT, summarize_permutation
    rows = summarize_permutation(store.get_permutation_pvalues())
    return {
        "top_permutation": rows[:5],
        "perm_ranked": len(rows),
        "perm_significant": sum(1 for r in rows if r["significant"]),
        "perm_caveat": PERM_CAVEAT,
    }


def _permutation_pair(code, symbol, interval, start_iso, end_iso, *, get_bars=None, run=None):
    """One (strategy, symbol) random-timing test, or None if it cannot be tested. Blocking
    (sandbox spawn) -- call from an executor."""
    from cerebral.trading.replay import _warmup_days
    from cerebral.trading.permutation_null import circular_shift_null
    if get_bars is None:
        get_bars = bar_cache.get_bars
    if run is None:
        from cerebral.trading.replay import run_bars_verbose as run
    start_dt = datetime.datetime.fromisoformat(start_iso)
    fetch_start = (start_dt - datetime.timedelta(days=int(_warmup_days(interval)))).date().isoformat()
    bars = get_bars(symbol, fetch_start, end_iso, interval)
    _equity, position, _metrics, reason = run(code, bars, interval)
    if reason is not None:
        return None
    in_window = (bars.index >= start_dt).astype(bool)
    returns = bars["Close"].pct_change().fillna(0.0).values[in_window]
    return circular_shift_null(position.values[in_window], returns)


async def start_permutation_baseline() -> str:
    global _perm_task
    if _perm_task is not None and not _perm_task.done():
        return "Permutation baseline already running."
    _perm_task = asyncio.create_task(_run_permutation_baseline())
    return "Permutation baseline started."


async def _run_permutation_baseline(*, pair_fn=None) -> None:
    from cerebral.trading.permutation_null import DEFAULT_COST
    pair_fn = pair_fn or _permutation_pair
    store = CrossStockStore()
    non_causal = store.get_excluded_ids()
    done = store.get_permutation_done()
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=365 * _CROSS_STOCK_WINDOW_YEARS)).isoformat()
    end = today.isoformat()
    jobs = [
        (spec, symbol)
        for spec in StrategyStore().list_all()
        if spec.cross_test_eligible and spec.strategy_id not in non_causal
        for symbol in store.get_top_trade_symbols(spec.strategy_id, _PERM_STOCKS)
        if (spec.strategy_id, symbol) not in done
    ]
    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(_PERM_WORKERS)
    finished = 0

    async def one(spec, symbol) -> None:
        nonlocal finished
        async with sem:
            try:
                res = await loop.run_in_executor(
                    None, pair_fn, spec.code, symbol, spec.interval, start, end
                )
            except Exception as exc:
                logger.warning("[permutation] %s/%s failed: %s", spec.strategy_id[:60], symbol, exc)
                return
            if res is None:
                return
            store.record_permutation(
                spec.strategy_id, symbol, res.observed, res.null_median, res.p_value, res.n_sims, DEFAULT_COST
            )
            finished += 1

    await asyncio.gather(*(one(spec, symbol) for spec, symbol in jobs))
    logger.info("[permutation] baseline finished: %d/%d pairs recorded.", finished, len(jobs))


# ── Regime stress windows (informational; never feeds graduation/retirement/gauntlet) ──
_stress_task: Optional[asyncio.Task] = None
_STRESS_STOCKS = 30
_STRESS_WORKERS = 4
_STRESS_HISTORY_START = "2006-01-01"   # warm-up before the 2007-10 gfc window
_STRESS_MIN_EARLY_BARS = 900           # daily bars needed inside 2006-2009 to count as having 2008 history
_STRESS_CAVEAT = (
    "Adjusted daily bars from yfinance on today's large caps (survivorship-biased), 2 bps per side cost. "
    "robust = beats buy-and-hold on half the stocks with positive median excess in every window; "
    "defensive = in every window gives up at most 10% vs buy-and-hold and cuts max drawdown by 10+ points. "
    "Stocks are correlated, so p-values are optimistic. Informational only."
)


def _stress_status_fields(store) -> dict:
    from cerebral.trading.stress_windows import summarize_stress
    res = summarize_stress(store.get_stress_rows())
    return {
        "strategies": len(res["strategies"]),
        "robust": res["robust"],
        "defensive": res["defensive"],
        "windows": res["windows"],
        "top": [
            {"strategy_id": r["strategy_id"], "robust": r["robust"], "defensive": r["defensive"],
             "windows": {w: {k: v for k, v in d.items() if k != "p_value"} for w, d in r["windows"].items()}}
            for r in res["strategies"][:5]
        ],
        "caveat": _STRESS_CAVEAT,
    }


def _stress_stocks(end_iso, *, get_bars=None, limit=_STRESS_STOCKS) -> list:
    """The first `limit` BASKET symbols that actually have 2006-2009 daily history. Blocking (network)."""
    from cerebral.trading import historical_bars
    get_bars = get_bars or historical_bars.get_daily_bars
    out = []
    for symbol in BASKET:
        if len(out) >= limit:
            break
        early = get_bars(symbol, _STRESS_HISTORY_START, end_iso)
        if len(early) and (early.index < "2010-01-01").sum() >= _STRESS_MIN_EARLY_BARS:
            out.append(symbol)
    return out


def _stress_pair(code, symbol, end_iso, *, get_bars=None, run=None) -> Optional[dict]:
    """{window: evaluate_window result} for one (strategy, symbol): ONE sandbox run over the whole
    history, position sliced per window. None if the strategy fails or never holds. Blocking."""
    from cerebral.trading import historical_bars
    from cerebral.trading.stress_windows import evaluate_window, windows
    get_bars = get_bars or historical_bars.get_daily_bars
    if run is None:
        from cerebral.trading.replay import run_bars_verbose as run
    bars = get_bars(symbol, _STRESS_HISTORY_START, end_iso)
    if len(bars) < 300:
        return None
    _equity, position, _metrics, reason = run(code, bars, "1d")
    if reason is not None or not position.any():
        return None
    returns = bars["Close"].pct_change().fillna(0.0)
    out = {}
    for name, (w_start, w_end) in windows(datetime.date.fromisoformat(end_iso)).items():
        mask = ((bars.index >= w_start) & (bars.index < w_end)).astype(bool)
        res = evaluate_window(position.values[mask], returns.values[mask])
        if res is not None:
            out[name] = res
    return out or None


async def start_stress_windows() -> str:
    global _stress_task
    if _stress_task is not None and not _stress_task.done():
        return "Stress windows already running."
    _stress_task = asyncio.create_task(_run_stress_windows())
    return "Stress windows started."


async def _run_stress_windows(*, pair_fn=None, stocks_fn=None) -> None:
    from cerebral.trading.stress_windows import RESEARCH_COST
    pair_fn = pair_fn or _stress_pair
    stocks_fn = stocks_fn or _stress_stocks
    store = CrossStockStore()
    non_causal = store.get_excluded_ids()
    done = store.get_stress_done()
    end = datetime.date.today().isoformat()
    loop = asyncio.get_event_loop()
    stocks = await loop.run_in_executor(None, stocks_fn, end)
    jobs = [
        (spec, symbol)
        for spec in StrategyStore().list_all()
        if spec.cross_test_eligible and spec.interval == "1d" and spec.strategy_id not in non_causal
        for symbol in stocks
        if (spec.strategy_id, symbol) not in done
    ]
    sem = asyncio.Semaphore(_STRESS_WORKERS)
    finished = 0

    async def one(spec, symbol) -> None:
        nonlocal finished
        async with sem:
            try:
                res = await loop.run_in_executor(None, pair_fn, spec.code, symbol, end)
            except Exception as exc:
                logger.warning("[stress] %s/%s failed: %s", spec.strategy_id[:60], symbol, exc)
                return
            if not res:
                return
            for window, row in res.items():
                store.record_stress(spec.strategy_id, symbol, window, row, RESEARCH_COST)
            finished += 1

    await asyncio.gather(*(one(spec, symbol) for spec, symbol in jobs))
    logger.info("[stress] finished: %d/%d pairs recorded over %d stocks.", finished, len(jobs), len(stocks))


# ── Intraday (day-trading) rules: true 5-minute bars, regular session, flat by the close ──
_intraday_task: Optional[asyncio.Task] = None
_INTRADAY_STOCKS = 30
_INTRADAY_WORKERS = 4
_INTRADAY_START = "2020-01-02"
_INTRADAY_MIN_BARS = 90_000            # regular-session 5m bars since 2020 (~98k possible) for a stock to count
_INTRADAY_CAVEAT = (
    "Hand-authored 5-minute rules on Alpaca regular-session bars (IEX feed volume), 2 bps per side cost, "
    "flat by the close. profitable = positive net return on half the stocks with positive median net in every "
    "window; the gross vs net gap is the cost drag. Survivorship-biased large caps, correlated stocks, so "
    "p-values are optimistic. Informational only."
)


def _intraday_status_fields(store) -> dict:
    from cerebral.trading.stress_windows import summarize_intraday
    res = summarize_intraday(store.get_intraday_rows())
    return {
        "rules": len(res["strategies"]),
        "profitable": res["profitable"],
        "windows": res["windows"],
        "top": [
            {"strategy_id": r["strategy_id"], "profitable": r["profitable"],
             "windows": {w: {k: v for k, v in d.items() if k != "p_value"} for w, d in r["windows"].items()}}
            for r in res["strategies"]
        ],
        "caveat": _INTRADAY_CAVEAT,
    }


def _regular_session(bars):
    return bars.between_time("09:30", "15:55")


def _intraday_stocks(end_iso, *, get_bars=None, limit=_INTRADAY_STOCKS) -> list:
    """First `limit` BASKET symbols with a full 5-minute history since 2020. Blocking (Alpaca fetch)."""
    get_bars = get_bars or bar_cache.get_bars
    out = []
    for symbol in BASKET:
        if len(out) >= limit:
            break
        try:
            bars = _regular_session(get_bars(symbol, _INTRADAY_START, end_iso, "5m"))
        except Exception as exc:
            logger.warning("[intraday] %s bars unavailable: %s", symbol, exc)
            continue
        if len(bars) >= _INTRADAY_MIN_BARS:
            out.append(symbol)
    return out


def _intraday_pair(code, symbol, end_iso, *, get_bars=None, run=None) -> Optional[dict]:
    """{window: evaluate_window result} for one (rule, symbol) over the intraday windows; one sandbox run."""
    from cerebral.trading.stress_windows import evaluate_window, intraday_windows
    get_bars = get_bars or bar_cache.get_bars
    if run is None:
        from cerebral.trading.replay import run_bars_verbose as run
    bars = _regular_session(get_bars(symbol, _INTRADAY_START, end_iso, "5m"))
    if len(bars) < 5_000:
        return None
    _equity, position, _metrics, reason = run(code, bars, "5m")
    if reason is not None or not position.any():
        return None
    returns = bars["Close"].pct_change().fillna(0.0)
    out = {}
    for name, (w_start, w_end) in intraday_windows(datetime.date.fromisoformat(end_iso)).items():
        mask = ((bars.index >= w_start) & (bars.index < w_end)).astype(bool)
        res = evaluate_window(position.values[mask], returns.values[mask])
        if res is not None:
            out[name] = res
    return out or None


async def start_intraday_research() -> str:
    global _intraday_task
    if _intraday_task is not None and not _intraday_task.done():
        return "Intraday research already running."
    _intraday_task = asyncio.create_task(_run_intraday_research())
    return "Intraday research started."


async def _run_intraday_research(*, pair_fn=None, stocks_fn=None) -> None:
    from cerebral.trading.intraday_rules import RULES, register_intraday_rules
    from cerebral.trading.stress_windows import RESEARCH_COST
    pair_fn = pair_fn or _intraday_pair
    stocks_fn = stocks_fn or _intraday_stocks
    store = CrossStockStore()
    strategies = StrategyStore()
    register_intraday_rules(strategies)
    excluded = store.get_excluded_ids()
    done = store.get_stress_done(intraday=True)
    end = datetime.date.today().isoformat()
    loop = asyncio.get_event_loop()
    stocks = await loop.run_in_executor(None, stocks_fn, end)
    jobs = [
        (spec, symbol)
        for spec in strategies.list_all()
        if spec.strategy_id in RULES and spec.strategy_id not in excluded
        for symbol in stocks
        if (spec.strategy_id, symbol) not in done
    ]
    sem = asyncio.Semaphore(_INTRADAY_WORKERS)
    finished = 0

    async def one(spec, symbol) -> None:
        nonlocal finished
        async with sem:
            try:
                res = await loop.run_in_executor(None, pair_fn, spec.code, symbol, end)
            except Exception as exc:
                logger.warning("[intraday] %s/%s failed: %s", spec.strategy_id[:60], symbol, exc)
                return
            if not res:
                return
            for window, row in res.items():
                store.record_stress(spec.strategy_id, symbol, window, row, RESEARCH_COST)
            finished += 1

    await asyncio.gather(*(one(spec, symbol) for spec, symbol in jobs))
    logger.info("[intraday] finished: %d/%d pairs recorded over %d stocks.", finished, len(jobs), len(stocks))


_CAUSALITY_REFERENCE_SYMBOL = "AAPL"   # in BASKET; deep, liquid history
_CAUSALITY_YEARS = 7


_CAUSALITY_WORKERS = 4   # each check is ~60 sandbox spawns; a serial pass over 283 strategies is ~7h
_CAUSALITY_EXTRA_SYMBOLS = 2   # plus the stocks where the strategy trades most (measured: an AAPL-only
_CAUSALITY_EXTRA_CUTS = 30     # check passed a strategy whose leak only fires on high-volatility stocks)


async def _ensure_causality_checked(store, specs, *, get_bars=None, check=None) -> None:
    """CAUSALITY C3: run the look-ahead check once per eligible strategy that has
    no verdict yet (untestable verdicts count as checked). The check runs on the reference
    stock and on the stocks where the strategy traded most in the sweep; ANY non-causal
    verdict makes the strategy non-causal. A non-causal strategy's cross-stock consistency
    is later cleared by rollup_consistency. Informational only -- nothing here touches
    graduation, retirement or orders."""
    import functools
    from cerebral.trading.causality import CausalityResult, DEFAULT_CUTS
    if get_bars is None:
        get_bars = bar_cache.get_bars
    if check is None:
        from cerebral.trading.causality import check_causality as check
    done = store.get_causality_checked_ids()
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=365 * _CAUSALITY_YEARS)).isoformat()
    end = today.isoformat()
    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(_CAUSALITY_WORKERS)

    async def one(spec) -> None:
        async with sem:
            if _cross_stock_stop_flag:
                return
            extras = [
                s for s in store.get_top_trade_symbols(spec.strategy_id, _CAUSALITY_EXTRA_SYMBOLS)
                if s != _CAUSALITY_REFERENCE_SYMBOL
            ]
            results = []
            for i, symbol in enumerate([_CAUSALITY_REFERENCE_SYMBOL] + extras):
                try:
                    bars = get_bars(symbol, start, end, spec.interval)
                except Exception as exc:
                    if i == 0:
                        # Not recorded: retried next night rather than becoming a permanent hole.
                        logger.warning("[causality] %s: bar fetch failed: %s", spec.strategy_id[:60], exc)
                        return
                    continue
                cuts = DEFAULT_CUTS if i == 0 else _CAUSALITY_EXTRA_CUTS
                # The sandbox spawns are blocking -- keep them off the event loop.
                r = await loop.run_in_executor(
                    None, functools.partial(check, n_cuts=cuts), spec.code, bars
                )
                results.append(r)
                if r.causal is False:
                    break  # one leak on one stock is proof enough
            leaky = [r for r in results if r.causal is False]
            tested = sum(r.tested for r in results)
            if leaky:
                result = CausalityResult(False, sum(r.mismatches for r in leaky), tested)
            elif tested:
                result = CausalityResult(True, 0, tested)
            else:
                result = CausalityResult(None, 0, 0)
            store.record_causality(spec.strategy_id, result.causal, result.mismatches, result.tested)
            if result.causal is False:
                logger.warning(
                    "[causality] %s reads future bars (%d/%d cut points differ) -- excluded from cross-stock consistency",
                    spec.strategy_id[:60], result.mismatches, result.tested,
                )

    pending = [s for s in specs if s.cross_test_eligible and s.strategy_id not in done]
    await asyncio.gather(*(one(s) for s in pending))


async def _run_cross_stock_replay() -> None:
    settings = SettingsStore()
    settings.set("cross_stock_running", True)

    today = datetime.date.today()
    # timedelta(days=...), not .replace(year=...) -- the latter raises on
    # Feb 29 landing on a non-leap year 5 years back.
    start = (today - datetime.timedelta(days=365 * _CROSS_STOCK_WINDOW_YEARS)).isoformat()
    end = today.isoformat()

    store = CrossStockStore()
    run_id = store.create_run(start, end)

    from cerebral.trading.strategy_review import hold_untrusted_text_strategies
    hold_untrusted_text_strategies(StrategyStore(), store)
    await _ensure_causality_checked(store, StrategyStore().list_all())

    pairs = build_pairs(StrategyStore().list_all(), BASKET)
    # F1 (#1246): resume by skipping pairs already in the results table, not
    # by seeking a cursor index.  Deletion, reordering, and newly-created
    # strategies all self-heal -- no cursor ever goes stale.
    done_pairs = store.get_done_pairs()
    pending = [(spec, sym) for spec, sym in pairs if (spec.strategy_id, sym) not in done_pairs]

    loop = asyncio.get_event_loop()
    processed = 0
    run_started_mono = time.monotonic()
    for spec, symbol in pending:
        if _cross_stock_stop_flag:
            break

        try:
            result = await loop.run_in_executor(
                None, run_pair, spec.strategy_id, spec.code, symbol, start, end, spec.interval,
            )
            store.record_result(
                run_id, spec.strategy_id, symbol,
                result["net_return"], result["max_drawdown"], result["n_trades"], result["flat_reason"],
                result.get("benchmark_return"),
            )
        except Exception as exc:
            # Hard exception (not a flat_reason from run_pair itself) --
            # record a row so the pair is visible and retryable next pass,
            # rather than silently becoming a permanent hole in the table.
            logger.warning("[cross_stock] %s/%s failed: %s", spec.strategy_id, symbol, exc)
            store.record_result(run_id, spec.strategy_id, symbol, None, None, 0, str(exc))

        processed += 1
        await asyncio.sleep(0)  # yield control

    settings.set("cross_stock_running", False)
    # CROSS-STOCK-VALIDATION S4 (#1237): roll up per-strategy consistency
    # once per sweep pass (matches BATCH-REPLAY S4's own once-per-month
    # cadence for _rollup_worst_drawdowns) -- cheap relative to a single
    # pair's real backtest, but not worth a full GROUP BY over the whole
    # results table after every individual pair.
    rollup_consistency(StrategyStore(), store)
    # S5 (#1238): measured throughput, persisted (not just logged) so the
    # UI can read it directly instead of parsing cerebral.err.log. Only
    # updated when this run actually processed something and took
    # measurable time -- an interrupted-immediately run (e.g. stopped
    # right after starting) leaves the prior real measurement in place
    # rather than overwriting it with a meaningless near-zero rate.
    elapsed_seconds = time.monotonic() - run_started_mono
    if processed > 0 and elapsed_seconds > 0:
        settings.set("cross_stock_last_run_processed", processed)
        settings.set("cross_stock_last_run_rate_per_hour", processed / elapsed_seconds * 3600)
    # CROSS-STOCK-VALIDATION S3 (#1236): actual pairs-processed-this-run,
    # not just "pairs available" -- this is the real throughput number the
    # nightly soft-cap and any completion estimate calibrate against,
    # since the only prior timing data point (BATCH-REPLAY's own) doesn't
    # transfer to this campaign's very different per-pair cost.
    logger.info(
        "[cross_stock] Sweep pass finished: %d pairs processed this run (%d total pairs, %d remaining).",
        processed, len(pairs), len(pending) - processed,
    )


async def check_cross_stock_night_window(
    settings: Optional[SettingsStore] = None, now: Optional[datetime.datetime] = None,
) -> None:
    """CROSS-STOCK-VALIDATION S3 (#1236): nightly cross-stock sweep,
    midnight-8am ET, soft-capped at 8 real-clock hours. Called from
    cerebral.main's _scheduler_loop on its existing 5-minute tick -- not a
    new SchedulerPlugin recurring event, since list_due_events()'s "daily"
    recurrence is elapsed-time-since-last-run, not anchored to a clock
    hour (see plugins/scheduler.py's own _recurrence_interval), and would
    drift across restarts.

    `now` is injectable (naive treated as already NY-local, tz-aware
    converted), same convention as cerebral.trading.market_hours's own
    is_market_hours -- so a test can build one directly without fighting
    the real system clock.

    cross_stock_night_started_at (not just cross_stock_running) is what
    the soft cap measures elapsed time against and what stops this from
    re-firing start_cross_stock_replay every tick for the rest of the
    window once tonight's run is already going -- cross_stock_running
    alone can't do either job, since a manual start via the tray sets it
    too without setting a night-start timestamp.
    """
    if settings is None:
        settings = SettingsStore()
    from zoneinfo import ZoneInfo
    ny_tz = ZoneInfo("America/New_York")
    if now is None:
        now_ny = datetime.datetime.now(ny_tz)
    elif now.tzinfo is None:
        now_ny = now.replace(tzinfo=ny_tz)
    else:
        now_ny = now.astimezone(ny_tz)

    # Derived from the SAME now_ny basis as the hour check below, not a
    # fresh real-clock read -- otherwise `now` injection wouldn't actually
    # make this deterministic/testable, and production would silently mix
    # two different clock readings within one tick.
    now_utc = now_ny.astimezone(datetime.timezone.utc)

    night_started_at = settings.get("cross_stock_night_started_at")
    if night_started_at:
        # Already in a nightly run -- check the soft cap. Checked every
        # tick regardless of hour, not just "if now_ny.hour >= 8", so a
        # clock/timezone edge case can't strand the sweep running past
        # its cap indefinitely.
        elapsed_hours = (
            now_utc - datetime.datetime.fromisoformat(night_started_at)
        ).total_seconds() / 3600
        if now_ny.hour >= 8 or elapsed_hours >= 8:
            await stop_cross_stock_replay()
            settings.set("cross_stock_night_started_at", "")
    elif now_ny.hour < 8 and not settings.get("cross_stock_running"):
        # Not already running (a manual start elsewhere is left alone)
        # and inside the window -- start tonight's run.
        await start_cross_stock_replay()
        settings.set("cross_stock_night_started_at", now_utc.isoformat())


def create() -> TradingReplayPlugin:
    return TradingReplayPlugin()
