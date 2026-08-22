from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

@dataclass
class GateResult:
    name: str
    passed: bool
    metric: float
    threshold: float
    details: str = ""

@dataclass
class StrategyCard:
    hypothesis: str
    provenance: str
    gates: List[GateResult]
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
) -> StrategyCard:
    rng = np.random.default_rng(seed)
    params = params or {}
    equity_curve, metrics = backtest_func(prices, params)
    daily_returns = np.diff(equity_curve) / equity_curve[:-1] if len(equity_curve) > 1 else np.array([0.0])
    sharpe = metrics.get("sharpe", float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252))) if np.std(daily_returns) > 0 else 0.0
    total_return = (equity_curve[-1] / equity_curve[0]) - 1 if equity_curve[0] != 0 else 0.0

    gates: List[GateResult] = []
    verdict = "VALIDATED"

    # 1. Monte Carlo permutation test
    shuffled_means = []
    for _ in range(n_permutations):
        perm_returns = rng.permutation(daily_returns)
        shuffled_means.append(np.mean(perm_returns))
    p_value = np.mean([m >= np.mean(daily_returns) for m in shuffled_means])
    mc_gate = GateResult("monte_carlo_permutation", p_value < p_value_threshold, float(p_value), p_value_threshold, f"p={p_value:.3f}")
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
    vs_rand_gate = GateResult("vs_random", total_return > p95_random, float(total_return), float(p95_random), f"beat 95th percentile ({p95_random:.3f})")
    if not vs_rand_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(vs_rand_gate)

    # 3. Vs-benchmark (Buy-and-Hold)
    if benchmark_prices is not None and len(benchmark_prices) > 0:
        bench_return = (benchmark_prices.iloc[-1]["Close"] / benchmark_prices.iloc[0]["Close"]) - 1
        vs_bench_gate = GateResult("vs_benchmark", total_return > bench_return + benchmark_outperform_threshold, float(total_return), float(bench_return), f"vs buy-hold {bench_return:.3f}")
    else:
        vs_bench_gate = GateResult("vs_benchmark", True, float(total_return), 0.0, "skipped (no benchmark data)")
    if not vs_bench_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(vs_bench_gate)

    # 4. Noise test
    noisy_prices = prices * (1 + rng.normal(0, noise_pct, size=prices.shape))
    _, noisy_metrics = backtest_func(noisy_prices, params)
    noisy_sharpe = noisy_metrics.get("sharpe", sharpe)
    max_allowed_drop = 0.5 * sharpe if sharpe >= 0 else 0.0
    noise_gate = GateResult("noise_sensitivity", sharpe - noisy_sharpe <= max_allowed_drop, float(sharpe - noisy_sharpe), float(max_allowed_drop), f"sharpe drop: {sharpe - noisy_sharpe:.3f}")
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
    param_gate = GateResult("parameter_sensitivity", param_gate_passed, 0.0, 0.0, "; ".join(param_details) or "stable")
    if not param_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(param_gate)

    # 6. Capacity/liquidity check
    if position_sizes is not None and "Volume" in prices.columns:
        adv = prices["Volume"].rolling(window=20).mean().dropna()
        max_adv_pct = (position_sizes.abs().max() / adv.max()) if adv.max() > 0 else 0.0
        cap_gate = GateResult("capacity_liquidity", max_adv_pct <= adv_threshold_pct, float(max_adv_pct), float(adv_threshold_pct), f"max {max_adv_pct:.3f} of ADV")
    else:
        cap_gate = GateResult("capacity_liquidity", True, 0.0, float(adv_threshold_pct), "skipped (no position/volume data)")
    if not cap_gate.passed:
        verdict = "UNVALIDATED"
    gates.append(cap_gate)

    sharpe_ci = _bootstrap_ci(daily_returns, rng=rng)
    total_return_ci = _bootstrap_ci(np.cumprod(1 + daily_returns) - 1, rng=rng)

    return StrategyCard(
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
