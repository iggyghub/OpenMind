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
