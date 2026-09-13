"""Strategy/gauntlet admin plugin -- MCP tools for strategy management.
Extracted from plugins/scheduler.py per SCHEDULER-SPLIT.md S5 (#1213).
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.paths import data_dir
from cerebral.settings import SettingsStore
from cerebral.trading.broker import StubBrokerClient
from cerebral.trading.discovery import build_dynamic_universe, rank_for_day_trading
from cerebral.trading.gauntlet import run_gauntlet, compute_max_holding_days
from cerebral.trading.replay import run_bars
from cerebral.trading.strategy_store import StrategySpec, StrategyStore, mint_expansion_strategy_id

logger = logging.getLogger(__name__)

PLUGIN_NAME = "trading_strategies"

REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})


class TradingStrategiesPlugin:
    name = PLUGIN_NAME

    def __init__(self, router=None, scheduler=None, record_activity_fn=None, settings=None):
        self._router = router
        # scheduler: live SchedulerPlugin -- _create_event delegates here so
        # gauntlet.py auto-promote can schedule the recurring paper-trade event.
        self._scheduler = scheduler
        self._record_activity_fn = record_activity_fn
        self._on_trading_change = None
        self._lifecycle = None  # wired post-construction by main.py
        if settings is not None:
            self._settings = settings
        else:
            self._settings = SettingsStore(path=data_dir() / "felix-settings.json")

    def _create_event(self, args: dict):
        """Delegate to SchedulerPlugin so gauntlet.py auto-promote can
        register the recurring paper-trade event on the shared events table."""
        if self._scheduler is not None:
            return self._scheduler._create_event(args)
        logger.warning("[trading_strategies] _create_event called with no scheduler attached")

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="run_gauntlet",
                description=(
                    "Validates a trading strategy against the full validation gauntlet "
                    "(out-of-sample, walk-forward, Monte Carlo, vs-random, vs-benchmark, "
                    "noise, parameter sensitivity, costs, capacity). A VALIDATED verdict "
                    "auto-registers the strategy and schedules it for autonomous paper trading."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python source: def strategy(data) -> signals"},
                        "claim": {"type": "string", "description": "Trading hypothesis text to generate code from"},
                        "url": {"type": "string", "description": "URL to extract a trading claim from"},
                        "book": {"type": "string", "description": "Book title, with chapter"},
                        "chapter": {"type": "string", "description": "Chapter number, paired with book"},
                        "symbol": {"type": "string", "description": "Ticker to backtest and paper-trade"},
                        "hypothesis": {"type": "string", "description": "Falsifiable claim the strategy is testing"},
                        "provenance": {"type": "string", "description": "Where the strategy came from"},
                    },
                    "required": ["symbol", "hypothesis"],
                },
            ),
            Tool(
                name="edit_strategy",
                description=(
                    "Edits an existing strategy source code: records a new version, "
                    "re-runs the full validation gauntlet, and only moves the dispatch "
                    "pointer to the new version if it validates."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "strategy_id": {"type": "string"},
                        "code": {"type": "string"},
                    },
                    "required": ["strategy_id", "code"],
                },
            ),
            Tool(
                name="get_strategy_code",
                description="Returns a strategy's currently dispatched source code and provenance.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"strategy_id": {"type": "string"}},
                    "required": ["strategy_id"],
                },
            ),
            Tool(
                name="expand_strategy_ticker",
                description=(
                    "Expands a validated strategy to new candidate tickers via the gauntlet. "
                    "Requires positive confidence weight."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"strategy_id": {"type": "string"}},
                    "required": ["strategy_id"],
                },
            ),
            Tool(
                name="mix_strategies",
                description=(
                    "Combines multiple validated strategies into a composite strategy. "
                    "Modes: unanimous or majority."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "component_ids": {"type": "array", "items": {"type": "string"}},
                        "mode": {"type": "string", "enum": ["unanimous", "majority"]},
                    },
                    "required": ["component_ids", "mode"],
                },
            ),
            Tool(
                name="auto_combine_strategies",
                description=(
                    "S43: Auto-selects top-3 strategies for a symbol, combines via both "
                    "unanimous and majority voting, returns the better-performing composite."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="halt_strategy",
                description=(
                    "Manually halts a strategy autonomous dispatch -- reversible via resume_strategy."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"strategy_id": {"type": "string"}},
                    "required": ["strategy_id"],
                },
            ),
            Tool(
                name="resume_strategy",
                description=(
                    "Reverses a halt -- resumes at paper status, re-earns live through the "
                    "30-trade graduation gate."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"strategy_id": {"type": "string"}},
                    "required": ["strategy_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "run_gauntlet":
            return await self._run_gauntlet(args)
        if tool_name == "edit_strategy":
            return await self._edit_strategy(args)
        if tool_name == "get_strategy_code":
            return self._get_strategy_code(args)
        if tool_name == "expand_strategy_ticker":
            return await self._expand_strategy_ticker(args)
        if tool_name == "mix_strategies":
            return await self._run_mix_strategies(args)
        if tool_name == "auto_combine_strategies":
            return await self._run_auto_combine_strategies(args)
        if tool_name == "halt_strategy":
            return self._halt_strategy(args)
        if tool_name == "resume_strategy":
            return self._resume_strategy(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    async def _run_gauntlet(
        self, args: dict, *, strategy_store=None, fetch=None,
        origin: str = "generated", parent_version=None, strategy_id=None,
        components_json=None, interval: str = "1d",
    ) -> ToolResult:
        code = args.get("code", "").strip()
        claim = args.get("claim", "").strip()
        url = args.get("url", "").strip()
        book = args.get("book", "").strip()
        chapter = args.get("chapter", "").strip()
        symbol = args.get("symbol", "").strip()
        hypothesis = args.get("hypothesis", "").strip()
        provenance = args.get("provenance", "")
        interval = args.get("interval", "1d")

        if not symbol or not hypothesis:
            return ToolResult(content="symbol and hypothesis are required", is_error=True)

        idea = None
        if not code:
            from cerebral.trading_ideas import from_prose, from_book_claim, extract_from_url, to_strategy
            if book and chapter:
                idea = from_book_claim(claim or f"Hypothesis from {book}", book, chapter)
            elif claim:
                idea = from_prose(claim)
            elif url:
                ideas = extract_from_url(url)
                if not ideas:
                    return ToolResult(content=f"No claims extracted from {url}", is_error=True)
                idea = ideas[0]
            else:
                return ToolResult(content="One of code, claim, book+chapter, or url is required", is_error=True)
            code = await to_strategy(idea, router=self._router)
            if not code:
                return ToolResult(content="Strategy generation produced no code", is_error=True)

        if fetch is None:
            from cerebral.trading_data import fetch_ohlcv as fetch
        from cerebral.trading.sandboxed_eval import evaluate_signals_verbose

        end = datetime.now(timezone.utc).date()
        lookback_days = 365 if interval == "1d" else 30
        start = end - timedelta(days=lookback_days)
        try:
            prices = fetch(symbol, start.isoformat(), end.isoformat(), interval=interval)
        except Exception as e:
            return ToolResult(content=f"Data fetch failed for {symbol}: {e}", is_error=True)

        if idea is not None:
            _, _repair_err = evaluate_signals_verbose(code, prices)
            if _repair_err:
                from cerebral.trading_ideas import to_strategy
                _repaired = await to_strategy(idea, router=self._router, prior_code=code, prior_error=_repair_err)
                if _repaired:
                    code = _repaired
                    provenance = provenance + " (repaired after 1 retry)"

        def backtest(bars, params):
            equity, _position, metrics = run_bars(code, bars, interval)
            return equity, metrics

        last_price = float(prices["Close"].iloc[-1]) if "Close" in prices.columns and len(prices) else 0.0
        risk_pct = self._settings.get("max_per_trade_risk_pct") or 2.0
        starting_capital = self._settings.get("trading_paper_starting_capital") or 10000.0
        position_qty = (starting_capital * (risk_pct / 100.0)) / last_price if last_price > 0 else 1.0

        try:
            # ponytail: benchmark is strategy buy-and-hold; SPY wiring is a separate slice.
            card = run_gauntlet(
                backtest, prices, {}, prices.copy(),
                position_sizes=pd.Series([position_qty] * len(prices), index=prices.index),
                hypothesis=hypothesis, provenance=provenance,
                scheduler=self, paper_broker=StubBrokerClient(),
                symbol=symbol, strategy_code=code,
                strategy_store=strategy_store, position_qty=position_qty,
                origin=origin, parent_version=parent_version, strategy_id=strategy_id,
                components_json=components_json, interval=interval,
            )
        except Exception as e:
            logger.warning(f"[trading_strategies] run_gauntlet failed for {symbol}: {e}", exc_info=True)
            return ToolResult(content=f"Gauntlet run failed: {e}", is_error=True)

        return ToolResult(content=json.dumps({
            "verdict": card.verdict,
            "sharpe": card.sharpe,
            "total_return": card.total_return,
            "strategy_id": strategy_id or hypothesis or provenance,
            "gates": [{"name": g.name, "passed": bool(g.passed), "details": g.details} for g in card.gates],
        }))

    async def _edit_strategy(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        strategy_id = args.get("strategy_id", "").strip()
        code = args.get("code", "").strip()
        if not strategy_id or not code:
            return ToolResult(content="strategy_id and code are required", is_error=True)
        store = strategy_store if strategy_store is not None else StrategyStore()
        spec = store.get(strategy_id)
        parent = store.get_current_version(strategy_id)
        if spec is None or parent is None:
            return ToolResult(content=f"No existing strategy '{strategy_id}' to edit", is_error=True)
        provenance = store.render_provenance(parent) + ", as modified by user"
        return await self._run_gauntlet(
            {"code": code, "symbol": spec.symbol, "hypothesis": parent["hypothesis"] or "", "provenance": provenance},
            strategy_store=store, fetch=fetch,
            origin="user_edited", parent_version=parent["version"], strategy_id=strategy_id,
        )

    def _get_strategy_code(self, args: dict, *, strategy_store=None) -> ToolResult:
        strategy_id = args.get("strategy_id", "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)
        store = strategy_store if strategy_store is not None else StrategyStore()
        spec = store.get(strategy_id)
        if spec is None:
            return ToolResult(content=f"No strategy '{strategy_id}' found", is_error=True)
        version_row = store.get_current_version(strategy_id)
        provenance = store.render_provenance(version_row) if version_row is not None else "unknown"
        return ToolResult(content=json.dumps({"code": spec.code, "provenance": provenance}))

    async def _expand_strategy_ticker(
        self, args: dict, *, strategy_store=None, fetch=None, confidence_fn=None, broker=None,
    ) -> ToolResult:
        strategy_id = args.get("strategy_id", "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)
        store = strategy_store if strategy_store is not None else StrategyStore()
        spec = store.get(strategy_id)
        if spec is None:
            return ToolResult(content=f"No strategy '{strategy_id}' found", is_error=True)
        if confidence_fn is not None:
            confidence = confidence_fn(strategy_id)
        else:
            from cerebral.trading.forward_record import ForwardRecord
            confidence = ForwardRecord().compute_confidence_weight(strategy_id=strategy_id)
        if confidence <= 0:
            return ToolResult(content=f"Strategy '{strategy_id}' has non-positive confidence weight ({confidence}). Cannot expand.", is_error=True)
        version_row = store.get_current_version(strategy_id)
        hypothesis = (version_row["hypothesis"] if version_row is not None else "") or f"Hypothesis from {strategy_id}"
        current_symbol = spec.symbol
        candidate_limit = self._settings.get("discovery_candidate_limit") or 3
        fetch_fn = fetch
        if fetch_fn is None:
            from cerebral.trading_data import fetch_ohlcv as fetch_fn
        broker_obj = broker
        if broker_obj is None:
            from cerebral.trading.broker import AlpacaBrokerClient
            broker_obj = AlpacaBrokerClient(env="paper")
        universe = build_dynamic_universe(broker_obj, fetch_fn)
        candidates = [t for t in universe if t != current_symbol]
        ranked_candidates = rank_for_day_trading(candidates, fetch_fn)
        candidates = ranked_candidates[:candidate_limit]
        results = []
        for candidate in candidates:
            new_id = mint_expansion_strategy_id(strategy_id, candidate)
            try:
                result = await self._run_gauntlet(
                    {"code": spec.code, "symbol": candidate, "hypothesis": hypothesis,
                     "provenance": f"Expanded from {strategy_id} for {candidate}"},
                    strategy_store=store, fetch=fetch, origin="discovered", strategy_id=new_id,
                )
                verdict = json.loads(result.content).get("verdict", "ERROR") if not result.is_error else "ERROR"
            except Exception:
                verdict = "ERROR"
                logger.exception("[trading_strategies] expand gauntlet failed for %s", candidate)
            results.append({"ticker": candidate, "new_id": new_id, "verdict": verdict})
        if self._record_activity_fn is not None:
            await self._record_activity_fn("activity", {
                "source": "expand_strategy_ticker", "strategy_id": strategy_id,
                "tickers": [r["ticker"] for r in results],
                "verdicts": {r["ticker"]: r["verdict"] for r in results},
            })
        return ToolResult(content=json.dumps({"original_id": strategy_id, "attempts": results}))

    async def _run_mix_strategies(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        component_ids = args.get("component_ids", [])
        mode = args.get("mode", "")
        if not component_ids or mode not in ("unanimous", "majority"):
            return ToolResult(content="component_ids (list) and mode (unanimous/majority) are required", is_error=True)
        store = strategy_store if strategy_store is not None else StrategyStore()
        resolved = []
        symbols = set()
        provenances = []
        for cid in component_ids:
            spec = store.get(cid)
            version = store.get_current_version(cid)
            if spec is None or version is None:
                return ToolResult(content=f"Component strategy '{cid}' not found in store", is_error=True)
            symbols.add(spec.symbol)
            provenances.append(store.render_provenance(version))
            resolved.append((cid, spec.code))
        if len(symbols) > 1:
            return ToolResult(content=f"Mismatched symbols: {sorted(symbols)}. All components must share the same symbol.", is_error=True)
        symbol = symbols.pop()
        try:
            from cerebral.trading.compose import compose_strategies
            composite_code = compose_strategies(resolved, mode)
        except Exception as e:
            return ToolResult(content=f"Composite generation failed: {e}", is_error=True)
        new_id = f"mixed_{uuid.uuid4().hex[:8]}"
        components = [{"id": cid, "provenance": p} for cid, p in zip(component_ids, provenances)]
        provenance_str = f"Mixed strategy ({mode}) of {len(component_ids)} components: {', '.join(component_ids)}"
        return await self._run_gauntlet(
            {"code": composite_code, "symbol": symbol,
             "hypothesis": f"Composite strategy ({mode}) of {len(component_ids)} components",
             "provenance": provenance_str},
            strategy_store=store, fetch=fetch, origin="mixed", strategy_id=new_id, components_json=components,
        )

    async def _run_auto_combine_strategies(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        symbol = args.get("symbol", "").strip()
        if not symbol:
            return ToolResult(content="symbol is required", is_error=True)
        store = strategy_store if strategy_store is not None else StrategyStore()
        symbol_strategies = [s for s in store.list_all() if s.symbol == symbol]
        from cerebral.trading.forward_record import ForwardRecord
        record = ForwardRecord()
        scored = []
        for s in symbol_strategies:
            try:
                conf = record.compute_confidence_weight(strategy_id=s.strategy_id)
                if conf > 0:
                    scored.append((conf, s.strategy_id))
            except Exception:
                continue
        scored.sort(key=lambda x: x[0], reverse=True)
        top_3 = scored[:3]
        if len(top_3) < 2:
            return ToolResult(content=json.dumps({
                "status": "not_enough_strategies", "eligible_count": len(top_3),
                "message": f"Need at least 2 eligible strategies (confidence > 0), found {len(top_3)} on {symbol}.",
            }))
        component_ids = [sid for _, sid in top_3]
        res_uni = await self._run_mix_strategies({"component_ids": component_ids, "mode": "unanimous"}, strategy_store=store, fetch=fetch)
        res_maj = await self._run_mix_strategies({"component_ids": component_ids, "mode": "majority"}, strategy_store=store, fetch=fetch)

        def parse(res):
            try:
                d = json.loads(res.content)
                return d.get("total_return", 0.0), d.get("verdict", "ERROR"), d.get("strategy_id")
            except Exception:
                return 0.0, "ERROR", None

        ret_uni, ver_uni, id_uni = parse(res_uni)
        ret_maj, ver_maj, id_maj = parse(res_maj)
        if ver_uni == "VALIDATED" and ver_maj != "VALIDATED":
            winner, loser = "unanimous", "majority"
        elif ver_maj == "VALIDATED" and ver_uni != "VALIDATED":
            winner, loser = "majority", "unanimous"
        else:
            winner = "unanimous" if ret_uni >= ret_maj else "majority"
            loser = "majority" if winner == "unanimous" else "unanimous"
        winner_ret, winner_ver = (ret_uni, ver_uni) if winner == "unanimous" else (ret_maj, ver_maj)
        loser_id = id_maj if winner == "unanimous" else id_uni
        if loser_id is not None:
            store.delete(loser_id)
        if self._record_activity_fn is not None:
            await self._record_activity_fn("activity", {
                "source": "auto_combine_strategies", "symbol": symbol,
                "component_ids": component_ids, "winner_mode": winner,
                "winner_strategy_id": id_uni if winner == "unanimous" else id_maj,
                "winner_verdict": winner_ver,
            })
        return ToolResult(content=json.dumps({
            "status": "complete", "symbol": symbol, "component_ids": component_ids,
            "winner_mode": winner,
            "winner_strategy_id": id_uni if winner == "unanimous" else id_maj,
            "winner_return": winner_ret, "winner_verdict": winner_ver, "loser_mode": loser,
        }))

    def _halt_strategy(self, args: dict) -> ToolResult:
        strategy_id = (args.get("strategy_id") or "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)
        if self._lifecycle is None:
            return ToolResult(content="Strategy lifecycle is not wired", is_error=True)
        self._lifecycle.halt_strategy(strategy_id)
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({"strategy_id": strategy_id, "status": "halted"}))

    def _resume_strategy(self, args: dict) -> ToolResult:
        strategy_id = (args.get("strategy_id") or "").strip()
        if not strategy_id:
            return ToolResult(content="strategy_id is required", is_error=True)
        if self._lifecycle is None:
            return ToolResult(content="Strategy lifecycle is not wired", is_error=True)
        state = self._lifecycle.get_state(strategy_id)
        if state.status != "halted":
            return ToolResult(content=f"Strategy '{strategy_id}' is not halted (status={state.status})", is_error=True)
        self._lifecycle.resume_strategy(strategy_id)
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({"strategy_id": strategy_id, "status": "paper"}))


def create() -> TradingStrategiesPlugin:
    return TradingStrategiesPlugin()
