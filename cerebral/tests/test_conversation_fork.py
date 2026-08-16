"""fork_thread -- snapshot a thread's turns into a new one (harness parity
H3-S2 / #734, ADR-0022 decision 3). Hermetic: real ConversationStore on a
tmp_path SQLite db, mirrors test_conversation.py's bare-profile seed."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.conversation import (
    KIND_FELIX_SPEECH,
    KIND_USER_TEXT,
    ConversationStore,
)


def _seed_db(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.executescript(
        "PRAGMA foreign_keys=ON;"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        "INSERT INTO profiles (id) VALUES (1);"
    )
    con.commit()
    con.close()


@pytest.fixture
def store(tmp_path) -> ConversationStore:
    db = tmp_path / "openmind.db"
    _seed_db(db)
    return ConversationStore(db_path=db)


def test_fork_thread_copies_turns_up_to_boundary_inclusive(store):
    thread = store.create_thread(1, title="Original")
    turns = [
        store.append(1, KIND_USER_TEXT, {"text": "one"}, thread_id=thread.id),
        store.append(1, KIND_FELIX_SPEECH, {"text": "two"}, thread_id=thread.id),
        store.append(1, KIND_USER_TEXT, {"text": "three"}, thread_id=thread.id),
        store.append(1, KIND_FELIX_SPEECH, {"text": "four"}, thread_id=thread.id),
        store.append(1, KIND_USER_TEXT, {"text": "five"}, thread_id=thread.id),
    ]

    forked = store.fork_thread(thread.id, turns[2].id)  # boundary = "three"

    assert forked.id != thread.id
    forked_turns = store.list_recent_for_thread(forked.id)
    assert [t.content["text"] for t in forked_turns] == ["one", "two", "three"]


def test_fork_thread_is_independent_afterward(store):
    thread = store.create_thread(1, title="Original")
    t1 = store.append(1, KIND_USER_TEXT, {"text": "shared"}, thread_id=thread.id)
    forked = store.fork_thread(thread.id, t1.id)

    store.append(1, KIND_USER_TEXT, {"text": "only in source"}, thread_id=thread.id)
    store.append(1, KIND_FELIX_SPEECH, {"text": "only in fork"}, thread_id=forked.id)

    source_texts = [t.content["text"] for t in store.list_recent_for_thread(thread.id)]
    forked_texts = [t.content["text"] for t in store.list_recent_for_thread(forked.id)]
    assert source_texts == ["shared", "only in source"]
    assert forked_texts == ["shared", "only in fork"]


def test_fork_unknown_thread_raises(store):
    with pytest.raises(ValueError):
        store.fork_thread(99999, 1)


def test_fork_with_boundary_before_any_turns_yields_empty_fork(store):
    thread = store.create_thread(1, title="Original")
    store.append(1, KIND_USER_TEXT, {"text": "hello"}, thread_id=thread.id)

    forked = store.fork_thread(thread.id, 0)  # boundary before every real id

    assert store.list_recent_for_thread(forked.id) == []


def test_fork_preserves_content_exactly(store):
    thread = store.create_thread(1, title="Original")
    rich = {"text": "hi", "attachments": [{"id": 1, "name": "a.png"}], "extra": {"n": 3}}
    turn = store.append(1, KIND_USER_TEXT, rich, thread_id=thread.id)

    forked = store.fork_thread(thread.id, turn.id)

    forked_turns = store.list_recent_for_thread(forked.id)
    assert len(forked_turns) == 1
    assert forked_turns[0].content == rich


def test_fork_default_title_mentions_source(store):
    thread = store.create_thread(1, title="My chat")
    turn = store.append(1, KIND_USER_TEXT, {"text": "hi"}, thread_id=thread.id)

    forked = store.fork_thread(thread.id, turn.id)
    assert "Fork of" in forked.title
    assert "My chat" in forked.title

    untitled = store.create_thread(1, title="")
    turn2 = store.append(1, KIND_USER_TEXT, {"text": "hi"}, thread_id=untitled.id)
    forked2 = store.fork_thread(untitled.id, turn2.id)
    assert "Fork of" in forked2.title  # falls back to a sensible default, doesn't crash
