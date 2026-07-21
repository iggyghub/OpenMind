"""
S8 #487 -- memory proposals: propose_memory tool + approve/dismiss IPC.

Three behaviours under test:
  1. propose_memory queues a KIND_MEMORY_PROPOSAL item (plugin unit test).
  2. approve_item on a memory_proposal writes the fact to ChromaDB.
  3. dismiss_item on a memory_proposal writes nothing.
"""
import chromadb
import pytest

from cerebral.action_queue.manager import KIND_MEMORY_PROPOSAL, QueueManager
from cerebral.memory.manager import MemoryManager
from plugins.memory import MemoryPlugin


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def queue():
    return QueueManager(":memory:")


@pytest.fixture
def mem_mgr(tmp_path):
    chroma = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)


@pytest.fixture
def plugin(queue):
    return MemoryPlugin(queue_factory=lambda: queue)


@pytest.fixture
def ipc_rig(tmp_path, queue):
    """Patch main.py so approve/dismiss_item IPC handlers use in-memory fixtures."""
    import cerebral.main as main_mod
    import chromadb as _chromadb

    chroma = _chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    mgr = MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)

    saved = {
        "_queue": main_mod._queue,
        "_get_memory": main_mod._get_memory,
        "_broadcast": main_mod._broadcast,
    }

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._queue = queue
    main_mod._get_memory = lambda: mgr
    main_mod._broadcast = fake_broadcast

    class Rig:
        def __init__(self):
            self.queue = queue
            self.mgr = mgr
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def memory_updates(self):
            return [e for e in self.sent if e["type"] == "memory_update"]

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── 1. propose_memory queues a memory_proposal ────────────────────────────────

async def test_propose_memory_enqueues_proposal(plugin, queue):
    result = plugin._propose({"fact": "I prefer dark mode"})
    assert not result.is_error
    pending = queue.get_pending()
    assert len(pending) == 1
    item = pending[0]
    assert item.kind == KIND_MEMORY_PROPOSAL
    assert item.tool_args == {"fact": "I prefer dark mode"}
    assert "Remember" in item.title


async def test_propose_memory_blank_fact_is_error(plugin):
    result = plugin._propose({"fact": "   "})
    assert result.is_error


async def test_propose_memory_no_queue_factory_is_error():
    p = MemoryPlugin()  # no factory wired
    result = p._propose({"fact": "something"})
    assert result.is_error


async def test_propose_memory_strips_whitespace(plugin, queue):
    plugin._propose({"fact": "  My cat is named Luna.  "})
    item = queue.get_pending()[0]
    assert item.tool_args == {"fact": "My cat is named Luna."}


# ── 2. approve writes to ChromaDB ─────────────────────────────────────────────

async def test_approve_memory_proposal_writes_fact(ipc_rig):
    item = ipc_rig.queue.add_item(
        "Remember: I drink tea in the morning",
        "I drink tea in the morning",
        kind=KIND_MEMORY_PROPOSAL,
        tool_args={"fact": "I drink tea in the morning"},
    )
    await ipc_rig.handle({"type": "approve_item", "data": {"item_id": item.id}})
    stored = ipc_rig.mgr.list_all()
    assert len(stored) == 1
    assert stored[0].fact == "I drink tea in the morning"


async def test_approve_memory_proposal_broadcasts_memory_update(ipc_rig):
    item = ipc_rig.queue.add_item(
        "Remember: I am vegetarian",
        "I am vegetarian",
        kind=KIND_MEMORY_PROPOSAL,
        tool_args={"fact": "I am vegetarian"},
    )
    await ipc_rig.handle({"type": "approve_item", "data": {"item_id": item.id}})
    assert ipc_rig.memory_updates(), "expected memory_update broadcast"


# ── 3. dismiss writes nothing ─────────────────────────────────────────────────

async def test_dismiss_memory_proposal_writes_nothing(ipc_rig):
    item = ipc_rig.queue.add_item(
        "Remember: I play guitar",
        "I play guitar",
        kind=KIND_MEMORY_PROPOSAL,
        tool_args={"fact": "I play guitar"},
    )
    await ipc_rig.handle({"type": "dismiss_item", "data": {"item_id": item.id}})
    assert ipc_rig.mgr.list_all() == []
    assert ipc_rig.memory_updates() == []
