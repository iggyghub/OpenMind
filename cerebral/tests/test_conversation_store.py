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
