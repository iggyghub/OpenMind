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
  ok        = await mgr.forget(memory_id)                  # → bool

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

DB_PATH = Path(__file__).parent.parent / "data" / "openmind.db"
CHROMA_PATH = Path(__file__).parent.parent / "data" / "chroma"


@dataclass
class Memory:
    id: str
    fact: str
    profile_id: int
    created_at: str
    distance: float = 0.0


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

    async def remember(self, fact: str) -> str:
        memory_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        self._collection.add(
            documents=[fact],
            ids=[memory_id],
            metadatas=[{"profile_id": self._profile_id, "created_at": created_at}],
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
            memories.append(Memory(
                id=mem_id,
                fact=doc,
                profile_id=meta.get("profile_id", self._profile_id),
                created_at=meta.get("created_at", ""),
                distance=dist,
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
