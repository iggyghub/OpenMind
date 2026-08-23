"""One live/paper dispatch tick: a validated strategy -> a real broker order.

THE CANONICAL LIVE STRATEGY CONTRACT
====================================
Decision #5 in TRADING.md says a strategy is ``def strategy(data) -> signals``.
That was never pinned down to anything concrete, and three incompatible
shapes drifted apart. This module pins the *live* one down and is the only
consumer of it:

``data``
    The pandas DataFrame ``cerebral.trading_data.fetch_ohlcv`` returns:
    ascending DatetimeIndex, columns ``Open/High/Low/Close/Volume``
    (capitalised, split/dividend adjusted). NOT a dict -- the stub generator
    in ``trading_ideas.py`` used to read ``data.get("close", [])``, which
    against that DataFrame silently returns the ``[]`` default (pandas'
    ``.get()`` treats a missing column as absent), i.e. every generated
    strategy produced no signals whatsoever. Fixed at the generator, pinned
    here.

``signals``
    A sequence of TARGET POSITIONS, one per bar, each ``1`` (want long),
    ``0`` (want flat) or ``-1`` (want short). Target state, not an action:
    the dispatcher diffs the LAST element against what the broker says it
    actually holds, so a missed tick, a partial fill or a Felix restart
    self-corrects on the next tick instead of double-entering. A sequence
    shorter than ``data`` is fine (indicator warm-up); the last element is
    always "what to hold now". An empty sequence means "no opinion" -> hold.

``run_gauntlet``'s batch ``backtest_func(prices, params) -> (equity, metrics)``
is deliberately NOT reconciled with this. Validating a whole history and
deciding one bar are different jobs with legitimately different shapes; this
module only owns the live path.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable, List, Optional, Sequence

from cerebral.trading.broker import AlpacaBrokerClient, Position
from cerebral.trading.alerts import AlertDispatcher, StructuredAlert
from cerebral.trading.sandboxed_eval import evaluate_signals
from cerebral.trading.strategy_store import StrategySpec, StrategyStore

logger = logging.getLogger(__name__)

SIGNAL_LONG = 1
SIGNAL_FLAT = 0
SIGNAL_SHORT = -1

# ponytail: one fixed window rather than asking the strategy how much history
# it wants. 180 calendar days is ~124 trading bars -- enough for a 100-bar
# moving average, the deepest indicator anything generated so far uses. Add a
# `lookback_days` field to StrategySpec when a strategy genuinely needs more.
DEFAULT_LOOKBACK_DAYS = 180


def evaluate_signal(strategy_fn: Callable[[Any], Sequence], data: Any) -> int:
    """Today's target position: the last element of the strategy's signals.

    Anything the strategy can't express as -1/0/1 is treated as "no opinion"
    (flat is a decision; garbage is not, and must not become a trade).
    """
    signals = strategy_fn(data)
    if signals is None or len(signals) == 0:
        return SIGNAL_FLAT
    raw = signals[-1]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Strategy returned a non-numeric signal %r; holding", raw)
        return SIGNAL_FLAT
    if value not in (SIGNAL_LONG, SIGNAL_FLAT, SIGNAL_SHORT):
        logger.warning("Strategy returned out-of-range signal %r; holding", raw)
        return SIGNAL_FLAT
    return value


def position_direction(position: Optional[Position]) -> int:
    """+1 long / -1 short / 0 flat, from whatever the broker reports.

    The two BrokerClient implementations disagree on how they say it:
    StubBrokerClient signs ``qty`` and uses ``side`` "buy"/"sell", Alpaca
    uses ``side`` "long"/"short". Sign of qty first (unambiguous when
    present), the side string as the fallback.
    """
    if position is None:
        return 0
    qty = float(position.qty)
    if qty > 0:
        return SIGNAL_LONG
    if qty < 0:
        return SIGNAL_SHORT
    side = (position.side or "").lower()
    if side in ("short", "sell"):
        return SIGNAL_SHORT
    if side in ("long", "buy"):
        return SIGNAL_LONG
    return SIGNAL_FLAT


def find_position(positions: List[Position], symbol: str) -> Optional[Position]:
    """The broker's own row for this symbol, or None if flat.

    A zero-qty row counts as flat -- brokers differ on whether a fully
    closed position disappears or lingers at qty 0.
    """
    for p in positions:
        if p.symbol == symbol and float(p.qty) != 0.0:
            return p
    return None


def decide_action(
    signal: int, position: Optional[Position], open_qty: float
) -> Optional[tuple[str, float, bool]]:
    """(side, qty, is_close) for the order to place, or None to hold.

    ponytail: one order per tick. A flip (long -> short) closes this tick and
    opens on the next, rather than sending a double-size reversing order --
    the target-state contract makes that correct automatically, and it keeps
    a mis-sized flip from ever being possible.
    """
    current = position_direction(position)
    if signal == current:
        return None
    if current != 0:
        # Close what we hold, whatever the new signal is.
        return ("sell" if current == SIGNAL_LONG else "buy", abs(float(position.qty)), True)
    return ("buy" if signal == SIGNAL_LONG else "sell", float(open_qty), False)


def realized_pnl(
    entry_price: float, exit_price: float, qty: float, direction: int, fees: float = 0.0
) -> float:
    """Realized P&L on a closing trade.

    ``fees`` is the *closing* order's fee only -- the broker reports that one
    with the fill. The opening order's fee is not re-derived here; it was
    recorded on its own fill row's ``fees`` column when it happened, and
    guessing at it would be exactly the fabricated-number failure this
    campaign keeps catching.
    """
    gross = (exit_price - entry_price) * qty
    if direction == SIGNAL_SHORT:
        gross = -gross
    return gross - fees


def run_strategy_tick(
    strategy_id: str,
    spec: StrategySpec,
    broker: Any,
    forward_record: Any,
    fetch: Optional[Callable] = None,
    today: Optional[date] = None,
    phase: str = "paper",
    risk: Optional[Any] = None,
    size_pct: float = 1.0,
) -> dict:
    """Evaluate one strategy against fresh data and act on the result.

    Fetch bars -> compile + call the strategy -> read the broker's own
    position -> open / close / hold -> record the fill (with real realized
    P&L on a close). ``phase`` is the caller's job to set correctly --
    "live" only when ``broker`` is actually a live-env AlpacaBrokerClient
    (see dispatch_due_events's arm/graduation gate); every other caller
    keeps the "paper" default.
    """
    if fetch is None:
        # Imported lazily: cerebral.trading_data pulls in yfinance, which
        # nothing needs at import time (and no test should ever reach).
        from cerebral.trading_data import fetch_ohlcv as fetch

    end = today or date.today()
    start = end - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    data = fetch(spec.symbol, start.isoformat(), end.isoformat())

    # Re-evaluated per tick rather than cached: a sandbox spawn is cheap
    # next to a data fetch, and it means a re-registered spec takes effect
    # immediately. evaluate_signal's existing None/empty/garbage handling
    # is reused via a lambda rather than duplicated inline.
    signals = evaluate_signals(spec.code, data)
    signal = evaluate_signal(lambda d: signals, data)

    # StrategySpec is frozen -- ramp the local open-qty, not spec.qty itself.
    # Only the OPEN side reads this; decide_action closes at the position's
    # own qty regardless, so a ramped strategy still exits its full size.
    open_qty = spec.qty * size_pct

    position = find_position(broker.list_positions(), spec.symbol)
    action = decide_action(signal, position, open_qty)
    if action is None:
        return {"status": "hold", "signal": signal, "symbol": spec.symbol}

    side, qty, is_close = action
    # Captured BEFORE the order: placing it mutates the broker's position.
    entry_price = float(position.avg_entry_price) if is_close else 0.0
    direction = position_direction(position) if is_close else 0

    if risk is not None:
        last_close = float(data["Close"].iloc[-1]) if "Close" in data.columns else 0.0
        trade_value = float(qty) * last_close
        # Real accrued loss, not a fabricated 0.0 -- forward_record already
        # has every fill's realized pnl with a real timestamp, so today's
        # loss is one query away rather than an invented number.
        current_daily_loss = max(0.0, -forward_record.get_daily_pnl())
        res = risk.check_order(
            account_equity=broker.get_account().equity,
            current_positions_count=len(broker.list_positions()),
            current_daily_loss=current_daily_loss,
            trade_value=trade_value,
            symbol=spec.symbol,
            qty=float(qty),
        )
        if not res.allowed:
            return {"status": "blocked", "blocked_by": res.blocked_by}

    order = broker.place_order(symbol=spec.symbol, qty=qty, side=side, type="market")
    if order.status not in ("FILLED", "PARTIALLY_FILLED"):
        return {"status": "unfilled", "signal": signal, "symbol": spec.symbol,
                "order_status": order.status}

    # A partial close leaves the remainder on the book; the next tick sees the
    # smaller position and tries again -- "keep the partial, adjust position
    # size math" (TRADING.md failure behaviour), for free from target-state
    # signals.
    filled = float(order.filled_qty) or float(order.qty)
    pnl = (
        realized_pnl(entry_price, float(order.price), filled, direction, float(order.fees))
        if is_close
        # An opening fill has no realized P&L yet -- a real 0.0, not a
        # placeholder. It becomes real on the close that pairs with it.
        else 0.0
    )

    forward_record.add_fill(
        symbol=order.symbol, side=order.side, qty=filled,
        price=float(order.price), fees=float(order.fees), pnl=pnl,
        phase=phase, strategy_id=strategy_id,
    )
    return {
        "status": "closed" if is_close else "opened",
        "signal": signal, "symbol": spec.symbol, "side": order.side,
        "qty": filled, "price": float(order.price), "pnl": pnl,
        "order_id": order.id,
    }


def dispatch_due_events(
    scheduler: Any,
    broker: Any,
    forward_record: Any,
    lifecycle: Any = None,
    store: Optional[StrategyStore] = None,
    fetch: Optional[Callable] = None,
    arm: bool = False,
    risk: Optional[Any] = None,
    size_pct: float = 1.0,
    alert_dispatcher: Optional[AlertDispatcher] = None,
    live_broker_factory: Optional[Callable[[], Any]] = None,
) -> List[dict]:
    """One pass of the recurring dispatcher: run every due strategy.

    Lives here rather than inline in cerebral/main.py's ``_scheduler_loop``
    so the whole chain is testable without importing main. ``scheduler`` is
    duck-typed (``list_due_events`` / ``_run_paper_strategy`` /
    ``mark_event_run``) -- cerebral/ must not import plugins/.

    ``arm`` (S11 Part 2/4): the manual arm/disarm toggle. A strategy only
    trades against a real ``AlpacaBrokerClient(env="live")`` when BOTH
    ``arm`` is True AND its own lifecycle status is "live" -- graduation
    alone, or the toggle alone, is never sufficient. Every other strategy
    (not yet graduated, or graduated but disarmed) keeps trading against
    the ``broker`` passed in (paper), exactly as before this parameter
    existed.
    """
    results: List[dict] = []
    for evt in scheduler.list_due_events():
        name = evt["title"]
        # S17 (#862): the versioned identity used for forward-record/lifecycle
        # state, so an edited strategy's paper/live record restarts clean
        # (decision #27) -- falls back to the bare name when no lineage row
        # exists yet (e.g. a strategy registered before S16, or store=None).
        version_row = store.get_current_version(name) if store is not None else None
        dispatch_id = f"{name}@v{version_row['version']}" if version_row is not None else name

        # A halted strategy stays scheduled (retirement is reversible) but
        # places no new trades. Still marked run, so it doesn't re-check on
        # every single tick.
        if lifecycle is not None and lifecycle.get_state(dispatch_id).status == "halted":  # S17
            scheduler.mark_event_run(evt["id"])
            results.append({"status": "halted", "strategy": name})
            continue

        is_live = arm and lifecycle is not None and lifecycle.get_state(dispatch_id).status == "live"  # S17
        current_broker = broker
        if is_live:
            # Injectable so tests never construct a real AlpacaBrokerClient
            # (whose preflight() would otherwise make a genuine credential/
            # network check even inside a pure unit test) -- matches the
            # existing fetch=/store=/risk= injection seams on this function.
            live_broker = (live_broker_factory or (lambda: AlpacaBrokerClient(env="live")))()
            ok, reason = live_broker.preflight()
            if ok:
                current_broker = live_broker
            else:
                # Conservative-continue (TRADING.md failure behaviour): stay
                # on paper rather than silently error-looping every tick.
                is_live = False
                if alert_dispatcher is not None:
                    alert_dispatcher.emit(StructuredAlert(
                        severity="critical", event_type="live_preflight_failed",
                        message=f"Live preflight failed for '{name}': {reason}. Staying on paper.",
                        context={"strategy": name, "dispatch_id": dispatch_id, "reason": reason},
                    ))

        # Ramp only advances (and only matters) once a strategy is actually
        # trading live -- a disarmed/paper strategy's live_trade_count never
        # grows, so apply_position_ramp would just return the unused 0.25
        # default. size_pct is an optional caller-level multiplier on top.
        ramp_pct = lifecycle.apply_position_ramp(dispatch_id) if is_live else 1.0

        result = scheduler._run_paper_strategy(
            name, current_broker, forward_record, {}, store=store, fetch=fetch,
            phase="live" if is_live else "paper", dispatch_id=dispatch_id,
            risk=risk, size_pct=size_pct * ramp_pct,  # S20
        )
        # Marked regardless of outcome: a persistently failing strategy should
        # retry at its own interval, not spam every tick.
        scheduler.mark_event_run(evt["id"])
        result["strategy"] = name
        results.append(result)

        if lifecycle is not None:
            _apply_lifecycle(lifecycle, dispatch_id, forward_record, result)  # S17
    return results


def _apply_lifecycle(lifecycle: Any, name: str, forward_record: Any, result: dict) -> None:
    """Graduation / ramp / retirement checks after a dispatch.

    Graduation flips the strategy's lifecycle status to "live" -- it does
    NOT by itself start live trading. dispatch_due_events only switches to
    a real AlpacaBrokerClient (and records phase="live") when the manual
    arm/disarm toggle is also on (S11 Part 2/4); every other combination
    keeps trading on the paper broker with phase="paper" fills.
    """
    if lifecycle.check_graduation(name, forward_record):
        size_pct = lifecycle.apply_position_ramp(name)
        logger.warning(
            "[trading] Strategy '%s' met the paper graduation bar (ramp %.0f%%). "
            "No live orders placed -- live execution is not wired.",
            name, size_pct * 100,
        )
        result["graduated"] = True

    # Wired, but inert until live fills exist: check_retirement returns early
    # unless status == "live" AND a live equity curve has been recorded, and
    # the drawdown branch is skipped entirely at worst_backtest_dd=0.0.
    # 0.0 is honest -- nothing stores the gauntlet's worst backtest drawdown
    # yet (StrategyCard carries an equity curve but no drawdown metric), and
    # inventing one would fabricate the very threshold that halts a strategy.
    if lifecycle.check_retirement(name, worst_backtest_dd=0.0):
        result["retired"] = True
