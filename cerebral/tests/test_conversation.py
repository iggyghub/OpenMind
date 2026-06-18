"""
Conversation turn persistence + WS IPC tests — Issue #214 / ADR-0007.

Exercises the IPC layer in Cerebral:
  - list_conversation_turns broadcasts conversation_turns_data
  - user_text_command records KIND_USER_TEXT
  - call_tool records KIND_TOOL_CALL and KIND_TOOL_RESULT
  - switch_model records KIND_SYSTEM_EVENT
  - consent_response records KIND_SYSTEM_EVENT

No real WebSocket — patches _broadcast.
All tests are async (pytest asyncio_mode=auto).
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.conversation import (
    KIND_SYSTEM_EVENT,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    KIND_USER_TEXT,
    ConversationStore,
)
from cerebral.db.profiles import Profile


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
def conv_rig(tmp_path):
    """Patches _conversation, _active_profile, _broadcast, _connected in main.

    Also stubs _process_command so user_text_command tests don't spin up the
    full LLM + TTS pipeline.
    """
    import cerebral.main as main_mod

    db = tmp_path / "openmind.db"
    _seed_db(db)
    store = ConversationStore(db_path=db)

    profile = Profile(name="Test", id=1)
    sent: list[dict] = []

    async def fake_broadcast(event: dict) -> None:
        sent.append(event)

    async def fake_process_command(text: str, *, speak: bool = True) -> None:
        pass

    saved = {
        "_conversation":    main_mod._conversation,
        "_active_profile":  main_mod._active_profile,
        "_broadcast":       main_mod._broadcast,
        "_connected":       main_mod._connected,
        "_process_command": main_mod._process_command,
    }

    main_mod._conversation    = store
    main_mod._active_profile  = profile
    main_mod._broadcast       = fake_broadcast
    main_mod._connected       = set()
    main_mod._process_command = fake_process_command
    # S9 / #292 -- the active-thread cache is module-level RAM. Reset it so
    # tests don't see a stale active id from a previous test run.
    main_mod._active_thread_by_profile.clear()

    class Rig:
        def __init__(self) -> None:
            self.store = store
            self.sent  = sent

        async def handle(self, msg: dict) -> None:
            await main_mod._handle_message(msg)

        def turns_events(self) -> list[dict]:
            return [e for e in sent if e["type"] == "conversation_turns_data"]

        def threads_events(self) -> list[dict]:
            return [e for e in sent if e["type"] == "conversation_threads_data"]

        def projects_events(self) -> list[dict]:
            return [e for e in sent if e["type"] == "conversation_projects_data"]

    try:
        yield Rig()
    finally:
        main_mod._active_thread_by_profile.clear()
        for k, v in saved.items():
            setattr(main_mod, k, v)


# ── list_conversation_turns ──────────────────────────────────────────────────

async def test_list_conversation_turns_broadcasts_snapshot(conv_rig):
    await conv_rig.handle({"type": "list_conversation_turns"})
    evts = conv_rig.turns_events()
    assert len(evts) == 1
    assert evts[0]["data"]["profile_id"] == 1
    assert evts[0]["data"]["turns"] == []


async def test_list_conversation_turns_includes_existing_turns(conv_rig):
    conv_rig.store.append(1, KIND_USER_TEXT, {"text": "hi"})
    await conv_rig.handle({"type": "list_conversation_turns"})
    turns = conv_rig.turns_events()[-1]["data"]["turns"]
    assert len(turns) == 1
    assert turns[0]["kind"] == KIND_USER_TEXT


async def test_list_conversation_turns_respects_limit(conv_rig):
    for i in range(5):
        conv_rig.store.append(1, KIND_USER_TEXT, {"text": f"msg{i}"})
    await conv_rig.handle({"type": "list_conversation_turns", "data": {"limit": 2}})
    turns = conv_rig.turns_events()[-1]["data"]["turns"]
    assert len(turns) == 2


# ── user_text_command ────────────────────────────────────────────────────────

async def test_user_text_command_records_user_text_turn(conv_rig):
    await conv_rig.handle({"type": "user_text_command", "data": {"text": "hello"}})
    turns = conv_rig.store.list_recent(1)
    assert len(turns) == 1
    assert turns[0].kind == KIND_USER_TEXT
    assert turns[0].content["text"] == "hello"


async def test_user_text_command_empty_text_not_recorded(conv_rig):
    await conv_rig.handle({"type": "user_text_command", "data": {"text": "   "}})
    assert conv_rig.store.list_recent(1) == []


# ── call_tool ────────────────────────────────────────────────────────────────

async def test_call_tool_records_tool_call_and_result(conv_rig, monkeypatch):
    import cerebral.main as main_mod
    from cerebral.mcp.orchestrator import ToolResult

    async def fake_call_tool(name: str, args: dict) -> ToolResult:
        return ToolResult(content="ok", is_error=False)

    monkeypatch.setattr(main_mod._orc, "call_tool", fake_call_tool)

    await conv_rig.handle({
        "type": "call_tool",
        "data": {"name": "memory_search", "args": {"q": "test"}},
    })

    turns = conv_rig.store.list_recent(1)
    kinds = [t.kind for t in turns]
    assert KIND_TOOL_CALL in kinds
    assert KIND_TOOL_RESULT in kinds

    tc = next(t for t in turns if t.kind == KIND_TOOL_CALL)
    assert tc.content["name"] == "memory_search"

    tr = next(t for t in turns if t.kind == KIND_TOOL_RESULT)
    assert tr.content["name"] == "memory_search"
    assert tr.content["is_error"] is False


# ── switch_model ─────────────────────────────────────────────────────────────

async def test_switch_model_records_system_event(conv_rig, monkeypatch):
    import cerebral.main as main_mod

    monkeypatch.setattr(main_mod._router, "switch_model", lambda mid: None)
    monkeypatch.setattr(main_mod._pm, "update_active_model", lambda pid, mid: None)
    monkeypatch.setattr(main_mod._pm, "get", lambda pid: Profile(name="Test", id=1))

    async def fake_pulse() -> None:
        pass

    monkeypatch.setattr(main_mod, "_pulse_back_to_passive", fake_pulse)
    monkeypatch.setattr(main_mod, "_models_list_event",
                        lambda: {"type": "models_list", "data": {}})

    await conv_rig.handle({"type": "switch_model", "data": {"model_id": "ollama/gemma3"}})

    turns = conv_rig.store.list_recent(1)
    sys_turns = [t for t in turns if t.kind == KIND_SYSTEM_EVENT]
    assert len(sys_turns) == 1
    assert sys_turns[0].content["event"] == "model_switch"
    assert sys_turns[0].content["model_id"] == "ollama/gemma3"


# ── consent_response ─────────────────────────────────────────────────────────

async def test_consent_response_records_system_event(conv_rig):
    import cerebral.main as main_mod

    loop = asyncio.get_event_loop()
    fut: asyncio.Future[str] = loop.create_future()
    main_mod._pending_consents["req-99"] = fut

    try:
        await conv_rig.handle({
            "type": "consent_response",
            "data": {"request_id": "req-99", "choice": "once"},
        })
    finally:
        main_mod._pending_consents.pop("req-99", None)

    turns = conv_rig.store.list_recent(1)
    sys_turns = [t for t in turns if t.kind == KIND_SYSTEM_EVENT]
    assert len(sys_turns) == 1
    assert sys_turns[0].content["event"] == "consent_response"
    assert sys_turns[0].content["choice"] == "once"
    assert sys_turns[0].content["request_id"] == "req-99"


# ── S9 / #292 -- conversation threads IPC ────────────────────────────────────


async def test_list_conversation_threads_emits_snapshot(conv_rig):
    conv_rig.store.create_thread(1, title="A")
    conv_rig.store.create_thread(1, title="B")
    await conv_rig.handle({"type": "list_conversation_threads"})
    evts = conv_rig.threads_events()
    assert len(evts) == 1
    data = evts[-1]["data"]
    assert data["profile_id"] == 1
    titles = [t["title"] for t in data["threads"]]
    assert set(titles) == {"A", "B"}


async def test_new_conversation_thread_creates_and_activates(conv_rig):
    # Seed an existing thread so we can prove the new one supersedes it.
    existing = conv_rig.store.create_thread(1, title="prior")
    conv_rig.store.append(
        1, KIND_USER_TEXT, {"text": "hello"}, thread_id=existing.id,
    )

    await conv_rig.handle({"type": "new_conversation_thread"})

    threads = conv_rig.store.list_threads(1)
    # Two threads now: original + new untitled one.
    assert len(threads) == 2
    untitled = [t for t in threads if not t.title]
    assert len(untitled) == 1

    # The newest broadcast turns-snapshot is for the new (empty) thread.
    last_turns_event = conv_rig.turns_events()[-1]
    assert last_turns_event["data"]["thread_id"] == untitled[0].id
    assert last_turns_event["data"]["turns"] == []

    # The threads-list broadcast reports the new thread as active.
    last_threads_event = conv_rig.threads_events()[-1]
    assert last_threads_event["data"]["active_thread_id"] == untitled[0].id


async def test_new_conversation_thread_then_record_attaches_to_new(conv_rig):
    # Existing thread with a turn.
    a = conv_rig.store.create_thread(1, title="A")
    conv_rig.store.append(1, KIND_USER_TEXT, {"text": "in A"}, thread_id=a.id)

    # User clicks "New conversation".
    await conv_rig.handle({"type": "new_conversation_thread"})

    # A subsequent user_text_command must land in the new thread, not A.
    await conv_rig.handle(
        {"type": "user_text_command", "data": {"text": "fresh start"}},
    )

    # Active thread now has exactly one turn.
    new_threads = [t for t in conv_rig.store.list_threads(1) if t.id != a.id]
    assert len(new_threads) == 1
    in_new = conv_rig.store.list_recent_for_thread(new_threads[0].id)
    assert [t.content["text"] for t in in_new] == ["fresh start"]
    # And the old thread is untouched.
    in_a = conv_rig.store.list_recent_for_thread(a.id)
    assert [t.content["text"] for t in in_a] == ["in A"]


async def test_rename_conversation_thread_updates_title(conv_rig):
    t = conv_rig.store.create_thread(1, title="")
    await conv_rig.handle({
        "type": "rename_conversation_thread",
        "data": {"thread_id": t.id, "title": "Renamed by user"},
    })
    assert conv_rig.store.get_thread(t.id).title == "Renamed by user"
    # And a fresh threads broadcast went out so the UI label updates.
    assert conv_rig.threads_events(), "expected conversation_threads_data broadcast"


async def test_rename_conversation_thread_rejects_other_profile(conv_rig):
    import cerebral.main as main_mod

    # Create a thread for profile 2 (not the active profile).
    main_mod._active_profile = None
    # Borrow the store directly to create a foreign thread without auto-active.
    foreign = conv_rig.store.create_thread(2, title="P2 owned")
    main_mod._active_profile = Profile(name="Test", id=1)

    await conv_rig.handle({
        "type": "rename_conversation_thread",
        "data": {"thread_id": foreign.id, "title": "HIJACK"},
    })
    # Title must not have changed -- the handler scoped to active profile.
    assert conv_rig.store.get_thread(foreign.id).title == "P2 owned"


async def test_switch_conversation_thread_changes_active(conv_rig):
    a = conv_rig.store.create_thread(1, title="A")
    b = conv_rig.store.create_thread(1, title="B")
    conv_rig.store.append(1, KIND_USER_TEXT, {"text": "in A"}, thread_id=a.id)
    conv_rig.store.append(1, KIND_USER_TEXT, {"text": "in B"}, thread_id=b.id)

    await conv_rig.handle({
        "type": "switch_conversation_thread",
        "data": {"thread_id": a.id},
    })

    last_turns_event = conv_rig.turns_events()[-1]
    assert last_turns_event["data"]["thread_id"] == a.id
    texts = [t["content"]["text"] for t in last_turns_event["data"]["turns"]]
    assert texts == ["in A"]


# ── S11 / #294 -- conversation projects IPC ─────────────────────────────────


async def test_list_conversation_projects_emits_snapshot(conv_rig):
    conv_rig.store.create_project(1, name="P1")
    conv_rig.store.create_project(1, name="P2")
    await conv_rig.handle({"type": "list_conversation_projects"})
    evts = conv_rig.projects_events()
    assert len(evts) == 1
    data = evts[-1]["data"]
    assert data["profile_id"] == 1
    names = [p["name"] for p in data["projects"]]
    assert set(names) == {"P1", "P2"}


async def test_create_conversation_project_persists_and_broadcasts(conv_rig):
    await conv_rig.handle({
        "type": "create_conversation_project",
        "data": {"name": "Trips"},
    })
    projects = conv_rig.store.list_projects(1)
    assert [p.name for p in projects] == ["Trips"]
    assert conv_rig.projects_events(), "expected a projects broadcast"


async def test_rename_conversation_project_updates_name(conv_rig):
    p = conv_rig.store.create_project(1, name="Old")
    await conv_rig.handle({
        "type": "rename_conversation_project",
        "data": {"project_id": p.id, "name": "New"},
    })
    assert conv_rig.store.get_project(p.id).name == "New"


async def test_rename_conversation_project_rejects_other_profile(conv_rig):
    """A project created against a different profile cannot be renamed."""
    import cerebral.main as main_mod
    main_mod._active_profile = None
    foreign = conv_rig.store.create_project(2, name="P2-owned")
    main_mod._active_profile = Profile(name="Test", id=1)
    await conv_rig.handle({
        "type": "rename_conversation_project",
        "data": {"project_id": foreign.id, "name": "HIJACK"},
    })
    assert conv_rig.store.get_project(foreign.id).name == "P2-owned"


async def test_delete_conversation_project_unfiles_threads(conv_rig):
    """Spec AC: deleting a project leaves its threads Unfiled."""
    p = conv_rig.store.create_project(1, name="Trips")
    t = conv_rig.store.create_thread(1, title="Tokyo")
    conv_rig.store.move_thread_to_project(t.id, p.id)
    await conv_rig.handle({
        "type": "delete_conversation_project",
        "data": {"project_id": p.id},
    })
    assert conv_rig.store.get_project(p.id) is None
    # Thread survives and is now Unfiled.
    survivor = conv_rig.store.get_thread(t.id)
    assert survivor is not None
    assert survivor.project_id is None
    # Both projects + threads broadcasts went out.
    assert conv_rig.projects_events()
    assert conv_rig.threads_events()


async def test_move_conversation_thread_assigns_project(conv_rig):
    p = conv_rig.store.create_project(1, name="Cooking")
    t = conv_rig.store.create_thread(1, title="Pasta")
    await conv_rig.handle({
        "type": "move_conversation_thread",
        "data": {"thread_id": t.id, "project_id": p.id},
    })
    assert conv_rig.store.get_thread(t.id).project_id == p.id
    # Threads broadcast went out so the renderer can re-group.
    assert conv_rig.threads_events()


async def test_move_conversation_thread_to_none_unfiles(conv_rig):
    p = conv_rig.store.create_project(1, name="P")
    t = conv_rig.store.create_thread(1, title="X")
    conv_rig.store.move_thread_to_project(t.id, p.id)
    await conv_rig.handle({
        "type": "move_conversation_thread",
        "data": {"thread_id": t.id, "project_id": None},
    })
    assert conv_rig.store.get_thread(t.id).project_id is None


async def test_move_conversation_thread_rejects_foreign_thread(conv_rig):
    """A thread belonging to another profile can't be moved by the
    active profile, even with a valid project_id under the active profile."""
    import cerebral.main as main_mod
    main_mod._active_profile = None
    foreign = conv_rig.store.create_thread(2, title="P2 thread")
    main_mod._active_profile = Profile(name="Test", id=1)
    p = conv_rig.store.create_project(1, name="P1 project")
    await conv_rig.handle({
        "type": "move_conversation_thread",
        "data": {"thread_id": foreign.id, "project_id": p.id},
    })
    # Thread's project_id unchanged.
    assert conv_rig.store.get_thread(foreign.id).project_id is None


async def test_move_conversation_thread_rejects_foreign_project(conv_rig):
    import cerebral.main as main_mod
    main_mod._active_profile = None
    foreign_project = conv_rig.store.create_project(2, name="P2 project")
    main_mod._active_profile = Profile(name="Test", id=1)
    t = conv_rig.store.create_thread(1, title="P1 thread")
    await conv_rig.handle({
        "type": "move_conversation_thread",
        "data": {"thread_id": t.id, "project_id": foreign_project.id},
    })
    assert conv_rig.store.get_thread(t.id).project_id is None


async def test_auto_title_broadcast_after_felix_speech(conv_rig):
    # User turn first, then a felix_speech triggers the store's auto-title
    # pass; the IPC layer should re-broadcast conversation_threads_data so
    # the renderer's title strip refreshes without a separate fetch.
    await conv_rig.handle({
        "type": "user_text_command", "data": {"text": "What's the weather?"},
    })
    # Simulate the felix response landing via the record_turn path.
    import cerebral.main as main_mod
    from cerebral.db.conversation import KIND_FELIX_SPEECH
    await main_mod._record_turn(KIND_FELIX_SPEECH, {"text": "Sunny."})

    threads_events = conv_rig.threads_events()
    assert threads_events, "expected a threads-list broadcast after felix_speech"
    # Newest threads broadcast carries the auto-derived title.
    last = threads_events[-1]["data"]
    titles = [t["title"] for t in last["threads"]]
    assert any("What's the weather?".startswith(title) or title.startswith("What's the weather?")
               for title in titles)
