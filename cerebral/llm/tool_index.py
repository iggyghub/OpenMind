"""Embedding ranking of tool name+description for ``planner.shortlist_tools`` (F3).

Raises on any Chroma/embedder failure -- the caller falls back to lexical scoring.

Measured 2026-09-18: embedding ~311 tools costs ~21s on CPU, so the index is
persistent and incremental: only tools whose text is new or changed are
embedded (~65ms each), never the whole registry per turn.
"""
from cerebral.paths import data_dir

INDEX_PATH = data_dir() / "tool_index"
_state: dict = {"coll": None}


def _collection():
    if _state["coll"] is None:
        import chromadb

        INDEX_PATH.mkdir(parents=True, exist_ok=True)
        _state["coll"] = chromadb.PersistentClient(path=str(INDEX_PATH)).get_or_create_collection(
            "tools", metadata={"hnsw:space": "cosine"}
        )
    return _state["coll"]


def rank(transcript: str, tools: list[dict], limit: int = 30) -> list[dict]:
    """Return up to ``limit`` of ``tools`` ordered by embedding distance to ``transcript``."""
    by_name = {t["name"]: t for t in tools}
    docs = {n: f"{n.replace('_', ' ')} {t.get('description') or ''}" for n, t in by_name.items()}
    coll = _collection()
    have = coll.get(ids=list(docs))
    stale = dict(docs)
    for i, d in zip(have["ids"], have["documents"]):
        if docs[i] == d:
            del stale[i]
    if stale:
        coll.upsert(ids=list(stale), documents=list(stale.values()))
    # The persistent index may hold tools no longer registered: fetch all, filter to ``tools``.
    res = coll.query(query_texts=[transcript], n_results=coll.count())
    return [by_name[n] for n in res["ids"][0] if n in by_name][:limit]
