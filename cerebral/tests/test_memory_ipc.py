"""
Memory tray IPC tests — Issue #82.

Exercises the WebSocket dispatcher branches that back the Memory tray
window: ``list_memories`` / ``edit_memory`` / ``delete_memory`` and the
``memory_update`` snapshot they broadcast. The dispatcher reaches memory
only through these IPC messages — never via an MCP tool — so this is the
ADR-0005 sanctioned listing surface.

Patches ``cerebral.main._get_memory`` to a tmp_path-backed manager
(PersistentClient, not EphemeralClient — chromadb 1.5.x shares a
module-level store across instances in the same process).
"""
import chromadb
import pytest

from cerebral.memory.manager import MemoryManager


@pytest.fixture
def memory_rig(tmp_path):
    import cerebral.main as main_mod

    chroma = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    mgr = MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)

    saved = {
        "_get_memory": main_mod._get_memory,
        "_broadcast": main_mod._broadcast,
    }

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._broadcast = fake_broadcast
    main_mod._get_memory = lambda: mgr

    class Rig:
        def __init__(self):
            self.module = main_mod
            self.mgr = mgr
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def no_profile(self):
            main_mod._get_memory = lambda: None

        def memory_updates(self):
            return [e for e in self.sent if e["type"] == "memory_update"]

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── list_memories ─────────────────────────────────────────────────────────────

async def test_list_memories_broadcasts_memory_update(memory_rig):
    await memory_rig.mgr.remember("I take my coffee black")
    await memory_rig.handle({"type": "list_memories"})
    updates = memory_rig.memory_updates()
    assert len(updates) == 1
    mems = updates[0]["data"]["memories"]
    assert [m["fact"] for m in mems] == ["I take my coffee black"]


async def test_memory_update_payload_shape(memory_rig):
    await memory_rig.mgr.remember("My birthday is in June")
    await memory_rig.handle({"type": "list_memories"})
    mem = memory_rig.memory_updates()[0]["data"]["memories"][0]
    assert set(mem.keys()) == {"id", "fact", "created_at"}
    assert mem["fact"] == "My birthday is in June"
    assert mem["created_at"] != ""


async def test_list_memories_empty_when_no_profile(memory_rig):
    memory_rig.no_profile()
    await memory_rig.handle({"type": "list_memories"})
    updates = memory_rig.memory_updates()
    assert len(updates) == 1
    assert updates[0]["data"]["memories"] == []


async def test_list_memories_newest_first(memory_rig):
    memory_rig.mgr._collection.add(
        documents=["old", "new"],
        ids=["x", "y"],
        metadatas=[
            {"profile_id": 1, "created_at": "2026-01-01T00:00:00+00:00"},
            {"profile_id": 1, "created_at": "2026-05-01T00:00:00+00:00"},
        ],
    )
    await memory_rig.handle({"type": "list_memories"})
    mems = memory_rig.memory_updates()[0]["data"]["memories"]
    assert [m["fact"] for m in mems] == ["new", "old"]


# ── edit_memory ───────────────────────────────────────────────────────────────

async def test_edit_memory_updates_and_rebroadcasts(memory_rig):
    mid = await memory_rig.mgr.remember("I work from the office")
    await memory_rig.handle(
        {"type": "edit_memory", "data": {"memory_id": mid, "fact": "I work from home"}}
    )
    updates = memory_rig.memory_updates()
    assert len(updates) == 1
    assert [m["fact"] for m in updates[0]["data"]["memories"]] == ["I work from home"]


async def test_edit_memory_unknown_id_no_broadcast(memory_rig):
    await memory_rig.mgr.remember("untouched")
    await memory_rig.handle(
        {"type": "edit_memory", "data": {"memory_id": "ghost", "fact": "x"}}
    )
    assert memory_rig.memory_updates() == []


async def test_edit_memory_blank_fact_no_broadcast(memory_rig):
    mid = await memory_rig.mgr.remember("keep me intact")
    await memory_rig.handle(
        {"type": "edit_memory", "data": {"memory_id": mid, "fact": "   "}}
    )
    assert memory_rig.memory_updates() == []
    assert memory_rig.mgr.list_all()[0].fact == "keep me intact"


async def test_edit_memory_no_profile_no_broadcast(memory_rig):
    memory_rig.no_profile()
    await memory_rig.handle(
        {"type": "edit_memory", "data": {"memory_id": "any", "fact": "y"}}
    )
    assert memory_rig.memory_updates() == []


# ── delete_memory ─────────────────────────────────────────────────────────────

async def test_delete_memory_removes_and_rebroadcasts(memory_rig):
    mid = await memory_rig.mgr.remember("delete this fact")
    await memory_rig.handle({"type": "delete_memory", "data": {"memory_id": mid}})
    updates = memory_rig.memory_updates()
    assert len(updates) == 1
    assert updates[0]["data"]["memories"] == []
    assert memory_rig.mgr.list_all() == []


async def test_delete_memory_unknown_id_no_broadcast(memory_rig):
    await memory_rig.mgr.remember("survivor")
    await memory_rig.handle({"type": "delete_memory", "data": {"memory_id": "ghost"}})
    assert memory_rig.memory_updates() == []
    assert len(memory_rig.mgr.list_all()) == 1


async def test_delete_memory_no_profile_no_broadcast(memory_rig):
    memory_rig.no_profile()
    await memory_rig.handle({"type": "delete_memory", "data": {"memory_id": "any"}})
    assert memory_rig.memory_updates() == []
