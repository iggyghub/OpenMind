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
) -> dict:
    """Groups strategies by symbol rather than strategy_id, so a ticker
    with more than one strategy shows all of them on one card, and a
    ticker still mid-discovery (no strategy registered yet) shows too.

    Three honest stages (decision #49). Not four: discovery.py's
    DiscoveryWatchlist has no persisted per-attempt log (S27 only records
    "dispatched" via record_activity_fn, never the eventual gauntlet
    verdict), so a screened-but-strategy-less ticker cannot be told apart
    from one that was judged-and-rejected or dispatched-and-failed the
    gauntlet without inventing a status that isn't actually stored anywhere
    -- fabricating that distinction would break the Honesty rule this
    campaign enforces throughout. What's real and shown instead:
      - "screened":   in the discovery watchlist, no strategy exists for it.
      - "validated":  a strategy exists but has recorded zero fills yet.
      - "charting":   a strategy exists with at least one fill -- the real
        equity-vs-benchmark chart.

    Known limitation, same honesty reasoning: DiscoveryWatchlist also has no
    rejection/expiry flag, so a watchlist symbol never drops out of
    "screened" on its own even if discovery rejected it long ago -- only a
    halted strategy with zero fills is dropped (decision #48's "nothing
    paper/live behind it"), since that IS real, queryable state.

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
    """
    tickers: Dict[str, dict] = {
        sym: {"symbol": sym, "stage": "screened", "strategies": [], "reason": ""}
        for sym in watchlist_symbols
    }

    # S30: Apply per-attempt log to override "screened" -> "rejected"
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

        entry = tickers.setdefault(symbol, {"symbol": symbol, "stage": "screened", "strategies": []})
        entry["strategies"].append({"name": dispatch_id, "status": state.status, "segments": segments})
        if segments:
            entry["stage"] = "charting"
        elif entry["stage"] != "charting":
            entry["stage"] = "validated"

    return {"tickers": sorted(tickers.values(), key=lambda t: t["symbol"])}
