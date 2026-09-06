"""
Memory manager — Issue #12.

Three-tier memory, all per-profile:
  - Long-term vector (ChromaDB) — facts, preferences, conversation history
  - Structured (SQLite)         — typed preferences queryable by key
  - Short-term (RAM)            — handled by RollingBuffer in audio/pipeline.py

Public interface:
  mgr = MemoryManager(profile_id=1)                        # production
  mgr = MemoryManager(1, db_path=":memory:", chroma_client=EphemeralClient())  # tests

  memory_id = await mgr.remember("My sister's name is Alice")
  memories  = await mgr.recall("sister")                   # → list[Memory]
  ok        = await mgr.edit(memory_id, "Alice is my cousin")  # → bool
  ok        = await mgr.forget(memory_id)                  # → bool
  all_mems  = mgr.list_all()                               # → list[Memory] (sync, newest-first)

  mgr.set_preference("theme", "dark")
  mgr.get_preference("theme")                              # → "dark"
  mgr.list_preferences()                                   # → {"theme": "dark", ...}
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from cerebral.paths import data_dir

DB_PATH = data_dir() / "openmind.db"
CHROMA_PATH = data_dir() / "chroma"


@dataclass
class Memory:
    id: str
    fact: str
    profile_id: int
    created_at: str
    distance: float = 0.0
    category: str = ""  # groups the Memory page (e.g. "money-making idea"); "" = uncategorised
    order: float = 0.0  # manual drag-to-reorder position within a category; see _order_of()


def _order_of(meta: dict | None, created_at: str) -> float:
    """Manual sort position for a memory: an explicit `order` in metadata if the
    item has ever been dragged, else its creation time -- so memories stored
    before drag-reorder existed still sort newest-first by default instead of
    all clustering at 0."""
    meta = meta or {}
    if "order" in meta:
        try:
            return float(meta["order"])
        except (TypeError, ValueError):
            pass
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except (TypeError, ValueError):
        return 0.0


class MemoryManager:
    def __init__(
        self,
        profile_id: int,
        db_path: str | Path = DB_PATH,
        chroma_client: Any = None,
        chroma_path: str | Path = CHROMA_PATH,
    ) -> None:
        self._profile_id = profile_id
        self._collection_name = f"profile_{profile_id}"

        # ChromaDB — vector long-term memory
        if chroma_client is not None:
            self._chroma = chroma_client
        else:
            import chromadb
            Path(chroma_path).mkdir(parents=True, exist_ok=True)
            self._chroma = chromadb.PersistentClient(path=str(chroma_path))

        self._collection = self._chroma.get_or_create_collection(self._collection_name)

        # SQLite — structured preferences
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS preferences (
                profile_id  INTEGER NOT NULL,
                key         TEXT    NOT NULL,
                value       TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (profile_id, key)
            );
        """)
        self._con.commit()

    # ── Vector memory ─────────────────────────────────────────────────────────

    async def remember(self, fact: str, category: str = "") -> str:
        memory_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        meta = {
            "profile_id": self._profile_id,
            "created_at": created_at,
            # New memories start ordered by creation time, same as the implicit
            # sort before manual reordering existed; a drag later overwrites this.
            "order": datetime.now(timezone.utc).timestamp(),
        }
        if category:
            meta["category"] = category
        self._collection.add(
            documents=[fact],
            ids=[memory_id],
            metadatas=[meta],
        )
        logger.info("[memory] Stored fact for profile %d: %r", self._profile_id, fact[:60])
        return memory_id

    async def recall(self, query: str, n_results: int = 5) -> list[Memory]:
        count = self._collection.count()
        if count == 0:
            return []

        actual_n = min(n_results, count)
        results = self._collection.query(
            query_texts=[query],
            n_results=actual_n,
        )

        memories: list[Memory] = []
        ids = results["ids"][0]
        docs = results["documents"][0]
        dists = results["distances"][0]
        metas = results["metadatas"][0]

        for mem_id, doc, dist, meta in zip(ids, docs, dists, metas):
            created_at = meta.get("created_at", "")
            memories.append(Memory(
                id=mem_id,
                fact=doc,
                profile_id=meta.get("profile_id", self._profile_id),
                created_at=created_at,
                distance=dist,
                category=(meta or {}).get("category", ""),
                order=_order_of(meta, created_at),
            ))
        return memories

    async def forget(self, memory_id: str) -> bool:
        try:
            existing = self._collection.get(ids=[memory_id])
            if not existing["ids"]:
                return False
            self._collection.delete(ids=[memory_id])
            logger.info("[memory] Forgot memory %s", memory_id)
            return True
        except Exception:
            logger.exception("[memory] forget() failed for id %s", memory_id)
            return False

    async def edit(self, memory_id: str, new_fact: str) -> bool:
        if not new_fact or not new_fact.strip():
            return False
        try:
            existing = self._collection.get(ids=[memory_id])
            if not existing["ids"]:
                return False
            # chroma >=1.5 update() leaves unspecified fields (metadata,
            # hence created_at) unchanged and re-embeds the new document.
            self._collection.update(ids=[memory_id], documents=[new_fact])
            logger.info("[memory] Edited memory %s", memory_id)
            return True
        except Exception:
            logger.exception("[memory] edit() failed for id %s", memory_id)
            return False

    def list_all(self) -> list[Memory]:
        if self._collection.count() == 0:
            return []
        # collection.get() with no id filter returns flat lists, unlike
        # collection.query() which nests results per query string.
        res = self._collection.get()
        memories = [
            Memory(
                id=mem_id,
                fact=doc,
                profile_id=self._profile_id,
                created_at=(meta or {}).get("created_at", ""),
                category=(meta or {}).get("category", ""),
                order=_order_of(meta, (meta or {}).get("created_at", "")),
            )
            for mem_id, doc, meta in zip(
                res["ids"], res["documents"], res["metadatas"]
            )
        ]
        # `order` defaults to created_at's epoch for anything never manually
        # dragged, so this stays newest-first until a drag actually changes it.
        memories.sort(key=lambda m: m.order, reverse=True)
        return memories

    async def set_category(self, memory_id: str, category: str) -> bool:
        """Move a memory into a (possibly new) category -- assigning a category
        string nobody's used before is how a new folder gets created; there's
        no separate folder-creation call since categories are just a metadata
        field, not their own table."""
        try:
            existing = self._collection.get(ids=[memory_id])
            if not existing["ids"]:
                return False
            meta = dict(existing["metadatas"][0] or {})
            meta["category"] = category
            self._collection.update(ids=[memory_id], metadatas=[meta])
            logger.info("[memory] Moved memory %s to category %r", memory_id, category)
            return True
        except Exception:
            logger.exception("[memory] set_category() failed for id %s", memory_id)
            return False

    async def set_order(self, memory_id: str, order: float) -> bool:
        """Persists a manual drag-to-reorder position. Callers compute `order`
        as the midpoint between the two neighbours the item was dropped
        between (a classic fractional-index scheme), so reordering one item
        never requires rewriting every other item's position."""
        try:
            existing = self._collection.get(ids=[memory_id])
            if not existing["ids"]:
                return False
            meta = dict(existing["metadatas"][0] or {})
            meta["order"] = order
            self._collection.update(ids=[memory_id], metadatas=[meta])
            return True
        except Exception:
            logger.exception("[memory] set_order() failed for id %s", memory_id)
            return False

    async def duplicate(self, memory_id: str, category: str | None = None) -> str | None:
        """Copy/paste: creates an independent second memory with the same fact
        (and, unless overridden, the same category). Returns the new id, or
        None if the source doesn't exist."""
        try:
            existing = self._collection.get(ids=[memory_id])
            if not existing["ids"]:
                return None
            fact = existing["documents"][0]
            meta = existing["metadatas"][0] or {}
            dest_category = category if category is not None else meta.get("category", "")
            return await self.remember(fact, dest_category)
        except Exception:
            logger.exception("[memory] duplicate() failed for id %s", memory_id)
            return None

    # ── Structured preferences ────────────────────────────────────────────────

    def set_preference(self, key: str, value: str) -> None:
        self._con.execute(
            "INSERT INTO preferences (profile_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(profile_id, key) DO UPDATE SET value=excluded.value",
            (self._profile_id, key, value),
        )
        self._con.commit()

    def get_preference(self, key: str, default: str = "") -> str:
        row = self._con.execute(
            "SELECT value FROM preferences WHERE profile_id=? AND key=?",
            (self._profile_id, key),
        ).fetchone()
        return row["value"] if row else default

    def list_preferences(self) -> dict[str, str]:
        rows = self._con.execute(
            "SELECT key, value FROM preferences WHERE profile_id=?",
            (self._profile_id,),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
