"""Recipes storage -- S3 core loop (Issue #276).

A Recipe is a saved, named, user-approved chain: a frozen literal sequence of
tool calls that Felix can re-run on command. Stored per-profile in SQLite
alongside the ACL / memory / conversation tables (consent belongs with identity).

Steps are stored as JSON ``[{"tool_name": ..., "args": {...}}, ...]``.
Frozen args still yield fresh results: a stored ``gmail_search(query="is:unread")``
re-runs against today's mail on replay.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "openmind.db"

STALE_DAYS = 30


@dataclass
class Recipe:
    id: int
    profile_id: int
    name: str
    steps: list[dict]
    created_at: str
    run_count: int
    last_run_at: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "name": self.name,
            "steps": self.steps,
            "created_at": self.created_at,
            "run_count": self.run_count,
            "last_run_at": self.last_run_at,
        }

    @property
    def synthetic_tool_name(self) -> str:
        safe = self.name.lower().replace(" ", "_")
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
        return f"recipe_{safe}"


def _steps_fingerprint(steps: list[dict]) -> str:
    """Canonical JSON string used for exact-duplicate detection."""
    return json.dumps(
        [{"tool_name": s["tool_name"], "args": s["args"]} for s in steps],
        sort_keys=True,
    )


class RecipeStore:
    """Per-profile Recipe persistence.

    One instance per Cerebral process; profile_id is a per-call argument so a
    profile switch doesn't require a rebuild.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS recipes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id   INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                steps_json   TEXT    NOT NULL DEFAULT '[]',
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                run_count    INTEGER NOT NULL DEFAULT 0,
                last_run_at  DATETIME,
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_profile_name
                ON recipes (profile_id, name);
        """)
        self._con.commit()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def save(self, profile_id: int, name: str, steps: list[dict]) -> Recipe:
        """Insert a new Recipe. Raises ValueError on duplicate name within profile."""
        if not name:
            raise ValueError("Recipe name must be non-empty")
        if len(steps) < 2:
            raise ValueError("Recipes require at least 2 steps")
        try:
            cur = self._con.execute(
                "INSERT INTO recipes (profile_id, name, steps_json) VALUES (?, ?, ?)",
                (profile_id, name, json.dumps(steps)),
            )
            self._con.commit()
            return self._get(cur.lastrowid)  # type: ignore[arg-type]
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Recipe '{name}' already exists for this profile") from exc

    def get(self, recipe_id: int) -> Recipe | None:
        row = self._con.execute(
            "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        return _row_to_recipe(row) if row else None

    def get_by_name(self, profile_id: int, name: str) -> Recipe | None:
        row = self._con.execute(
            "SELECT * FROM recipes WHERE profile_id = ? AND name = ?",
            (profile_id, name),
        ).fetchone()
        return _row_to_recipe(row) if row else None

    def get_by_synthetic_name(
        self, profile_id: int, synthetic_name: str
    ) -> Recipe | None:
        """Find a Recipe by its synthetic tool name (e.g. ``recipe_morning_briefing``)."""
        for recipe in self.list_for_profile(profile_id):
            if recipe.synthetic_tool_name == synthetic_name:
                return recipe
        return None

    def list_for_profile(self, profile_id: int) -> list[Recipe]:
        rows = self._con.execute(
            "SELECT * FROM recipes WHERE profile_id = ? ORDER BY name ASC",
            (profile_id,),
        ).fetchall()
        return [_row_to_recipe(r) for r in rows]

    def rename(self, recipe_id: int, new_name: str) -> bool:
        """Rename a Recipe. Returns True iff a row was updated."""
        if not new_name:
            return False
        try:
            cur = self._con.execute(
                "UPDATE recipes SET name = ? WHERE id = ?",
                (new_name, recipe_id),
            )
            self._con.commit()
            return cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def delete(self, recipe_id: int) -> bool:
        """Delete a Recipe. Returns True iff a row was deleted."""
        cur = self._con.execute(
            "DELETE FROM recipes WHERE id = ?", (recipe_id,)
        )
        self._con.commit()
        return cur.rowcount > 0

    def record_run(self, recipe_id: int) -> None:
        """Increment run_count and update last_run_at to now."""
        self._con.execute(
            "UPDATE recipes SET run_count = run_count + 1, "
            "last_run_at = CURRENT_TIMESTAMP WHERE id = ?",
            (recipe_id,),
        )
        self._con.commit()

    # ── Synthetic tool exposure ───────────────────────────────────────────────

    def get_synthetic_tools(self, profile_id: int) -> list[dict]:
        """Return planner-ready tool schemas for every Recipe in this profile.

        Each Recipe becomes a zero-argument tool named ``recipe_<safe_name>``
        that the planner can pick just like a real tool.
        """
        tools = []
        for recipe in self.list_for_profile(profile_id):
            step_names = ", ".join(s["tool_name"] for s in recipe.steps)
            tools.append(
                {
                    "name": recipe.synthetic_tool_name,
                    "description": (
                        f"Run the '{recipe.name}' Recipe "
                        f"({len(recipe.steps)} steps: {step_names})"
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                }
            )
        return tools

    # ── Panel hygiene flags ───────────────────────────────────────────────────

    def stale_ids(self, profile_id: int) -> set[int]:
        """Return ids of Recipes unused for more than STALE_DAYS days.

        A Recipe is stale when both of these are true:
        - run_count == 0 and created_at is more than STALE_DAYS old, OR
        - last_run_at is more than STALE_DAYS old.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        rows = self._con.execute(
            """SELECT id FROM recipes
                WHERE profile_id = ?
                  AND (
                       (run_count = 0 AND created_at < ?)
                    OR (last_run_at IS NOT NULL AND last_run_at < ?)
                  )""",
            (profile_id, cutoff_str, cutoff_str),
        ).fetchall()
        return {r["id"] for r in rows}

    def duplicate_ids(self, profile_id: int) -> set[int]:
        """Return ids of Recipes whose step sequence is shared by another Recipe.

        Both the earlier and later Recipe get flagged so the panel can show the
        user which two are redundant.
        """
        recipes = self.list_for_profile(profile_id)
        fingerprints: dict[str, list[int]] = {}
        for r in recipes:
            fp = _steps_fingerprint(r.steps)
            fingerprints.setdefault(fp, []).append(r.id)

        dup_ids: set[int] = set()
        for ids in fingerprints.values():
            if len(ids) > 1:
                dup_ids.update(ids)
        return dup_ids

    # ── Private ───────────────────────────────────────────────────────────────

    def _get(self, recipe_id: int) -> Recipe:
        row = self._con.execute(
            "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Recipe {recipe_id} not found")
        return _row_to_recipe(row)


def _row_to_recipe(row: sqlite3.Row) -> Recipe:
    try:
        steps = json.loads(row["steps_json"]) if row["steps_json"] else []
    except (ValueError, TypeError):
        steps = []
    return Recipe(
        id=row["id"],
        profile_id=row["profile_id"],
        name=row["name"],
        steps=steps,
        created_at=row["created_at"] or "",
        run_count=row["run_count"] or 0,
        last_run_at=row["last_run_at"],
    )
