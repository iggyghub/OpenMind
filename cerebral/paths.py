"""Resolver for the mutable data directory (SQLite, Chroma, JSON stores).

Every store defaults its path from here at import time. OPENMIND_DATA_DIR
overrides the default ``cerebral/data`` — the test suite's conftest sets it
to a throwaway temp dir so importing ``cerebral.main`` (which constructs
module-level stores) can never touch the real user state.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("OPENMIND_DATA_DIR")
    return Path(override) if override else Path(__file__).parent / "data"
