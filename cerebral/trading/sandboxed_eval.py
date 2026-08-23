import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import List

import pandas as pd

from cerebral.sandbox._windows import WindowsSandbox

_WORKDIR_ROOT = Path(r"C:\Users\Public\OpenMind-sbx\trading")


def evaluate_signals(code: str, bars: pd.DataFrame) -> List[int]:
    workdir = _WORKDIR_ROOT / str(uuid.uuid4())
    workdir.mkdir(parents=True, exist_ok=True)

    strategy_path = workdir / "strategy.py"
    strategy_path.write_text(code)

    bars_path = workdir / "bars.csv"
    bars.to_csv(bars_path, index=True)

    runner_path = Path(__file__).parent / "_strategy_runner.py"

    try:
        sandbox = WindowsSandbox()
        result = sandbox.spawn(
            [sys.executable, str(runner_path), str(workdir)],
            timeout=20,
        )

        if result.exit_code != 0 or result.killed_reason:
            return [0] * len(bars)

        signals_path = workdir / "signals.json"
        if not signals_path.exists():
            return [0] * len(bars)

        signals = json.loads(signals_path.read_text())
        if not isinstance(signals, list):
            return [0] * len(bars)
        if not all(isinstance(s, int) and s in (1, 0, -1) for s in signals):
            return [0] * len(bars)

        return signals
    except Exception:
        return [0] * len(bars)
    finally:
        if workdir.exists():
            shutil.rmtree(workdir)
