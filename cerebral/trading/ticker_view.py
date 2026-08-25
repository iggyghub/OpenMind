"""Per-ticker progress aggregation for the Trading pane's Tickers sub-tab
(S29/#892, decisions #48-#51).

Pure and duck-typed, like live_tick.py/discovery.py -- takes its data
sources as plain arguments rather than reaching into cerebral/main.py's
module-level globals, so it's testable with fakes instead of the real
on-disk SQLite files those globals are bound to.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional


def build_ticker_benchmark(
    symbol: str, phase_fills: List[dict], fetch_ohlcv: Callable,
) -> List[dict]:
    """Buy-and-hold benchmark for one phase segment: what this segment's own
    FIRST fill -- its real qty at its real entry price -- would be worth,
    held flat, at each subsequent daily close. Reuses the gauntlet's own
    vs_benchmark definition (gauntlet.py:277, `last_close/first_close-1`)
    but expressed as a running dollar series scaled to the strategy's real
    position size, rather than one before/after scalar -- so it lands in
    the same $ units as the strategy's own cumulative-PnL line and the two
    are directly comparable, not a fabricated "invested capital" figure.
    """
    if not phase_fills:
        return []
    first = phase_fills[0]
    start_date = str(first["timestamp"])[:10]
    end_date = str(phase_fills[-1]["timestamp"])[:10]
    try:
        bars = fetch_ohlcv(symbol, start_date, end_date, interval="1d")
    except Exception:
        return []
    if bars is None or len(bars) == 0:
        return []
    entry_price = float(first["price"])
    qty = float(first["qty"])
    return [
        {"ts": str(idx), "value": qty * (float(close) - entry_price)}
        for idx, close in bars["Close"].items()
    ]


def build_ticker_view(
    watchlist_symbols: List[str],
    states: Dict[str, "object"],
    get_spec: Callable[[str], Optional["object"]],
    get_fills: Callable[[str], List[dict]],
    fetch_ohlcv: Callable,
    get_latest_attempt: Optional[Callable[[str], Optional[dict]]] = None,
) -> dict:
    """Groups strategies by symbol rather than strategy_id, so a ticker
    with more than one strategy shows all of them on one card, and a
    ticker still mid-discovery (no strategy registered yet) shows too.

    Four honest stages now (decision #49, closed by S30/#894 -- was three).
    discovery.py's DiscoveryAttempts persists each dispatch's real gauntlet
    outcome, so a screened-but-strategy-less ticker CAN now be told apart
    from one that was actually dispatched and rejected:
      - "screened":   in the discovery watchlist, no attempt on record yet,
        no strategy exists for it.
      - "rejected":   its most recent logged attempt was UNVALIDATED, no
        strategy exists for it -- the reason is on the "reason" key.
      - "validated":  a strategy exists but has recorded zero fills yet.
      - "charting":   a strategy exists with at least one fill -- the real
        equity-vs-benchmark chart.

    "rejected" only applies while no strategy exists for that symbol -- the
    states loop below always takes priority once one does, same as before.
    `get_latest_attempt` is optional (defaults to None, preserving the old
    three-stage behavior for any caller not yet wired to DiscoveryAttempts).

    Still not tracked, same honesty reasoning as before: DiscoveryWatchlist
    has no expiry flag, so a watchlist symbol never drops off this view on
    its own -- only a halted strategy with zero fills is dropped (decision
    #48's "nothing paper/live behind it"), since that IS real, queryable
    state. There is also no live "gauntlet currently running" stage --
    deliberately out of scope, see #894: a dispatch is one awaited call,
    nothing meaningful to poll mid-flight.

    Args:
        watchlist_symbols: ``DiscoveryWatchlist.symbols()`` -- tickers
            currently on the discovery watchlist.
        states: dispatch_id -> object with a ``.status`` attribute
            ("paper"/"live"/"halted"), matching ``StrategyLifecycle._states``.
        get_spec: strategy_id (base, unversioned) -> object with ``.symbol``,
            or None -- matching ``StrategyStore.get``.
        get_fills: dispatch_id -> list of fill dicts (each with timestamp/
            phase/side/pnl/price/qty), most-recent-first -- matching
            ``ForwardRecord.get_fills(strategy_id=...)``.
        fetch_ohlcv: (symbol, start_date, end_date, interval="1d") -> a
            DataFrame with a "Close" column -- matching
            ``cerebral.trading_data.fetch_ohlcv``.
        get_latest_attempt: symbol -> {"verdict", "reason", ...} or None --
            matching ``DiscoveryAttempts.get_latest``.
    """
    tickers: Dict[str, dict] = {
        sym: {"symbol": sym, "stage": "screened", "strategies": [], "reason": ""}
        for sym in watchlist_symbols
    }

    if get_latest_attempt is not None:
        for sym in watchlist_symbols:
            attempt = get_latest_attempt(sym)
            if attempt is not None and attempt.get("verdict") == "UNVALIDATED":
                tickers[sym]["stage"] = "rejected"
                tickers[sym]["reason"] = attempt.get("reason", "")

    for dispatch_id, state in states.items():
        base_id = dispatch_id.rsplit("@v", 1)[0] if "@v" in dispatch_id else dispatch_id
        spec = get_spec(base_id)
        if spec is None:
            continue
        symbol = spec.symbol

        fills = list(reversed(get_fills(dispatch_id)))  # chronological ASC
        segments = []
        for phase in ("paper", "live"):
            phase_fills = [f for f in fills if f["phase"] == phase]
            if not phase_fills:
                continue
            points = []
            cum = 0.0
            for f in phase_fills:
                cum += f["pnl"]
                points.append({
                    "ts": f["timestamp"], "equity": cum, "side": f["side"],
                    "pnl": f["pnl"], "price": f["price"], "strategy": dispatch_id,
                })
            segments.append({
                "phase": phase, "points": points,
                "benchmark": build_ticker_benchmark(symbol, phase_fills, fetch_ohlcv),
            })

        if state.status == "halted" and not segments:
            continue  # decision #48: halted with nothing paper/live behind it

        entry = tickers.setdefault(symbol, {"symbol": symbol, "stage": "screened", "strategies": [], "reason": ""})
        entry["strategies"].append({"name": dispatch_id, "status": state.status, "segments": segments})
        if segments:
            entry["stage"] = "charting"
        elif entry["stage"] != "charting":
            entry["stage"] = "validated"

    return {"tickers": sorted(tickers.values(), key=lambda t: t["symbol"])}
