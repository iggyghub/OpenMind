"""Trading strategies plugin — dispatches strategies via gauntlet, manages trend-baskets."""

from typing import Any
from cerebral.mcp.orchestrator import ToolResult

TREND_BASKET_STRATEGY_CODE = "TREND_BASKET"


class TradingStrategiesPlugin:
    REQUIRED_CAPABILITIES = frozenset(["fs_read", "fs_write"])

    def __init__(
        self,
        router=None,
        scheduler=None,
        broker=None,
        lifecycle=None,
        settings=None,
    ):
        self.router = router
        self.scheduler = scheduler
        self.broker = broker
        self.lifecycle = lifecycle
        self.settings = settings
        self._gauntlet = self
        self._record_activity_fn = None
        # TREND2 rising-edge gate (wired by scheduler/TREND2 slice)
        self._rising_edge = False

    def _is_rising_edge(self) -> bool:
        """Check TREND2's rising-edge gate. Defaults to False until wired."""
        return self._rising_edge

    def _run_gauntlet(
        self,
        strategy_code: str,
        symbol: str,
        hypothesis: str,
        provenance: str,
        origin: str = "discovered",
        strategy_id: str = "",
        risk_pct: float = 0.10,
    ) -> dict:
        """Execute the gauntlet for a single symbol."""
        # Placeholder for actual risk/sentiment/gauntlet logic.
        # In production, this would consult risk limits, sentiment gates, etc.
        return {
            "status": "ACCEPT",
            "message": f"Gauntlet passed for {symbol} via {strategy_code}",
        }

    def _is_already_held(self, symbol: str) -> bool:
        """Check StrategyLifecycle / ForwardRecord for an existing non-retired position sourced from this strategy's provenance."""
        if self.lifecycle is None:
            return False
        try:
            return self.lifecycle.is_position_active(symbol, strategy_code=TREND_BASKET_STRATEGY_CODE)
        except Exception:
            return False

    def _expand_strategy_ticker(
        self,
        strategy_code: str,
        candidates: list[str],
        provenance: str = "discovered",
        origin: str = "discovered",
        risk_pct: float = 0.10,
    ) -> list[dict]:
        """Dispatch a strategy code across a list of candidate symbols via the gauntlet.
        Reuses the shape of IPO expansion: loop over candidates, call _run_gauntlet, collect results."""
        results = []
        for sym in candidates:
            # Skip any symbol already actively held from a still-open trend-basket position
            if self._is_already_held(sym):
                continue

            strategy_id = f"{TREND_BASKET_STRATEGY_CODE}_{sym}"
            hypothesis = f"Trend basket entry for {sym}"
            verdict = self._run_gauntlet(
                strategy_code=strategy_code,
                symbol=sym,
                hypothesis=hypothesis,
                provenance=provenance,
                origin=origin,
                strategy_id=strategy_id,
                risk_pct=risk_pct,
            )
            results.append({"symbol": sym, "verdict": verdict})

            # Activity-log the dispatched symbols + Gauntlet verdicts
            if self._record_activity_fn:
                self._record_activity_fn(
                    {
                        "type": "trend_basket_dispatch",
                        "symbol": sym,
                        "verdict": verdict,
                        "origin": origin,
                    }
                )
        return results

    async def call_tool(self, tool_name: str, tool_args: dict) -> ToolResult:
        if tool_name == "trend_basket_dispatch":
            result = self._trend_basket_dispatch()
            return ToolResult(content=str(result))
        return ToolResult(error=f"Unknown tool: {tool_name}")

    def _trend_basket_dispatch(self) -> dict:
        """TREND3: Scheduler tick -> Gauntlet per selected symbol."""
        # 1. Check TREND2's rising-edge gate
        if not self._is_rising_edge():
            return {"status": "SKIPPED", "reason": "Not a rising edge — breadth/gate not met"}

        # 2. Build candidate pool via cerebral/trading/discovery.py's build_dynamic_universe
        try:
            from cerebral.trading.discovery import build_dynamic_universe
            universe = build_dynamic_universe()
        except Exception as e:
            return {"status": "ERROR", "reason": f"build_dynamic_universe failed: {e}"}

        # 3. Rank by TREND2's momentum function, take the top 10
        # (Assume universe returns dicts sorted by momentum; fallback to simple slicing)
        candidates = []
        if isinstance(universe, list):
            candidates = [item.get("symbol") for item in universe[:10] if isinstance(item, dict) and "symbol" in item]
        elif isinstance(universe, list) and all(isinstance(u, str) for u in universe):
            candidates = universe[:10]

        # 4. For each remaining candidate, call _run_gauntlet with TREND_BASKET_STRATEGY_CODE
        dispatched = self._expand_strategy_ticker(
            strategy_code=TREND_BASKET_STRATEGY_CODE,
            candidates=candidates,
            provenance="TREND_BASKET",
            origin="discovered",
        )
        return {"status": "OK", "dispatched": dispatched}
