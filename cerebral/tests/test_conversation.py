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

    class Rig:
        def __init__(self) -> None:
            self.store = store
            self.sent  = sent

        async def handle(self, msg: dict) -> None:
            await main_mod._handle_message(msg)

        def turns_events(self) -> list[dict]:
            return [e for e in sent if e["type"] == "conversation_turns_data"]

    try:
        yield Rig()
    finally:
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
