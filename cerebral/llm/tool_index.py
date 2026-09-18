import chromadb

_DB_PATH = ".chroma_tool_index"
_COLLECTION = "tool_catalog"

_client = chromadb.PersistentClient(path=_DB_PATH)


def rank(transcript: str, tools: list[dict], limit: int = 30) -> list[dict]:
    """Rank tools by embedding similarity to the transcript.
    
    Populates the Chroma collection on first call if needed.
    Raises on Chroma failure so the caller can fall back to lexical scoring.
    """
    coll = _client.get_or_create_collection(
        name=_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    
    # Populate missing tools
    current_count = coll.count()
    if current_count < len(tools):
        to_add = tools[current_count:]
        coll.upsert(
            ids=[str(i) for i in range(current_count, len(tools))],
            documents=[f"{t.get('name', '')} {t.get('description', '')}" for t in to_add],
        )
        
    results = coll.query(query_texts=[transcript], n_results=limit)
    ranked_ids = results["ids"][0]
    if not ranked_ids:
        return list(tools)
    return [tools[int(idx)] for idx in ranked_ids if int(idx) < len(tools)]
