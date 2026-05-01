## Parent
#1 — PRD: OpenMind v1

## What to build
Three-tier memory manager, all per-profile: ChromaDB long-term vector store (facts, preferences, conversation history) and SQLite structured store (discrete profile-scoped records). Felix remembers things across sessions.

## Acceptance criteria
- [ ] `remember(fact, profile_id)` stores a fact in ChromaDB under the given profile
- [ ] `recall(query, profile_id)` performs semantic similarity search and returns relevant memories
- [ ] `forget(memory_id)` removes a specific memory record
- [ ] ChromaDB data is isolated per profile — no cross-profile recall
- [ ] SQLite structured store holds preferences, queryable by key
- [ ] Memory persists across Cerebral restarts
- [ ] Demo: tell Felix a fact, restart Cerebral, ask about it — Felix recalls correctly

## Blocked by
- #4 (profile manager)
- #6 (model router)
