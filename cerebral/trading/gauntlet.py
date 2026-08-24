from datetime import datetime, timezone
from typing import Any, Dict, List, Callable, Tuple, Optional, Sequence
from dataclasses import dataclass
import numpy as np
import pandas as pd

from .cost_model import apply_costs_to_returns, Trade


def _bars_per_year(interval: str) -> float:
    """Trading bars per year for Sharpe annualisation, derived from interval."""
    if interval == "1d":
        return 252.0
    if interval in ("1h", "4h"):
        return 252 * 6.5  # ~1638
    if interval in ("5m", "15m", "30m"):
        return 252 * 6.5 * (60 / 5)  # ~10440
    return 252.0


@dataclass
class GateResult:
    """Result of a single S2 gate (oos_test / walk_forward)."""
    passed: bool
    metrics: Dict[str, Any]
    details: str = ""


def oos_test(
    strategy_fn: Callable[[Sequence], Tuple[List[float], List[Trade]]],
    data: Sequence,
    holdout_pct: float = 0.25,
) -> GateResult:
    """
    Out-of-sample validation gate.
    Splits data, fits on first (1-holdout_pct), tests on held-out holdout_pct.
    Returns pass/fail and metrics.
    """
    cost_config = {"min_spread_pct": 0.01, "max_spread_pct": 0.03}

    split_idx = int(len(data) * (1 - holdout_pct))
    train_data = data[:split_idx]
    oos_data = data[split_idx:]

    # Fit strategy on training window
    fitted_strategy = strategy_fn(train_data)

    # Evaluate on OOS window
    if callable(fitted_strategy):
        gross_oos, trades_oos = fitted_strategy(oos_data)
    else:
        gross_oos, trades_oos = strategy_fn(oos_data)

    net_oos = apply_costs_to_returns(gross_oos, trades_oos, cost_config)

    cum_net = 1.0
    for r in net_oos:
        cum_net *= (1 + r)
    cum_net -= 1.0

    passed = cum_net > 0.0

    return GateResult(
        passed=passed,
        metrics={
            "oos_cumulative_net_return": cum_net,
            "oos_trades_count": len(trades_oos),
            "holdout_pct": holdout_pct,
        },
        details=f"OOS cumulative net return: {cum_net:.4f}, trades: {len(trades_oos)}"
    )


def walk_forward(
    strategy_fn: Callable[[Sequence], Any],
    data: Sequence,
    fit_ratio: int = 5,
) -> GateResult:
    """
    Walk-forward validation gate.
    Rolling fit/test windows based on fit_ratio.
    Returns pass/fail and aggregate forward-window performance.
    """
    cost_config = {"min_spread_pct": 0.01, "max_spread_pct": 0.03}

    if fit_ratio < 1:
        raise ValueError("fit_ratio must be >= 1")

    window_size = fit_ratio + 1
    n = len(data)
    num_windows = max(0, n - window_size)

    all_net_returns = []
    total_trades = 0

    for i in range(num_windows):
        train_window = data[i : i + fit_ratio]
        test_window = data[i + fit_ratio : i + window_size]

        fitted_strategy = strategy_fn(train_window)
        if callable(fitted_strategy):
            gross, trades = fitted_strategy(test_window)
        else:
            gross, trades = strategy_fn(test_window)

        net = apply_costs_to_returns(gross, trades, cost_config)
        all_net_returns.extend(net)
        total_trades += len(trades)

    cum_net = 1.0
    for r in all_net_returns:
        cum_net *= (1 + r)
    cum_net -= 1.0

    passed = cum_net > 0.0

    return GateResult(
        passed=passed,
        metrics={
            "wf_cumulative_net_return": cum_net,
            "wf_forward_windows": num_windows,
            "wf_total_trades": total_trades,
            "fit_ratio": fit_ratio,
        },
        details=f"Walk-forward cumulative net return: {cum_net:.4f}"
    )


# ── S3: full gauntlet (Monte Carlo, vs-random, vs-benchmark, noise,
# parameter sensitivity, capacity) + strategy card ─────────────────────────
#
# GauntletGateResult is intentionally a separate dataclass from GateResult
# above rather than a shared one: oos_test/walk_forward (S2) report a
# variable-shaped metrics dict, while run_gauntlet's six gates (S3) each
# reduce to one comparable metric/threshold pair. Unifying the two into one
# interface -- and reconciling run_gauntlet's `backtest_func(prices, params)
# -> (equity_curve, metrics)` shape against oos_test/walk_forward's
# `strategy_fn(data) -> (gross_returns, trades)` shape, and against decision
# #5's `def strategy(data) -> signals` -- is a real design decision, not a
# merge-conflict fix; see TRADING.md's Landed PRs note for S1b/S2.

@dataclass
class GauntletGateResult:
    name: str
    passed: bool
    metric: float
    threshold: float
    details: str = ""


@dataclass
class StrategyCard:
    hypothesis: str
    provenance: str
    gates: List[GauntletGateResult]
    equity_curve: List[float]
    sharpe: float
    sharpe_ci: Tuple[float, float]
    total_return: float
    total_return_ci: Tuple[float, float]
    verdict: str
    survivorship_bias_caveat: str = (
        "Data may not include delisted securities. "
        "Results could be skewed if survival bias is present."
    )


def _bootstrap_ci(
    data: np.ndarray, n_boot: int = 10000, alpha: float = 0.05, rng: np.random.Generator = None
) -> Tuple[float, float]:
    if rng is None:
        rng = np.random.default_rng(42)
    if len(data) == 0:
        return 0.0, 0.0
    means = []
    for _ in range(n_boot):
        sample = rng.choice(data, size=len(data), replace=True)
        means.append(np.mean(sample))
    lower = np.percentile(means, 100 * alpha / 2)
    upper = np.percentile(means, 100 * (1 - alpha / 2))
    return float(lower), float(upper)


def run_gauntlet(
    backtest_func: Callable[[pd.DataFrame, Dict[str, float]], Tuple[List[float], Dict[str, float]]],
    prices: pd.DataFrame,
    params: Dict[str, float] = None,
    benchmark_prices: pd.DataFrame = None,
    position_sizes: Optional[pd.Series] = None,
    hypothesis: str = "",
    provenance: str = "",
    n_permutations: int = 1000,
    p_value_threshold: float = 0.05,
    benchmark_outperform_threshold: float = 0.0,
    noise_pct: float = 0.015,
    param_sensitivity_pct: float = 0.2,
    adv_threshold_pct: float = 0.075,
    holding_period: int = 1,
    seed: int = 42,
    scheduler: Any = None,
    # BrokerClient for auto-promote's paper trade (see the auto-promote
    # block below); None skips auto-promote entirely (no dry-run mode).
    paper_broker: Any = None,
    auto_promote: bool = True,
    # What the dispatcher needs to actually evaluate this strategy later:
    # the ticker to trade and the source of its `def strategy(data) -> signals`
    # function. Registered in strategy_store on a pass; see the auto-promote
    # block. backtest_func above stays the batch-validation shape -- these are
    # the per-tick half, deliberately separate (see live_tick.py's header).
    symbol: Optional[str] = None,
    strategy_code: Optional[str] = None,
    strategy_store: Any = None,
    position_qty: float = 1.0,
    origin: str = 'generated',
    parent_version: Optional[int] = None,
    strategy_id: Optional[str] = None,
    components_json: Any = None,
    interval: str = "1d",
) -> StrategyCard:
    rng = np.random.default_rng(seed)
    params = params or {}
    equity_curve, metrics = backtest_func(prices, params)
    daily_returns = np.diff(equity_curve) / equity_curve[:-1] if len(equity_curve) > 1 else np.array([0.0])
    ann_factor = _bars_per_year(interval)
    sharpe = metrics.get("sharpe", float(np.mean(daily_returns) / np.std(daily_returns) * ann_factor)) if np.std(daily_returns) > 0 else 0.0
    total_return = (equity_curve[-1] / equity_curve[0]) - 1 if equity_curve[0] != 0 else 0.0

    gates: List[GauntletGateResult] = []
    verdict = "VALIDATED"

    # 1. Monte Carlo significance test: bootstrap-resample daily_returns WITH
    # replacement and ask what fraction of resampled means are <= 0. A naive
    # permutation (reordering the same fixed values without replacement)
    # cannot work here -- reordering never changes a set's arithmetic mean,
    # so a permuted mean vs. the actual mean comparison is always true and
    # p_value is always ~1.0 regardless of the strategy's actual quality.
    boot_means = []
    for _ in range(n_permutations):
        resample = rng.choice(daily_returns, size=len(daily_returns), replace=True)
        boot_means.append(np.mean(resample))
    p_value = np.mean([m <= 0 for m in boot_means])
    mc_gate = GauntletGateResult("monte_carlo_permutation", bool(p_value <= p_value_threshold), float(p_value), p_value_threshold, f"p={p_value:.3f}")
    if not mc_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(mc_gate)

    # 2. Vs-random benchmark
    random_port_returns = []
    for _ in range(1000):
        entries = rng.integers(0, len(prices) - holding_period - 1, size=20)
        port_rets = []
        for e in entries:
            end = min(e + holding_period, len(prices))
            pr = prices["Close"].iloc[end - 1] / prices["Close"].iloc[e] - 1
            port_rets.append(pr)
        random_port_returns.append(np.mean(port_rets) if port_rets else 0.0)
    p95_random = np.percentile(random_port_returns, 95)
    # >=, not >: a flat/neutral strategy and its random-entry comparison can
    # both land at exactly 0.0 (e.g. no price movement), a legitimate tie.
    vs_rand_gate = GauntletGateResult("vs_random", bool(total_return >= p95_random), float(total_return), float(p95_random), f"beat 95th percentile ({p95_random:.3f})")
    if not vs_rand_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(vs_rand_gate)

    # 3. Vs-benchmark (Buy-and-Hold)
    if benchmark_prices is not None and len(benchmark_prices) > 0:
        bench_return = (benchmark_prices.iloc[-1]["Close"] / benchmark_prices.iloc[0]["Close"]) - 1
        vs_bench_gate = GauntletGateResult("vs_benchmark", bool(total_return > bench_return + benchmark_outperform_threshold), float(total_return), float(bench_return), f"vs buy-hold {bench_return:.3f}")
    else:
        vs_bench_gate = GauntletGateResult("vs_benchmark", True, float(total_return), 0.0, "skipped (no benchmark data)")
    if not vs_bench_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(vs_bench_gate)

    # 4. Noise test
    noisy_prices = prices * (1 + rng.normal(0, noise_pct, size=prices.shape))
    _, noisy_metrics = backtest_func(noisy_prices, params)
    noisy_sharpe = noisy_metrics.get("sharpe", sharpe)
    max_allowed_drop = 0.5 * sharpe if sharpe >= 0 else 0.0
    noise_gate = GauntletGateResult("noise_sensitivity", bool(sharpe - noisy_sharpe <= max_allowed_drop), float(sharpe - noisy_sharpe), float(max_allowed_drop), f"sharpe drop: {sharpe - noisy_sharpe:.3f}")
    if not noise_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(noise_gate)

    # 5. Parameter sensitivity
    param_gate_passed = True
    param_details = []
    for k, v in params.items():
        for delta in [-param_sensitivity_pct, param_sensitivity_pct]:
            new_params = {**params, k: v * (1 + delta)}
            _, alt_metrics = backtest_func(prices, new_params)
            alt_profitable = alt_metrics.get("total_return", 0.0) > 0
            orig_profitable = total_return > 0
            if alt_profitable != orig_profitable:
                param_gate_passed = False
                param_details.append(f"{k}±{param_sensitivity_pct*100:.0f}% flipped {'profitable' if orig_profitable else 'losing'}")
    param_gate = GauntletGateResult("parameter_sensitivity", param_gate_passed, 0.0, 0.0, "; ".join(param_details) or "stable")
    if not param_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(param_gate)

    # 6. Capacity/liquidity check
    if position_sizes is not None and "Volume" in prices.columns:
        adv = prices["Volume"].rolling(window=20).mean().dropna()
        max_adv_pct = (position_sizes.abs().max() / adv.max()) if adv.max() > 0 else 0.0
        cap_gate = GauntletGateResult("capacity_liquidity", bool(max_adv_pct <= adv_threshold_pct), float(max_adv_pct), float(adv_threshold_pct), f"max {max_adv_pct:.3f} of ADV")
    else:
        cap_gate = GauntletGateResult("capacity_liquidity", True, 0.0, float(adv_threshold_pct), "skipped (no position/volume data)")
    if not cap_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(cap_gate)

    sharpe_ci = _bootstrap_ci(daily_returns, rng=rng)
    total_return_ci = _bootstrap_ci(np.cumprod(1 + daily_returns) - 1, rng=rng)

    # Auto-promote to paper trading on gauntlet pass. Registers a recurring
    # scheduler event via the plugin's own public _create_event -- reaching
    # into scheduler's private connection from cerebral/ is exactly what the
    # seam rule (an explicit S5c acceptance criterion) exists to prevent.
    #
    # S9: this used to call scheduler._run_paper_strategy(...) directly,
    # once, at promotion time -- a strategy could never accumulate the 30
    # trades check_graduation needs from a single call. The real recurring
    # dispatcher (cerebral/main.py's _scheduler_loop, S9) polls
    # scheduler.list_due_events() and calls _run_paper_strategy itself on
    # each due event, using its own shared broker/ForwardRecord -- this
    # call site's only job now is registering that the strategy should be
    # dispatched, not dispatching it. paper_broker is still required to gate
    # auto-promotion (a caller not wanting live trading wired can pass
    # None), even though it's no longer threaded through to the scheduler
    # call -- the dispatcher supplies its own broker instance.
    #
    # No seed fill: ForwardRecord's own __post_init__ already creates the
    # table on construction, so there was never a reason to write one. A
    # fabricated ("INIT", pnl=0) row would have silently counted toward
    # the 30-trade minimum without being a real trade -- the forward
    # record only ever gets real broker fills once actual paper trading
    # starts placing and recording them.
    if auto_promote and verdict == "VALIDATED" and scheduler and paper_broker:
        strategy_name = strategy_id or hypothesis or provenance
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        # Register WHAT to trade before scheduling WHEN. The event carries only
        # a title; without a matching spec the dispatcher has no symbol and no
        # strategy function, and skips the event with "no strategy spec
        # registered" -- an inert schedule, not a fake trade. (It used to buy a
        # literal "SYMBOL" ticker instead.) Callers that don't pass
        # symbol/strategy_code still get their event, still inert; nothing in
        # production calls run_gauntlet yet, so no caller regresses.
        if symbol and strategy_code:
            from cerebral.trading.strategy_store import StrategySpec, StrategyStore
            store = strategy_store if strategy_store is not None else StrategyStore()
            store.save(StrategySpec(
                strategy_id=strategy_name, symbol=symbol,
                code=strategy_code, qty=position_qty,
            ), origin=origin, provenance_json={"source": provenance}, hypothesis=hypothesis,
               parent_version=parent_version, components_json=components_json)
        scheduler._create_event({
            "title": strategy_name,
            "start_iso": now_iso,
            "recurrence": "5m",
        })

    card = StrategyCard(
        hypothesis=hypothesis,
        provenance=provenance,
        gates=gates,
        equity_curve=[float(x) for x in equity_curve],
        sharpe=float(sharpe),
        sharpe_ci=sharpe_ci,
        total_return=float(total_return),
        total_return_ci=total_return_ci,
        verdict=verdict,
    )
    return card
