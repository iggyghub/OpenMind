import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from cerebral.trading.sandboxed_eval import evaluate_signals

MA_STRATEGY_CODE = """
def strategy(data):
    fast = data['close'].rolling(5).mean()
    slow = data['close'].rolling(20).mean()
    signals = []
    for i in range(len(data)):
        if pd.isna(fast.iloc[i]) or pd.isna(slow.iloc[i]):
            signals.append(0)
        elif fast.iloc[i] > slow.iloc[i] and slow.iloc[i] > (fast.iloc[i-1] if i > 0 else 0):
            signals.append(1)
        elif fast.iloc[i] < slow.iloc[i] and slow.iloc[i] < (fast.iloc[i-1] if i > 0 else 0):
            signals.append(-1)
        else:
            signals.append(0)
    return signals
"""


@pytest.fixture
def trend_prices():
    """Replicates the _trend_prices-style fixture from test_plugin_scheduler.py"""
    dates = pd.date_range("2023-01-01", periods=50, freq="D")
    return pd.DataFrame({
        "open": range(100, 150),
        "high": range(105, 155),
        "low": range(95, 145),
        "close": range(102, 152),
        "volume": [1000] * 50
    }, index=dates)


def _compute_expected_signals(prices):
    fast = prices['close'].rolling(5).mean()
    slow = prices['close'].rolling(20).mean()
    expected = []
    for i in range(len(prices)):
        if pd.isna(fast.iloc[i]) or pd.isna(slow.iloc[i]):
            expected.append(0)
        elif fast.iloc[i] > slow.iloc[i] and i > 0 and slow.iloc[i] > fast.iloc[i-1]:
            expected.append(1)
        elif fast.iloc[i] < slow.iloc[i] and i > 0 and slow.iloc[i] < fast.iloc[i-1]:
            expected.append(-1)
        else:
            expected.append(0)
    return expected


def test_evaluate_signals_produces_identical_output(trend_prices):
    expected = _compute_expected_signals(trend_prices)

    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)
        signals_path = workdir / "signals.json"
        signals_path.write_text(json.dumps(expected))

        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.killed_reason = None
        mock_sandbox.spawn.return_value = mock_result

        with patch("cerebral.trading.sandboxed_eval.WindowsSandbox", return_value=mock_sandbox):
            with patch("cerebral.trading.sandboxed_eval._WORKDIR_ROOT", workdir):
                result = evaluate_signals(MA_STRATEGY_CODE, trend_prices)
                assert result == expected


def test_containment_of_malicious_strategy(trend_prices):
    malicious_code = """
def strategy(data):
    import os
    with open("C:/Windows/x", "w") as f:
        f.write("hacked")
    return [0]
"""
    with patch("cerebral.trading.sandboxed_eval.WindowsSandbox") as MockSandbox:
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.killed_reason = "timeout"
        mock_sandbox.spawn.return_value = mock_result

        with patch("cerebral.trading.sandboxed_eval._WORKDIR_ROOT", Path(tempfile.gettempdir())):
            result = evaluate_signals(malicious_code, trend_prices)
            assert all(s == 0 for s in result)
            # Verify containment: no real filesystem side-effects
            assert not Path("C:/Windows/x").exists()


def test_malformed_signals_degrades_to_flat(trend_prices):
    with patch("cerebral.trading.sandboxed_eval.WindowsSandbox") as MockSandbox:
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.killed_reason = None
        mock_sandbox.spawn.return_value = mock_result

        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            signals_path = workdir / "signals.json"
            signals_path.write_text('["not a number"]')

            with patch("cerebral.trading.sandboxed_eval._WORKDIR_ROOT", workdir):
                result = evaluate_signals(MA_STRATEGY_CODE, trend_prices)
                assert all(s == 0 for s in result)
