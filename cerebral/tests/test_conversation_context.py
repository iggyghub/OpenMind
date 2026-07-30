"""_conversation_context -- Felix references its own chat window.

The main voice/text planner path was stateless: the planner only ever saw the
current transcript, so Felix couldn't recall the ongoing conversation (only the
bridge/channel path folded history). _conversation_context feeds the recent
thread turns into the prompt. These checks pin the non-trivial bits: filtering
to conversational turns, dropping the just-recorded current user turn, the
last-N window, and empty/no-thread fallbacks.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cerebral.main as main_mod
from cerebral.db.conversation import (
    KIND_FELIX_SPEECH,
    KIND_SYSTEM_EVENT,
    KIND_TOOL_CALL,
    KIND_USER_TEXT,
    ConversationStore,
)

PROFILE_ID = 1


def _rig(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path / "convo.db")
    monkeypatch.setattr(main_mod, "_conversation", store)
    monkeypatch.setattr(main_mod, "_active_thread_by_profile", {}, raising=False)
    thread = store.create_thread(PROFILE_ID, title="t")
    monkeypatch.setattr(
        main_mod, "_active_thread_by_profile", {PROFILE_ID: thread.id}, raising=False
    )
    return store, thread


def test_folds_history_drops_current_turn_and_skips_tool_noise(tmp_path, monkeypatch):
    store, thread = _rig(tmp_path, monkeypatch)
    store.append(PROFILE_ID, KIND_USER_TEXT, {"text": "my name is Sam"}, thread_id=thread.id)
    store.append(PROFILE_ID, KIND_FELIX_SPEECH, {"text": "Nice to meet you, Sam"}, thread_id=thread.id)
    store.append(PROFILE_ID, KIND_TOOL_CALL, {"name": "memory_remember"}, thread_id=thread.id)
    store.append(PROFILE_ID, KIND_SYSTEM_EVENT, {"event": "x"}, thread_id=thread.id)
    # The current turn -- already recorded before _process_command runs.
    store.append(PROFILE_ID, KIND_USER_TEXT, {"text": "what is my name?"}, thread_id=thread.id)

    ctx = main_mod._conversation_context(PROFILE_ID)

    assert ctx.startswith("Conversation so far:\n")
    assert "User: my name is Sam" in ctx
    assert "Felix: Nice to meet you, Sam" in ctx
    assert "memory_remember" not in ctx        # tool turns skipped
    assert "what is my name?" not in ctx        # current turn dropped
    assert ctx.endswith("\n\n")


def test_windows_to_last_n_turns(tmp_path, monkeypatch):
    store, thread = _rig(tmp_path, monkeypatch)
    for i in range(20):
        store.append(PROFILE_ID, KIND_USER_TEXT, {"text": f"msg {i}"}, thread_id=thread.id)
    store.append(PROFILE_ID, KIND_USER_TEXT, {"text": "current"}, thread_id=thread.id)

    ctx = main_mod._conversation_context(PROFILE_ID, max_turns=3)

    assert ctx.count("User:") == 3
    assert "msg 19" in ctx and "msg 17" in ctx
    assert "msg 16" not in ctx


def test_empty_when_only_current_turn(tmp_path, monkeypatch):
    store, thread = _rig(tmp_path, monkeypatch)
    store.append(PROFILE_ID, KIND_USER_TEXT, {"text": "first ever message"}, thread_id=thread.id)
    assert main_mod._conversation_context(PROFILE_ID) == ""


def test_empty_when_no_thread(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path / "convo.db")
    monkeypatch.setattr(main_mod, "_conversation", store)
    monkeypatch.setattr(main_mod, "_active_thread_by_profile", {}, raising=False)
    assert main_mod._conversation_context(999) == ""


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
