import base64
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import List

import pandas as pd

from cerebral.sandbox._windows import WindowsSandbox

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
# grant is in effect, works fine). So both the strategy code and the bars
# data travel as argv (base64, to survive command-line quoting), and the
# only file involved is signals.json, which the child writes itself.
_RUNNER = (
    "import sys, base64, io, json\n"
    "import pandas as pd\n"
    "code = base64.b64decode(sys.argv[1]).decode('utf-8')\n"
    "bars_csv = base64.b64decode(sys.argv[2]).decode('utf-8')\n"
    "signals_path = sys.argv[3]\n"
    "bars = pd.read_csv(io.StringIO(bars_csv), index_col=0, parse_dates=True)\n"
    "ns = {}\n"
    "exec(code, ns)\n"
    "signals = ns['strategy'](bars)\n"
    "with open(signals_path, 'w') as f:\n"
    "    json.dump(signals, f)\n"
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
    b64_code = base64.b64encode(code.encode("utf-8")).decode("ascii")
    b64_bars = base64.b64encode(bars.to_csv(index=True).encode("utf-8")).decode("ascii")

    try:
        sandbox = WindowsSandbox()
        result = sandbox.spawn(
            [sys.executable, "-c", _RUNNER, b64_code, b64_bars, str(signals_path)],
            str(workdir),
            timeout_s=20,
        )

        if result.exit_code != 0 or result.killed_reason:
            return [0] * len(bars)

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
