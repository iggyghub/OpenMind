"""Strategy evaluation runner.

IMPORTANT: code + bars travel over the child's stdin, not argv. Windows
`CreateProcessW` caps the whole command line at 32,767 characters, and one
symbol's full year of daily bars alone already runs ~36KB base64-encoded --
over the limit on its own, before the strategy code is even added (confirmed
2026-08-24: a real SPY gauntlet run produced a 36,553-char command line and
CreateProcessW failed outright with error 206/ERROR_FILENAME_EXCED_RANGE,
silently degrading every such run to an all-flat signal). stdin has no such
limit. This doesn't change the "no batching tickers through one spawn"
policy -- this sandbox exists to run *generated* untrusted strategy code,
and only tickers that survive the cheap in-process pre-filter get a real
sandboxed evaluation, one spawn each; stdin just fixes how a single
ticker's own payload gets in.
"""
import json
import logging
import shutil
import sys
import uuid
from pathlib import Path
from typing import List

import pandas as pd

from cerebral.sandbox._windows import WindowsSandbox

logger = logging.getLogger(__name__)

# Not data_dir() -- that's under the repo, which isn't AppContainer-traversable
# (the existing AppContainer test fixture's own rationale: the full parent
# directory chain must be readable by the AppContainer SID). Public is.
_WORKDIR_ROOT = Path(r"C:\Users\Public\OpenMind-sbx\trading")

# Runs inline via `python -c` rather than as a script file. A file the PARENT
# writes into the workdir before spawn() does NOT become readable by the
# AppContainer -- _ac_grant_workdir's ACE is added to the per-spawn SID
# *after* the file already exists, and Windows doesn't retroactively
# propagate a folder ACL change onto pre-existing children (confirmed
# empirically: a script file copied into the workdir before spawn() fails
# with STATUS access-denied; a file the CHILD creates itself, after the
# grant is in effect, works fine). So the strategy code and bars data travel
# as one JSON payload over stdin (no argv-length limit, no base64 tax), and
# the only file involved is signals.json, which the child writes itself.
_RUNNER = (
    "import sys, io, json\n"
    "import pandas as pd\n"
    "payload = json.load(sys.stdin)\n"
    "code = payload['code']\n"
    "bars_csv = payload['bars_csv']\n"
    "signals_path = sys.argv[1]\n"
    "bars = pd.read_csv(io.StringIO(bars_csv), index_col=0, parse_dates=True)\n"
    "ns = {}\n"
    "exec(code, ns)\n"
    "signals = ns['strategy'](bars)\n"
    "with open(signals_path, 'w') as f:\n"
    "    json.dump([int(s) for s in list(signals)], f)\n"
    "print('OK')\n"
)


def evaluate_signals(code: str, bars: pd.DataFrame) -> List[int]:
    """Runs strategy `code` against `bars` in a real out-of-process sandbox
    (ADR-0010) -- never exec()s untrusted source in Felix's own address
    space. Any failure (sandbox unavailable, timeout, killed, malformed or
    missing output) degrades to an all-flat signal -- never a crash, and
    never treated as a real trading signal."""
    workdir = _WORKDIR_ROOT / str(uuid.uuid4())
    workdir.mkdir(parents=True, exist_ok=True)

    signals_path = workdir / "signals.json"
    stdin_payload = json.dumps({
        "code": code,
        "bars_csv": bars.to_csv(index=True),
    }).encode("utf-8")

    try:
        sandbox = WindowsSandbox()
        result = sandbox.spawn(
            [sys.executable, "-c", _RUNNER, str(signals_path)],
            str(workdir),
            timeout_s=20,
            stdin_data=stdin_payload,
        )

        if result.exit_code != 0 or result.killed_reason:
            logger.warning(
                "[sandboxed_eval] strategy evaluation failed (exit=%s, killed=%s): %s",
                result.exit_code, result.killed_reason, (result.stderr or "").strip()[:500],
            )
            return [0] * len(bars)

        if not signals_path.exists():
            logger.warning("[sandboxed_eval] no signals.json produced -- degrading to flat")
            return [0] * len(bars)

        signals = json.loads(signals_path.read_text())
        if len(signals) == 0:
            logger.warning("[sandboxed_eval] empty signal list -- degrading to flat")
            return [0] * len(bars)
        if not isinstance(signals, list) or not all(
            isinstance(s, (int, float)) and int(s) in (1, 0, -1) for s in signals
        ):
            logger.warning("[sandboxed_eval] malformed signal output %r -- degrading to flat", signals)
            return [0] * len(bars)

        return signals
    except Exception as exc:
        logger.warning("[sandboxed_eval] evaluation raised %s -- degrading to flat", exc, exc_info=True)
        return [0] * len(bars)
    finally:
        if workdir.exists():
            shutil.rmtree(workdir)
