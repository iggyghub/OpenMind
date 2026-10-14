"""The 'last N turns' readers must not sort a whole thread (measured ~9s on a 36k-turn thread)."""
from cerebral.db.conversation import ConversationStore


def _plan(store, sql, params):
    return " ".join(str(r[3]) for r in store._con.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_thread_recent_query_uses_the_id_index_without_a_sort(tmp_path):
    store = ConversationStore(db_path=tmp_path / "c.db")
    plan = _plan(
        store,
        "SELECT * FROM conversation_turns WHERE thread_id=? ORDER BY id DESC LIMIT ?",
        (1, 50),
    )
    assert "idx_conversation_turns_thread_id" in plan
    assert "TEMP B-TREE" not in plan


def test_profile_recent_query_uses_the_id_index_without_a_sort(tmp_path):
    store = ConversationStore(db_path=tmp_path / "c.db")
    plan = _plan(
        store,
        "SELECT * FROM conversation_turns WHERE profile_id=? ORDER BY id DESC LIMIT ?",
        (1, 50),
    )
    assert "idx_conversation_turns_profile_id" in plan
    assert "TEMP B-TREE" not in plan
