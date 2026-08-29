"""Persistent store for the strategy specs the live dispatcher needs.

A scheduler event only carries {title, start_iso, recurrence} -- enough to
say *when* a promoted strategy should be evaluated, nothing about *what* to
evaluate or *which symbol* to trade. This is the companion store that holds
that missing half, keyed by the same string the event uses as its title
(the strategy id).

Deliberately its own tiny table rather than extra columns on the scheduler
plugin's generic `events` table: `scheduler` is a calendar, not a trading
subsystem, and cerebral/ must not depend on plugins/ (seam rule #153/#385).
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import OperationalError
from typing import List, Optional

from cerebral.paths import data_dir

# Module-level Path (not str) so tests can monkeypatch it -- .parent.mkdir()
# below is pathlib-only, the S5c fixture bug.
_DB_PATH = data_dir() / "strategy_specs.db"
_VALID_ORIGINS = ('generated', 'user_edited', 'mixed', 'discovered')

# S39: Convention for expanded strategy_ids
_SUFFIX_RE = re.compile(r"^(.+) @\S+$")

def mint_expansion_strategy_id(claim: str, symbol: str) -> str:
    """Return the expanded `strategy_id` for a new ticker expansion.
    Follows the S42 convention: original claim + space + @ + symbol."""
    return f"{claim} @{symbol}"

def strip_expansion_suffix(strategy_id: str) -> str:
    r"""Strip the trailing ` @SYMBOL` suffix if present, recovering the bare claim.
    Lossless for claims containing `@` elsewhere; only matches a trailing
    ` @\S+` pattern."""
    m = _SUFFIX_RE.match(strategy_id)
    return m.group(1) if m else strategy_id


@dataclass(frozen=True)
class StrategySpec:
    """What the dispatcher needs to evaluate one promoted strategy.

    `code` is the source of a `def strategy(data) -> signals` function --
    see cerebral/trading/live_tick.py for the contract it must satisfy.
    Stored as source, not as a pickled callable: it has to survive a Felix
    restart, and it stays inspectable (a pickled callable is neither).
    """
    strategy_id: str
    symbol: str
    code: str
    qty: float = 1.0
    interval: str = "1d"


class StrategyStore:
    def __init__(self, db_path=None) -> None:
        path = db_path if db_path is not None else _DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_specs (
                strategy_id TEXT PRIMARY KEY,
                symbol      TEXT NOT NULL,
                code        TEXT NOT NULL,
                qty         REAL NOT NULL DEFAULT 1.0,
                interval    TEXT NOT NULL DEFAULT '1d',
                created_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_versions (
                strategy_id       TEXT NOT NULL,
                version           INTEGER NOT NULL,
                code              TEXT NOT NULL,
                origin            TEXT NOT NULL,
                provenance_json   TEXT,
                hypothesis        TEXT,
                parent_version    INTEGER,
                components_json   TEXT,
                created_at        TEXT NOT NULL,
                PRIMARY KEY (strategy_id, version)
            );
            """
        )
        self._con.commit()
        # Migration: add interval column if table exists but lacks it
        try:
            self._con.execute("ALTER TABLE strategy_specs ADD COLUMN interval TEXT NOT NULL DEFAULT '1d'")
            self._con.commit()
        except OperationalError:
            pass  # Column already exists

        # S25 migration: drop legacy CHECK constraint on origin
        try:
            ddl_row = self._con.execute(
                "SELECT sql FROM sqlite_master WHERE name='strategy_versions'"
            ).fetchone()
            if ddl_row and 'user_edited' in ddl_row[0] and 'discovered' not in ddl_row[0]:
                self._con.execute("BEGIN TRANSACTION")
                self._con.execute(
                    "CREATE TABLE strategy_versions_new ("
                    "strategy_id TEXT NOT NULL, version INTEGER NOT NULL, code TEXT NOT NULL, "
                    "origin TEXT NOT NULL, provenance_json TEXT, hypothesis TEXT, "
                    "parent_version INTEGER, components_json TEXT, created_at TEXT NOT NULL, "
                    "PRIMARY KEY (strategy_id, version))"
                )
                self._con.execute("INSERT INTO strategy_versions_new SELECT * FROM strategy_versions")
                self._con.execute("DROP TABLE strategy_versions")
                self._con.execute("ALTER TABLE strategy_versions_new RENAME TO strategy_versions")
                self._con.commit()
        except Exception:
            self._con.rollback()

    def save(self, spec: StrategySpec, origin: str = 'generated', provenance_json=None, hypothesis: str = '', parent_version=None, components_json=None) -> None:
        """Register (or re-register, on re-validation) one strategy, recording lineage."""
        if origin not in _VALID_ORIGINS:
            raise ValueError(f"Invalid origin: {origin!r}. Must be one of {_VALID_ORIGINS}")
        
        max_ver = self._con.execute(
            "SELECT MAX(version) FROM strategy_versions WHERE strategy_id = ?",
            (spec.strategy_id,)
        ).fetchone()[0]
        next_ver = (max_ver or 0) + 1
        
        ts = datetime.now(timezone.utc).isoformat()
        prov_json_str = json.dumps(provenance_json) if provenance_json is not None else None
        comp_json_str = json.dumps(components_json) if components_json is not None else None
        
        self._con.execute(
            "INSERT INTO strategy_versions "
            "(strategy_id, version, code, origin, provenance_json, hypothesis, parent_version, components_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (spec.strategy_id, next_ver, spec.code, origin, prov_json_str, hypothesis, parent_version, comp_json_str, ts),
        )
        self._con.execute(
            "INSERT OR REPLACE INTO strategy_specs "
            "(strategy_id, symbol, code, qty, interval, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (spec.strategy_id, spec.symbol, spec.code, float(spec.qty), spec.interval, ts),
        )
        self._con.commit()

    def render_provenance(self, row) -> str:
        """Produce a composed display string from a strategy_versions row --
        never collapses to one unclear source (the campaign's Honesty rule).
        `row` is a sqlite3.Row (from get_current_version/get_version below),
        which supports dict-style `row[...]` access but NOT `.get()` -- it
        isn't a real Mapping, unlike a plain dict."""
        v = row["version"]
        origin = row["origin"]
        prov = json.loads(row["provenance_json"]) if row["provenance_json"] else {}
        comp = row["components_json"] or ""

        if origin == "generated":
            src = prov.get("source", prov.get("url", prov.get("book", "generated")))
            return f"{src} (v{v})"
        elif origin == "user_edited":
            src = prov.get("source", prov.get("book", "user edit"))
            return f"{src}, as modified by user (v{v})"
        elif origin == "mixed":
            return f"mix of: {comp} (v{v})"
        elif origin == "discovered":
            return f"discovered (v{v})"
        return f"strategy (v{v})"

    def get_current_version(self, strategy_id: str) -> Optional[sqlite3.Row]:
        """The most recent strategy_versions row for strategy_id, or None
        if it's never been saved. The prerequisite S17 (edit)/S18 (mix)
        need to read a strategy's real lineage; nothing built that reader
        without this."""
        return self._con.execute(
            "SELECT * FROM strategy_versions WHERE strategy_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()

    def get(self, strategy_id: str) -> Optional[StrategySpec]:
        row = self._con.execute(
            "SELECT * FROM strategy_specs WHERE strategy_id = ?", (strategy_id,)
        ).fetchone()
        if row is None:
            return None
        return StrategySpec(
            strategy_id=row["strategy_id"], symbol=row["symbol"],
            code=row["code"], qty=row["qty"], interval=row["interval"],
        )

    def list_all(self) -> List[StrategySpec]:
        rows = self._con.execute(
            "SELECT * FROM strategy_specs ORDER BY created_at"
        ).fetchall()
        return [
            StrategySpec(strategy_id=r["strategy_id"], symbol=r["symbol"],
                         code=r["code"], qty=r["qty"], interval=r["interval"])
            for r in rows
        ]

    def close(self) -> None:
        self._con.close()
