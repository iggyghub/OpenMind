"""Trading-control plugin -- MCP tools for the paper/live trading dispatch.
Extracted from plugins/scheduler.py per SCHEDULER-SPLIT.md S6 (#1214).
"""
import json
import logging

import pandas as pd

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading.live_tick import run_strategy_tick
from cerebral.trading.strategy_store import StrategySpec, StrategyStore

logger = logging.getLogger(__name__)

PLUGIN_NAME = "trading_control"

# fs_read: start_trading/stop_trading read settings;
# fs_write: both mutate settings.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})


class TradingControlPlugin:
    name = PLUGIN_NAME

    def __init__(self, scheduler_plugin=None):
        # scheduler_plugin: the live SchedulerPlugin instance; provides
        # list_due_events/mark_event_run and _settings for start/stop_trading.
        self._scheduler_plugin = scheduler_plugin
        # Wired post-construction by main.py -- closes over objects
        # (_trading_forward_record, _trading_broker, paper_broker) that
        # don't exist yet at this constructor's call time.
        self._on_trading_change = None
        self._reset_paper_fn = None
        self._get_paper_archive_fills_fn = None

    @property
    def _settings(self):
        return self._scheduler_plugin._settings

    def list_tools(self):
        return [
            Tool(
                name="start_trading",
                description=(
                    "S34/#901: enable the autonomous paper-trading dispatch loop "
                    "(default already on -- this is for re-enabling after stop_trading). "
                    "Does not affect trading_live_arm or any already-graduated live strategy."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="stop_trading",
                description="S34/#901: pause the autonomous paper-trading dispatch loop.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="reset_paper_trading",
                description=(
                    "Clears the live paper-trading view (fills, P&L, equity "
                    "curves on the Overview/Trade Log tabs) and resets the "
                    "simulated paper account back to its configured starting "
                    "capital. Does not touch any 'live' fills or trading_live_arm. "
                    "Not destructive -- the cleared history is archived as its "
                    "own block, viewable in the Overview tab's collapsible "
                    "history section."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_paper_archive_fills",
                description=(
                    "Read-only: the real fills for one archived paper-trading "
                    "block created by reset_paper_trading, for the Overview "
                    "tab's collapsible history section (expand-to-fetch)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"archive_id": {"type": "integer"}},
                    "required": ["archive_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "start_trading":
            return self._start_trading(args)
        if tool_name == "stop_trading":
            return self._stop_trading(args)
        if tool_name == "reset_paper_trading":
            return await self._reset_paper_trading(args)
        if tool_name == "get_paper_archive_fills":
            return self._get_paper_archive_fills(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Delegation seam -- satisfies dispatch_due_events duck type
    # ------------------------------------------------------------------

    def list_due_events(self) -> list[dict]:
        return self._scheduler_plugin.list_due_events()

    def mark_event_run(self, event_id: int) -> None:
        self._scheduler_plugin.mark_event_run(event_id)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    def _start_trading(self, args: dict) -> ToolResult:
        self._scheduler_plugin._settings.set("trading_paper_enabled", True)
        return ToolResult(content=json.dumps({"enabled": True}))

    def _stop_trading(self, args: dict) -> ToolResult:
        self._scheduler_plugin._settings.set("trading_paper_enabled", False)
        return ToolResult(content=json.dumps({"enabled": False}))

    async def _reset_paper_trading(self, args: dict) -> ToolResult:
        if self._reset_paper_fn is None:
            return ToolResult(content="Reset not wired up yet.", is_error=True)
        result = self._reset_paper_fn()
        if hasattr(result, "__await__"):
            result = await result
        return ToolResult(content=json.dumps(result or {"reset": True}))

    def _get_paper_archive_fills(self, args: dict) -> ToolResult:
        if self._get_paper_archive_fills_fn is None:
            return ToolResult(content="Not wired up yet.", is_error=True)
        archive_id = args.get("archive_id")
        if archive_id is None:
            return ToolResult(content="archive_id is required.", is_error=True)
        fills = self._get_paper_archive_fills_fn(int(archive_id))
        return ToolResult(content=json.dumps({"fills": fills}))

    def _run_paper_strategy(
        self, strategy_name: str, broker, forward_record: "ForwardRecord",
        config: dict | None = None, store=None, fetch=None, phase: str = "paper",
        dispatch_id: str | None = None,
        risk=None, size_pct: float = 1.0,
        sentiment_label: str | None = None,
        stock_sentiment_labels: dict | None = None,
        claimed_symbols: set | None = None,
        bear_case_fn=None,
        correlation_matrix: "pd.DataFrame | None" = None,
    ) -> dict:
        """Runs one paper-trading tick for the given strategy. Pure trade
        execution -- event bookkeeping (marking a due event as dispatched)
        is the caller's job; see mark_event_run().

        The real decision (fetch data -> evaluate the strategy -> diff against
        the broker's own position -> open/close/hold -> record realized P&L)
        lives in cerebral/trading/live_tick.py; this is the plugin-side seam.
        It used to place a hardcoded `buy 1 "SYMBOL"` on every call -- a
        literal placeholder ticker, always buy, never sell, no strategy
        consulted at all.

        `config` may carry an inline spec ({"symbol", "code", "qty"}); with
        no code, the strategy's registered spec is looked up by name. No
        spec means no trade -- there is no default symbol to fall back to.
        """
        if not broker or not forward_record:
            return {"status": "skipped", "reason": "broker/record not provided"}
        config = config or {}

        try:
            if config.get("code"):
                spec = StrategySpec(
                    strategy_id=strategy_name, symbol=config["symbol"],
                    code=config["code"], qty=config.get("qty", 1.0),
                )
            else:
                spec = (store or StrategyStore()).get(strategy_name)
            if spec is None:
                return {"status": "skipped", "reason": "no strategy spec registered"}

            result = run_strategy_tick(
                dispatch_id or strategy_name, spec, broker, forward_record, fetch=fetch, phase=phase,
                risk=risk, size_pct=size_pct,
                position_key=strategy_name,
                sentiment_label=sentiment_label,
                stock_sentiment_labels=stock_sentiment_labels,
                claimed_symbols=claimed_symbols,
                bear_case_fn=bear_case_fn,
                correlation_matrix=correlation_matrix,
            )
            logger.info(f"Paper tick for {strategy_name}: {result}")
            return result
        except Exception as e:
            logger.warning(f"Paper trade execution failed for {strategy_name}: {e}")
            return {"status": "error", "reason": str(e)}


def create() -> TradingControlPlugin:
    return TradingControlPlugin()
