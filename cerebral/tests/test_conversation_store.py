"""ConversationStore tests -- Issue #185 / ADR-0007.

Locks the per-profile transcript persistence contract: append validates
kind, list_recent returns oldest-first capped at limit, per-profile
isolation, FK cascade on profile delete.
"""
from __future__ import annotations

import sqlite3

import pytest

from cerebral.db.conversation import (
    KIND_FELIX_SPEECH,
    KIND_USER_TEXT,
    KIND_USER_VOICE,
    ConversationStore,
)


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "openmind.db"
    # Seed a profiles table so FK references resolve. We don't need the
    # real schema -- just the id column.
    con = sqlite3.connect(str(db))
    con.executescript(
        "PRAGMA foreign_keys=ON;"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        "INSERT INTO profiles (id) VALUES (1);"
        "INSERT INTO profiles (id) VALUES (2);"
    )
    con.commit()
    con.close()
    return ConversationStore(db_path=db)


def test_append_returns_persisted_turn(store):
    turn = store.append(1, KIND_USER_TEXT, {"text": "hello"})
    assert turn.id > 0
    assert turn.profile_id == 1
    assert turn.kind == KIND_USER_TEXT
    assert turn.content == {"text": "hello"}
    assert turn.ts  # CURRENT_TIMESTAMP populated


def test_append_rejects_unknown_kind(store):
    with pytest.raises(ValueError, match="unknown conversation kind"):
        store.append(1, "garbage", {})


def test_list_recent_returns_oldest_first(store):
    a = store.append(1, KIND_USER_TEXT, {"text": "first"})
    b = store.append(1, KIND_FELIX_SPEECH, {"text": "reply"})
    c = store.append(1, KIND_USER_VOICE, {"text": "second"})
    turns = store.list_recent(1, limit=10)
    assert [t.id for t in turns] == [a.id, b.id, c.id]


def test_list_recent_caps_at_limit_keeping_newest(store):
    ids = [store.append(1, KIND_USER_TEXT, {"text": f"t{i}"}).id for i in range(5)]
    turns = store.list_recent(1, limit=3)
    # newest 3 in oldest-first order
    assert [t.id for t in turns] == ids[-3:]


def test_list_recent_zero_limit_returns_empty(store):
    store.append(1, KIND_USER_TEXT, {"text": "hi"})
    assert store.list_recent(1, limit=0) == []


def test_list_recent_isolates_by_profile(store):
    store.append(1, KIND_USER_TEXT, {"text": "p1"})
    store.append(2, KIND_USER_TEXT, {"text": "p2"})
    assert [t.content["text"] for t in store.list_recent(1)] == ["p1"]
    assert [t.content["text"] for t in store.list_recent(2)] == ["p2"]


def test_purge_drops_only_target_profile(store):
    store.append(1, KIND_USER_TEXT, {"text": "x"})
    store.append(1, KIND_USER_TEXT, {"text": "y"})
    store.append(2, KIND_USER_TEXT, {"text": "z"})
    deleted = store.purge(1)
    assert deleted == 2
    assert store.list_recent(1) == []
    assert len(store.list_recent(2)) == 1


def test_content_with_unicode_roundtrips(store):
    turn = store.append(1, KIND_FELIX_SPEECH, {"text": "héllo — 世界"})
    [reloaded] = store.list_recent(1)
    assert reloaded.content["text"] == turn.content["text"] == "héllo — 世界"


# ── S9 / #292 -- conversation threads ────────────────────────────────────────


def test_create_thread_persists_metadata(store):
    thread = store.create_thread(1, title="Trip planning")
    assert thread.id > 0
    assert thread.profile_id == 1
    assert thread.title == "Trip planning"
    assert thread.created_at
    assert thread.updated_at


def test_list_threads_orders_by_updated_desc(store):
    a = store.create_thread(1, title="first")
    b = store.create_thread(1, title="second")
    # Touch `a` so it becomes the most-recently-updated.
    store.append(1, KIND_USER_TEXT, {"text": "ping"}, thread_id=a.id)
    threads = store.list_threads(1)
    assert [t.id for t in threads] == [a.id, b.id]


def test_list_threads_isolates_by_profile(store):
    t1 = store.create_thread(1, title="p1 thread")
    t2 = store.create_thread(2, title="p2 thread")
    assert [t.id for t in store.list_threads(1)] == [t1.id]
    assert [t.id for t in store.list_threads(2)] == [t2.id]


def test_rename_thread_updates_title(store):
    t = store.create_thread(1, title="")
    assert store.rename_thread(t.id, "Renamed")
    assert store.get_thread(t.id).title == "Renamed"


def test_append_without_thread_id_uses_default_thread(store):
    # No thread exists yet -- append must create one and tag the turn.
    turn = store.append(1, KIND_USER_TEXT, {"text": "hi"})
    assert turn.thread_id is not None
    threads = store.list_threads(1)
    assert len(threads) == 1
    assert threads[0].id == turn.thread_id


def test_append_with_explicit_thread_id_attaches_to_that_thread(store):
    a = store.create_thread(1, title="A")
    b = store.create_thread(1, title="B")
    store.append(1, KIND_USER_TEXT, {"text": "in A"}, thread_id=a.id)
    store.append(1, KIND_USER_TEXT, {"text": "in B"}, thread_id=b.id)
    in_a = store.list_recent_for_thread(a.id)
    in_b = store.list_recent_for_thread(b.id)
    assert [t.content["text"] for t in in_a] == ["in A"]
    assert [t.content["text"] for t in in_b] == ["in B"]


def test_auto_title_set_from_first_user_turn_on_felix_speech(store):
    t = store.create_thread(1, title="")
    store.append(1, KIND_USER_TEXT,    {"text": "Plan my Tokyo trip"}, thread_id=t.id)
    store.append(1, KIND_FELIX_SPEECH, {"text": "Sure!"},               thread_id=t.id)
    assert store.get_thread(t.id).title == "Plan my Tokyo trip"


def test_auto_title_truncates_long_first_turn(store):
    t = store.create_thread(1, title="")
    long_text = "x" * 200
    store.append(1, KIND_USER_TEXT,    {"text": long_text}, thread_id=t.id)
    store.append(1, KIND_FELIX_SPEECH, {"text": "ok"},      thread_id=t.id)
    title = store.get_thread(t.id).title
    assert title.endswith("...")
    assert len(title) <= 64  # 60-char cap + ellipsis


def test_auto_title_does_not_overwrite_user_edit(store):
    t = store.create_thread(1, title="My custom title")
    store.append(1, KIND_USER_TEXT,    {"text": "anything"}, thread_id=t.id)
    store.append(1, KIND_FELIX_SPEECH, {"text": "ack"},      thread_id=t.id)
    assert store.get_thread(t.id).title == "My custom title"


def test_migration_backfills_pre_s9_turns_into_legacy_thread(tmp_path):
    """A DB created before S9 has no thread_id column and no threads table.
    ConversationStore must migrate non-destructively: turns keep their
    content and gain a thread_id pointing at a single per-profile
    'Legacy conversation' thread."""
    db = tmp_path / "openmind.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        "PRAGMA foreign_keys=ON;"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        "INSERT INTO profiles (id) VALUES (1);"
        "INSERT INTO profiles (id) VALUES (2);"
        # Pre-S9 schema: no thread_id, no threads table.
        "CREATE TABLE conversation_turns ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  profile_id INTEGER NOT NULL,"
        "  ts DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  kind TEXT NOT NULL,"
        "  content_json TEXT NOT NULL DEFAULT '{}'"
        ");"
        "INSERT INTO conversation_turns (profile_id, kind, content_json) "
        "VALUES (1, 'user_text',    '{\"text\":\"old p1\"}');"
        "INSERT INTO conversation_turns (profile_id, kind, content_json) "
        "VALUES (1, 'felix_speech', '{\"text\":\"old p1 reply\"}');"
        "INSERT INTO conversation_turns (profile_id, kind, content_json) "
        "VALUES (2, 'user_text',    '{\"text\":\"old p2\"}');"
    )
    con.commit()
    con.close()

    store = ConversationStore(db_path=db)

    # Each profile gets exactly one legacy thread; turns survive verbatim.
    threads_p1 = store.list_threads(1)
    threads_p2 = store.list_threads(2)
    assert len(threads_p1) == 1
    assert len(threads_p2) == 1
    assert threads_p1[0].title == "Legacy conversation"
    assert threads_p2[0].title == "Legacy conversation"

    turns_p1 = store.list_recent(1)
    assert [t.content["text"] for t in turns_p1] == ["old p1", "old p1 reply"]
    assert all(t.thread_id == threads_p1[0].id for t in turns_p1)

    turns_p2 = store.list_recent(2)
    assert [t.content["text"] for t in turns_p2] == ["old p2"]
    assert all(t.thread_id == threads_p2[0].id for t in turns_p2)


def test_migration_is_idempotent(tmp_path):
    """Constructing the store twice over the same DB must not produce a
    second Legacy thread or re-tag turns."""
    db = tmp_path / "openmind.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        "PRAGMA foreign_keys=ON;"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        "INSERT INTO profiles (id) VALUES (1);"
        "CREATE TABLE conversation_turns ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  profile_id INTEGER NOT NULL,"
        "  ts DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  kind TEXT NOT NULL,"
        "  content_json TEXT NOT NULL DEFAULT '{}'"
        ");"
        "INSERT INTO conversation_turns (profile_id, kind, content_json) "
        "VALUES (1, 'user_text', '{\"text\":\"legacy\"}');"
    )
    con.commit()
    con.close()

    ConversationStore(db_path=db)  # first migration
    store = ConversationStore(db_path=db)  # second open -- must be a no-op
    assert len(store.list_threads(1)) == 1


def test_list_recent_for_thread_scopes_to_thread(store):
    a = store.create_thread(1, title="A")
    b = store.create_thread(1, title="B")
    store.append(1, KIND_USER_TEXT, {"text": "ax"}, thread_id=a.id)
    store.append(1, KIND_USER_TEXT, {"text": "ay"}, thread_id=a.id)
    store.append(1, KIND_USER_TEXT, {"text": "bx"}, thread_id=b.id)
    in_a = store.list_recent_for_thread(a.id)
    assert [t.content["text"] for t in in_a] == ["ax", "ay"]


# ── S11 / #294 -- conversation projects (folders) ───────────────────────────


def test_create_project_persists_metadata(store):
    project = store.create_project(1, name="Trips")
    assert project.id > 0
    assert project.profile_id == 1
    assert project.name == "Trips"
    assert project.created_at


def test_list_projects_isolates_by_profile(store):
    p1 = store.create_project(1, name="A")
    p2 = store.create_project(2, name="B")
    assert [p.id for p in store.list_projects(1)] == [p1.id]
    assert [p.id for p in store.list_projects(2)] == [p2.id]


def test_rename_project_updates_name(store):
    p = store.create_project(1, name="Old")
    assert store.rename_project(p.id, "New")
    assert store.get_project(p.id).name == "New"


def test_thread_defaults_to_unfiled(store):
    """A freshly-created thread carries project_id = NULL ("Unfiled")."""
    t = store.create_thread(1, title="solo")
    fetched = store.get_thread(t.id)
    assert fetched.project_id is None


def test_move_thread_assigns_project(store):
    project = store.create_project(1, name="Cooking")
    thread = store.create_thread(1, title="Pasta")
    assert store.move_thread_to_project(thread.id, project.id)
    assert store.get_thread(thread.id).project_id == project.id


def test_move_thread_to_none_unfiles_it(store):
    project = store.create_project(1, name="Cooking")
    thread = store.create_thread(1, title="Pasta")
    store.move_thread_to_project(thread.id, project.id)
    assert store.move_thread_to_project(thread.id, None)
    assert store.get_thread(thread.id).project_id is None


def test_delete_project_leaves_threads_unfiled(store):
    """Spec AC: deleting a project leaves its threads Unfiled, not deleted."""
    project = store.create_project(1, name="Trips")
    t = store.create_thread(1, title="Tokyo")
    store.move_thread_to_project(t.id, project.id)
    store.append(1, KIND_USER_TEXT, {"text": "hi"}, thread_id=t.id)
    assert store.delete_project(project.id)
    # The thread itself survives, but is now Unfiled.
    survivor = store.get_thread(t.id)
    assert survivor is not None
    assert survivor.project_id is None
    # Its turns survive too.
    turns = store.list_recent_for_thread(t.id)
    assert [tu.content["text"] for tu in turns] == ["hi"]
    # And the project row is gone.
    assert store.get_project(project.id) is None


def test_list_projects_with_counts_reports_thread_count(store):
    p = store.create_project(1, name="Trips")
    a = store.create_thread(1, title="A")
    b = store.create_thread(1, title="B")
    store.move_thread_to_project(a.id, p.id)
    store.move_thread_to_project(b.id, p.id)
    rows = store.list_projects_with_counts(1)
    assert len(rows) == 1
    assert rows[0]["thread_count"] == 2


def test_list_threads_with_counts_carries_project_id(store):
    p = store.create_project(1, name="P")
    a = store.create_thread(1, title="A")  # filed
    store.create_thread(1, title="B")  # unfiled
    store.move_thread_to_project(a.id, p.id)
    rows = store.list_threads_with_counts(1)
    by_id = {r["id"]: r for r in rows}
    assert by_id[a.id]["project_id"] == p.id
    other = next(r for r in rows if r["id"] != a.id)
    assert other["project_id"] is None


def test_search_threads_carries_project_id(store):
    p = store.create_project(1, name="P")
    a = store.create_thread(1, title="Trip planning")
    store.move_thread_to_project(a.id, p.id)
    hits = store.search_threads(1, "Trip")
    assert hits
    assert hits[0]["project_id"] == p.id
