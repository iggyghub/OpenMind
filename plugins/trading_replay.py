"""Plugin for trading replay tools.

Registers `list_strategies`, `simulate_period`, and `replay_report` tools
that read from StrategyStore/ReplayStore and invoke run_replay.
"""
from typing import Optional, List, Dict, Any, get_type_hints
import json

from cerebral.core.plugin import Tool, ToolResult
from cerebral.trading.strategy_store import StrategyStore
from cerebral.trading.replay.store import ReplayStore
from cerebral.trading.replay.replay import run_replay


class TradingReplayPlugin:
    """Tools for historical replay operations."""

    TOOLS = [
        Tool(
            name="list_strategies",
            description="List available trading strategies, optionally filtered by symbol and interval.",
            parameters=get_type_hints(list_strategies),
            handler=list_strategies,
        ),
        Tool(
            name="simulate_period",
            description="Simulate a period of trading for a set of strategies and return a run summary.",
            parameters=get_type_hints(simulate_period),
            handler=simulate_period,
        ),
        Tool(
            name="replay_report",
            description="Generate a detailed report for a replay run, including per-strategy rows and a flat_reason census.",
            parameters=get_type_hints(replay_report),
            handler=replay_report,
        ),
    ]


def list_strategies(symbol: Optional[str] = None, interval: Optional[str] = None) -> ToolResult:
    """List strategies filtered by optional symbol and interval.

    Returns JSON array of id/symbol/interval/qty per strategy.
    """
    store = StrategyStore()
    all_strategies = store.list_all()

    filtered = []
    for s in all_strategies:
        if symbol is not None and s.symbol != symbol:
            continue
        if interval is not None and s.interval != interval:
            continue
        filtered.append({
            "id": s.id,
            "symbol": s.symbol,
            "interval": s.interval,
            "qty": s.qty,
        })

    return ToolResult(output=json.dumps(filtered))


def simulate_period(start: str, end: str, symbols: Optional[List[str]] = None, interval: Optional[str] = None) -> ToolResult:
    """Simulate a trading period and return run_id plus compact summary.

    Filters strategies by symbols/interval, calls run_replay, then queries
    ReplayStore for summary stats.
    """
    store = StrategyStore()
    all_strategies = store.list_all()

    filtered = []
    for s in all_strategies:
        if symbols is not None and s.symbol not in symbols:
            continue
        if interval is not None and s.interval != interval:
            continue
        filtered.append(s)

    run_id = run_replay(start, end, filtered)

    replay_store = ReplayStore()
    try:
        results = replay_store.get_results(run_id)
    except Exception:
        results = []

    total_strategies = len(filtered)
    flat_reason_count = 0
    total_net_return = 0.0
    count_with_return = 0

    for r in results:
        if r.get("flat_reason"):
            flat_reason_count += 1
        net_ret = r.get("net_return")
        if net_ret is not None:
            total_net_return += net_ret
            count_with_return += 1

    mean_return = total_net_return / count_with_return if count_with_return > 0 else 0.0

    summary = {
        "run_id": run_id,
        "total_strategies_replayed": total_strategies,
        "flat_reason_count": flat_reason_count,
        "aggregate_mean_net_return": mean_return,
    }

    return ToolResult(output=json.dumps(summary))


def replay_report(run_id: str) -> ToolResult:
    """Return full replay report with per-strategy rows and flat_reason census.

    Returns error JSON if run_id is not found, otherwise returns rows and census.
    """
    replay_store = ReplayStore()
    try:
        run_info = replay_store.get_run(run_id)
        results = replay_store.get_results(run_id)
    except Exception as e:
        return ToolResult(output=json.dumps({
            "error": f"Run {run_id} not found or unavailable: {str(e)}"
        }))

    # Build flat_reason census (reason-prefix -> count)
    reason_census: Dict[str, int] = {}
    rows = []

    for r in results:
        flat_reason = r.get("flat_reason")
        if flat_reason:
            # Extract prefix before colon or error type
            prefix = flat_reason.split(":")[0].strip()
            reason_census[prefix] = reason_census.get(prefix, 0) + 1

        rows.append({
            "symbol": r.get("symbol"),
            "interval": r.get("interval"),
            "net_return": r.get("net_return"),
            "flat_reason": flat_reason,
            # Include other relevant fields as needed
        })

    report = {
        "run_id": run_id,
        "total_strategies": len(rows),
        "rows": rows,
        "flat_reason_census": reason_census,
    }

    return ToolResult(output=json.dumps(report))
