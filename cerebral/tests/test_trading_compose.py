import json
import sys
import os
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from cerebral.trading.compose import compose_strategies

def test_right_alignment_shortest_length():
    """Right-aligns to shortest component length. LAST elements compared."""
    comp1 = "def strategy(d): return [1]*240"
    comp2 = "def strategy(d): return [2]*200"
    code = compose_strategies([("c1", comp1), ("c2", comp2)], "unanimous")
    ns = {}
    exec(code, ns)
    sigs = ns["strategy"]({})
    assert len(sigs) == 200
    assert all(s == 0 for s in sigs), "Unanimous mismatch -> 0"

def test_majority_alignment_and_logic():
    """Majority mode aligns to shortest and computes sign(sum)."""
    comp1 = "def strategy(d): return [1, 1, 1, 0, 0]"
    comp2 = "def strategy(d): return [1, 1, 0, 0, 0, 0]"
    # comp1 len=5, comp2 len=6 -> align to 5. comp2 last 5: [1, 0, 0, 0, 0]
    # Sums: 2, 1, 1, 0, 0 -> signs: 1, 1, 1, 0, 0
    code = compose_strategies([("c1", comp1), ("c2", comp2)], "majority")
    ns = {}
    exec(code, ns)
    sigs = ns["strategy"]({})
    assert sigs == [1, 1, 1, 0, 0]

def test_unanimous_mode_exact_agreement():
    """Unanimous returns value only when all match, else 0."""
    comp1 = "def strategy(d): return [1, 0, 1, -1]"
    comp2 = "def strategy(d): return [1, 0, 2, -1]" # 3rd differs
    code = compose_strategies([("c1", comp1), ("c2", comp2)], "unanimous")
    ns = {}
    exec(code, ns)
    sigs = ns["strategy"]({})
    assert sigs == [1, 0, 0, -1]

@pytest.mark.asyncio
async def test_mix_strategies_rejects_mismatched_symbols():
    """mix_strategies rejects different symbols."""
    from plugins.scheduler import SchedulerPlugin
    from cerebral.trading.strategy_store import StrategyStore
    
    plugin = SchedulerPlugin(db_path=":memory:")
    store = MagicMock(spec=StrategyStore)
    store.get.side_effect = lambda sid: MagicMock(symbol="AAPL" if sid == "s1" else "GOOG", code="def strategy(d): pass")
    store.get_current_version.side_effect = lambda sid: {"version": 1}
    store.render_provenance.return_value = "prov_1"
    
    result = await plugin._run_mix_strategies(
        {"component_ids": ["s1", "s2"], "mode": "unanimous"},
        strategy_store=store
    )
    assert result.is_error is True
    assert "Mismatched symbols" in result.content

@pytest.mark.asyncio
async def test_mix_strategies_provenance_includes_components():
    """Provenance string contains every component's identity."""
    from plugins.scheduler import SchedulerPlugin
    from cerebral.trading.strategy_store import StrategyStore
    
    plugin = SchedulerPlugin(db_path=":memory:")
    store = MagicMock(spec=StrategyStore)
    comp1_id = "strat_alpha"
    comp2_id = "strat_beta"
    store.get.side_effect = lambda sid: MagicMock(symbol="AAPL", code="def strategy(d): pass")
    store.get_current_version.side_effect = lambda sid: {"version": 1}
    store.render_provenance.return_value = "prov_1"
    
    gauntlet_called_with = {}
    async def capture_gauntlet(args, **kwargs):
        gauntlet_called_with.update(args)
        return MagicMock(content=json.dumps({"verdict": "VALIDATED"}), is_error=False)
    
    plugin._run_gauntlet = capture_gauntlet
    
    result = await plugin._run_mix_strategies(
        {"component_ids": [comp1_id, comp2_id], "mode": "majority"},
        strategy_store=store
    )
    
    assert "Mixed strategy" in gauntlet_called_with.get("provenance", "")
    assert comp1_id in gauntlet_called_with.get("provenance", "")
    assert comp2_id in gauntlet_called_with.get("provenance", "")


def _trend_prices(n=200, seed=42):
    """Same fixture cerebral/tests/test_plugin_scheduler.py's S11c tests use --
    a clean regime change a trend-following MA-cross strategy reliably clears
    the full gauntlet against."""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    up = rng.normal(0.004, 0.008, n // 2)
    down = rng.normal(-0.004, 0.008, n - n // 2)
    close = 100 * np.cumprod(1 + np.concatenate([up, down]))
    return pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": np.full(n, 2000),
    })


MA_CROSS_CODE = (
    "def strategy(data):\n"
    "    fast = data['Close'].rolling(10).mean()\n"
    "    slow = data['Close'].rolling(30).mean()\n"
    "    return (fast > slow).astype(int).tolist()\n"
)


@pytest.mark.asyncio
async def test_mix_strategies_end_to_end_persists_real_components_json(tmp_path):
    """The real (unmocked) chain: two strategies registered via the real
    run_gauntlet, mixed via the real _run_mix_strategies, and the resulting
    strategy_versions row's real components_json -- not a mocked capture of
    the provenance argument -- actually names both components. This is the
    gap the mocked tests above can't see: the original implementation built
    a components_json string but never passed it into run_gauntlet at all,
    so strategy_versions.components_json stayed NULL for every real mix and
    render_provenance could never actually name a component.

    Both components register the SAME code under different strategy_ids
    (deterministic: unanimous-mode composition of a signal with itself
    reproduces the exact original signal) so the composite's own gauntlet
    pass doesn't depend on two different strategies' signals happening to
    agree often enough -- that risk is irrelevant to what this test proves
    (the components_json plumbing), so it's deliberately eliminated rather
    than left to chance."""
    from plugins.scheduler import SchedulerPlugin
    from cerebral.trading.strategy_store import StrategyStore

    plugin = SchedulerPlugin(db_path=str(tmp_path / "sched.db"))
    store = StrategyStore(db_path=tmp_path / "specs.db")

    def fetch(symbol, start, end, interval="1d"):
        return _trend_prices()

    r1 = await plugin._run_gauntlet(
        {"code": MA_CROSS_CODE, "symbol": "AAPL", "hypothesis": "MA cross A"},
        strategy_store=store, fetch=fetch,
    )
    r2 = await plugin._run_gauntlet(
        {"code": MA_CROSS_CODE, "symbol": "AAPL", "hypothesis": "MA cross B"},
        strategy_store=store, fetch=fetch,
    )
    assert json.loads(r1.content)["verdict"] == "VALIDATED"
    assert json.loads(r2.content)["verdict"] == "VALIDATED"

    result = await plugin._run_mix_strategies(
        {"component_ids": ["MA cross A", "MA cross B"], "mode": "unanimous"},
        strategy_store=store, fetch=fetch,
    )

    assert not result.is_error, result.content
    assert json.loads(result.content)["verdict"] == "VALIDATED"

    new_id = next(
        c.strategy_id for c in store.list_all()
        if c.strategy_id not in ("MA cross A", "MA cross B")
    )
    version_row = store.get_current_version(new_id)
    assert version_row["origin"] == "mixed"
    provenance = store.render_provenance(version_row)
    assert "MA cross A" in provenance
    assert "MA cross B" in provenance
