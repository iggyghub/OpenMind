"""Spill store for oversized tool output (harness parity H5-S1 / #736).

DeepSeek-harness has `ctx.spillStore`: the backend saves oversized tool text and
hands the model a short locator + retrieval hint instead of the raw payload, so a
huge result (a long file, a web page, a 132k doc) never floods the model context.

OpenMind fed tool results back into the chain raw -- ChainEngine appended
`tool_result.content` to `prior_steps` and the planner re-sent it, bloating the
window and silently truncating the local qwen model. This store is the missing
piece: spill(content) -> locator, retrieve(locator) -> content, size-thresholded.

Backed by the shared openmind.db with the same connect/commit idiom as StepLedger
(cerebral/llm/step_ledger.py). ChainEngine wires it in as an opt-in (spill_store
param); when absent the chain runs exactly as before with no spilling.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cerebral.paths import data_dir

_DEFAULT_DB = data_dir() / "openmind.db"

# Above this many characters a tool result is considered oversized and spilled.
# ponytail: a flat char threshold, not a token count -- chars are free to measure
# and a ~2x overshoot of the real token limit is fine as a spill trigger. Swap in
# a tokenizer estimate here if a model ever truncates below this.
_DEFAULT_THRESHOLD = 8000

_DDL = """
CREATE TABLE IF NOT EXISTS spilled_content (
    locator    TEXT    PRIMARY KEY,
    content    TEXT    NOT NULL,
    size       INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);
"""


class SpillStore:
    """SQLite-backed store of oversized tool output, keyed by an opaque locator."""

    def __init__(self, db_path: Path = _DEFAULT_DB, threshold: int = _DEFAULT_THRESHOLD) -> None:
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_DDL)
        self.threshold = threshold

    def should_spill(self, content: object) -> bool:
        """True if content is a string longer than the threshold."""
        return isinstance(content, str) and len(content) > self.threshold

    def spill(self, content: str) -> str:
        """Persist content and return a fresh model-facing locator."""
        locator = "spill:" + uuid.uuid4().hex[:12]
        self._con.execute(
            "INSERT INTO spilled_content (locator, content, size, created_at) "
            "VALUES (?, ?, ?, ?)",
            (locator, content, len(content), datetime.now(timezone.utc).isoformat()),
        )
        self._con.commit()
        return locator

    def retrieve(self, locator: str) -> "str | None":
        """Full stored text for a locator, or None if the locator is unknown."""
        row = self._con.execute(
            "SELECT content FROM spilled_content WHERE locator = ?", (locator,)
        ).fetchone()
        return row["content"] if row is not None else None

    def hint(self, locator: str, size: int) -> str:
        """Short replacement string the model sees in place of the raw output."""
        return (
            f"[Tool output too large ({size} chars) -- stored as {locator}. "
            f"Call retrieve_spilled with locator={locator!r} to read the full text.]"
        )
