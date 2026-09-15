import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import pandas as pd

from cerebral.trading.cross_stock_replay import start_cross_stock_replay, stop_cross_stock_replay, get_cross_stock_replay_status, sweep


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Reset internal _state before each test."""
    from cerebral.trading import cross_stock_replay
    cross_stock_replay._state = {
        "running": False,
        "run_id": None,
        "cursor_strategy_idx": 0,
        "cursor_symbol_idx": 0,
        "pairs": [],
        "store": None,
    }
    yield


def _make_spec(strategy_id: str, symbols: list, cross_test_eligible: bool, interval: str = "1d") -> dict:
    return {
        "strategy_id": strategy_id,
        "code": f"print('hello')",
        "symbols": symbols,
        "interval": interval,
        "cross_test_eligible": cross_test_eligible,
    }


class TestCrossStockReplay:
    def test_cursor_resumes_exact_pair_after_restart(self):
        """Cursor must resume from the exact persisted pair, not restart."""
        specs = [_make_spec("s1", ["A", "B"], True)]
        
        with patch("cerebral.trading.cross_stock_replay.CrossStockStore") as MockStore, \
             patch("cerebral.settings.SettingsStore") as MockSettings:
            
            start_cross_stock_replay(specs, cursor_strategy_idx=0, cursor_symbol_idx=1)
            status = get_cross_stock_replay_status()
            assert status["cursor_strategy_idx"] == 0
            assert status["cursor_symbol_idx"] == 1
            assert status["running"] is True
            
            # Simulate stop & restart
            stop_cross_stock_replay()
            assert get_cross_stock_replay_status()["running"] is False
            
            # Restart with persisted cursor
            cross_stock_replay = __import__("cerebral.trading.cross_stock_replay", fromlist=["_state"])
            cross_stock_replay._state["running"] = True
            cross_stock_replay._state["cursor_strategy_idx"] = 0
            cross_stock_replay._state["cursor_symbol_idx"] = 1
            
            # sweep should pick up from index 1
            with patch.object(cross_stock_replay, "_run_single_pair") as mock_run, \
                 patch.object(cross_stock_replay._state["store"], "record_result"):
                mock_run.return_value = {"expectancy": 0.1, "max_drawdown": -0.05, "trade_count": 10}
                sweep()
                
                # Should have only run the second pair (index 1)
                mock_run.assert_called_once()
                assert mock_run.call_args[0][0] is specs[0]
                assert mock_run.call_args[0][1] == "B"

    def test_cross_test_eligible_false_excluded(self):
        """Strategies with cross_test_eligible=False are never included in the pair set."""
        specs = [
            _make_spec("s1", ["A"], True),
            _make_spec("s2", ["A", "B"], False),
            _make_spec("s3", ["C"], True),
        ]
        
        with patch("cerebral.trading.cross_stock_replay.CrossStockStore"):
            start_cross_stock_replay(specs)
            pairs = __import__("cerebral.trading.cross_stock_replay", fromlist=["_state"])._state["pairs"]
            
            # Only s1:A and s3:C should be present
            assert len(pairs) == 2
            assert pairs[0] == (specs[0], "A")
            assert pairs[1] == (specs[2], "C")
            assert specs[1] not in [p[0] for p in pairs]

    def test_pair_failure_does_not_stop_sweep(self):
        """A pair's failure (e.g. missing bars) must not abort the sweep."""
        specs = [_make_spec("s1", ["A", "B"], True)]
        
        with patch("cerebral.trading.cross_stock_replay.CrossStockStore"), \
             patch("cerebral.trading.bar_cache.BarCache.get_bars") as mock_get_bars, \
             patch("cerebral.trading.cross_stock_replay.run_bars") as mock_run_bars, \
             patch.object(__import__("cerebral.trading.cross_stock_replay", fromlist=["_state"])._state["store"], "record_result"):
            
            # A succeeds, B fails
            mock_get_bars.side_effect = [
                pd.DataFrame({"Close": [1.0, 2.0]}),  # A
                None,  # B fails (no bars)
            ]
            mock_run_bars.return_value = ([100.0], pd.Series([]), {"net_returns": 0.1, "max_drawdown": -0.01})
            
            cross_stock_replay = __import__("cerebral.trading.cross_stock_replay", fromlist=["_state"])
            cross_stock_replay._state["running"] = True
            cross_stock_replay._state["pairs"] = [(specs[0], "A"), (specs[0], "B")]
            cross_stock_replay._state["store"] = MagicMock()
            
            sweep()
            
            # Both should have been attempted
            assert mock_get_bars.call_count == 2
            assert cross_stock_replay._state["running"] is False
            assert cross_stock_replay._state["cursor_strategy_idx"] == 0
            assert cross_stock_replay._state["cursor_symbol_idx"] == 2
