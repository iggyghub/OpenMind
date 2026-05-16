"""
Memory MCP plugin tests — Issue #79.

Covers MemoryPlugin:
  - memory_remember(fact)
  - memory_recall(query, n_results?)
  - memory_forget(memory_id)

The factory contract is exercised hermetically with a fake MemoryManager.
The TestIntegration cycle runs a real MemoryManager against a PersistentClient
in tmp_path (mirroring test_memory.py — chromadb 1.5.x's EphemeralClient
shares a module-level store across instances in the same process and would
leak between tests).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# plugins/ lives at the repo root, alongside cerebral/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cerebral.memory.manager import Memory, MemoryManager  # noqa: E402

import plugins.memory as memory_module  # noqa: E402
from plugins.memory import (  # noqa: E402
    MemoryPlugin,
    PLUGIN_NAME,
    REQUIRED_CAPABILITIES,
    create,
    set_memory_factory,
)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeMemoryManager:
    """In-process MemoryManager double — no Chroma, no SQLite."""

    def __init__(self, profile_id: int = 1) -> None:
        self._profile_id = profile_id
        self._store: dict[str, str] = {}
        self._counter = 0
        self.remember_calls: list[str] = []
        self.recall_calls: list[tuple[str, int]] = []
        self.forget_calls: list[str] = []
        self.raise_on: str | None = None  # tool name to make explode

    async def remember(self, fact: str) -> str:
        self.remember_calls.append(fact)
        if self.raise_on == "remember":
            raise RuntimeError("simulated remember failure")
        self._counter += 1
        mid = f"id-{self._counter}"
        self._store[mid] = fact
        return mid

    async def recall(self, query: str, n_results: int = 5) -> list[Memory]:
        self.recall_calls.append((query, n_results))
        if self.raise_on == "recall":
            raise RuntimeError("simulated recall failure")
        items = list(self._store.items())[:n_results]
        return [
            Memory(
                id=mid,
                fact=fact,
                profile_id=self._profile_id,
                created_at="t",
                distance=0.1 * (i + 1),
            )
            for i, (mid, fact) in enumerate(items)
        ]

    async def forget(self, memory_id: str) -> bool:
        self.forget_calls.append(memory_id)
        if self.raise_on == "forget":
            raise RuntimeError("simulated forget failure")
        if memory_id in self._store:
            del self._store[memory_id]
            return True
        return False


@pytest.fixture(autouse=True)
def _reset_module_factory():
    """Ensure each test starts with the module-level factory unset."""
    original = memory_module._memory_factory
    memory_module._memory_factory = None
    yield
    memory_module._memory_factory = original


@pytest.fixture
def fake_mgr() -> _FakeMemoryManager:
    return _FakeMemoryManager()


@pytest.fixture
def plugin(fake_mgr) -> MemoryPlugin:
    return MemoryPlugin(memory_factory=lambda: fake_mgr)


# ---------------------------------------------------------------------------
# TestRemember — 4 tests
# ---------------------------------------------------------------------------


class TestRemember:
    async def test_happy_path_returns_id(self, plugin, fake_mgr):
        result = await plugin.call_tool("memory_remember", {"fact": "Alice is my sister"})
        assert result.is_error is False
        payload = json.loads(result.content)
        assert payload["memory_id"] == "id-1"
        assert fake_mgr.remember_calls == ["Alice is my sister"]

    async def test_blank_fact_rejected(self, plugin, fake_mgr):
        result = await plugin.call_tool("memory_remember", {"fact": "   \t\n"})
        assert result.is_error is True
        assert result.content == "'fact' is required for memory_remember"
        assert fake_mgr.remember_calls == []

    async def test_missing_fact_rejected(self, plugin, fake_mgr):
        result = await plugin.call_tool("memory_remember", {})
        assert result.is_error is True
        assert result.content == "'fact' is required for memory_remember"
        assert fake_mgr.remember_calls == []

    async def test_long_fact_stored_verbatim(self, plugin, fake_mgr):
        long_fact = "x" * 2000
        result = await plugin.call_tool("memory_remember", {"fact": long_fact})
        assert result.is_error is False
        assert fake_mgr.remember_calls == [long_fact]


# ---------------------------------------------------------------------------
# TestRecall — 5 tests
# ---------------------------------------------------------------------------


class TestRecall:
    async def test_returns_facts_ordered_by_distance(self, plugin, fake_mgr):
        await plugin.call_tool("memory_remember", {"fact": "fact A"})
        await plugin.call_tool("memory_remember", {"fact": "fact B"})
        result = await plugin.call_tool("memory_recall", {"query": "fact"})
        assert result.is_error is False
        payload = json.loads(result.content)
        memories = payload["memories"]
        assert len(memories) == 2
        # Distances are monotonically increasing (fake's contract); plugin
        # passes through whatever order MemoryManager returns.
        assert memories[0]["distance"] < memories[1]["distance"]
        assert memories[0]["fact"] == "fact A"
        assert memories[0]["id"] == "id-1"

    async def test_blank_query_rejected(self, plugin, fake_mgr):
        result = await plugin.call_tool("memory_recall", {"query": ""})
        assert result.is_error is True
        assert result.content == "'query' is required for memory_recall"
        assert fake_mgr.recall_calls == []

    async def test_n_results_honoured(self, plugin, fake_mgr):
        for i in range(5):
            await plugin.call_tool("memory_remember", {"fact": f"fact {i}"})
        await plugin.call_tool("memory_recall", {"query": "fact", "n_results": 3})
        assert fake_mgr.recall_calls[-1] == ("fact", 3)

    async def test_n_results_clamped(self, plugin, fake_mgr):
        # > cap → 20
        await plugin.call_tool("memory_recall", {"query": "x", "n_results": 999})
        assert fake_mgr.recall_calls[-1][1] == 20
        # < 1 → default 5
        await plugin.call_tool("memory_recall", {"query": "x", "n_results": 0})
        assert fake_mgr.recall_calls[-1][1] == 5
        # negative → default
        await plugin.call_tool("memory_recall", {"query": "x", "n_results": -3})
        assert fake_mgr.recall_calls[-1][1] == 5
        # non-int → default
        await plugin.call_tool("memory_recall", {"query": "x", "n_results": "twelve"})
        assert fake_mgr.recall_calls[-1][1] == 5
        # missing → default (no n_results arg)
        await plugin.call_tool("memory_recall", {"query": "x"})
        assert fake_mgr.recall_calls[-1][1] == 5

    async def test_empty_memory_returns_empty_list(self, plugin):
        result = await plugin.call_tool("memory_recall", {"query": "anything"})
        assert result.is_error is False
        payload = json.loads(result.content)
        assert payload == {"memories": []}


# ---------------------------------------------------------------------------
# TestForget — 4 tests
# ---------------------------------------------------------------------------


class TestForget:
    async def test_happy_path_deletes(self, plugin, fake_mgr):
        remember = await plugin.call_tool("memory_remember", {"fact": "a fact"})
        memory_id = json.loads(remember.content)["memory_id"]

        result = await plugin.call_tool("memory_forget", {"memory_id": memory_id})
        assert result.is_error is False
        assert json.loads(result.content) == {"forgotten": True}
        assert fake_mgr.forget_calls == [memory_id]
        assert memory_id not in fake_mgr._store

    async def test_unknown_id_returns_not_found(self, plugin, fake_mgr):
        result = await plugin.call_tool("memory_forget", {"memory_id": "id-does-not-exist"})
        assert result.is_error is True
        assert result.content == "Memory not found: 'id-does-not-exist'"

    async def test_missing_id_rejected(self, plugin, fake_mgr):
        result = await plugin.call_tool("memory_forget", {})
        assert result.is_error is True
        assert result.content == "'memory_id' is required for memory_forget"
        assert fake_mgr.forget_calls == []

    async def test_blank_id_rejected(self, plugin, fake_mgr):
        result = await plugin.call_tool("memory_forget", {"memory_id": "   "})
        assert result.is_error is True
        assert result.content == "'memory_id' is required for memory_forget"
        assert fake_mgr.forget_calls == []


# ---------------------------------------------------------------------------
# TestNoActiveProfile — 3 tests, parametrized over the 3 tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name, args",
    [
        ("memory_remember", {"fact": "anything"}),
        ("memory_recall", {"query": "anything"}),
        ("memory_forget", {"memory_id": "anything"}),
    ],
)
async def test_no_active_profile_returns_canonical_error(tool_name, args):
    plugin = MemoryPlugin(memory_factory=lambda: None)
    result = await plugin.call_tool(tool_name, args)
    assert result.is_error is True
    assert result.content == "Memory is not available — no active profile"


# ---------------------------------------------------------------------------
# TestNoFactoryWired — 3 tests, parametrized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name, args",
    [
        ("memory_remember", {"fact": "anything"}),
        ("memory_recall", {"query": "anything"}),
        ("memory_forget", {"memory_id": "anything"}),
    ],
)
async def test_no_factory_wired_returns_canonical_error(tool_name, args):
    # Neither constructor nor module factory set (autouse fixture clears
    # module-level _memory_factory before each test).
    plugin = MemoryPlugin()
    result = await plugin.call_tool(tool_name, args)
    assert result.is_error is True
    assert result.content == "Memory is not available — factory not wired"


# ---------------------------------------------------------------------------
# TestListTools — 3 tests
# ---------------------------------------------------------------------------


class TestListTools:
    def test_three_tools_registered(self, plugin):
        names = [t.name for t in plugin.list_tools()]
        assert names == ["memory_remember", "memory_recall", "memory_forget"]

    def test_tools_belong_to_plugin(self, plugin):
        for tool in plugin.list_tools():
            assert tool.plugin == PLUGIN_NAME

    def test_schemas_have_required_fields(self, plugin):
        by_name = {t.name: t for t in plugin.list_tools()}
        assert by_name["memory_remember"].schema["required"] == ["fact"]
        assert by_name["memory_recall"].schema["required"] == ["query"]
        assert "n_results" in by_name["memory_recall"].schema["properties"]
        assert by_name["memory_forget"].schema["required"] == ["memory_id"]


# ---------------------------------------------------------------------------
# TestModuleSurface — 3 tests
# ---------------------------------------------------------------------------


class TestModuleSurface:
    def test_plugin_name(self):
        assert PLUGIN_NAME == "memory"

    def test_required_capabilities_is_empty_frozenset(self):
        # First plugin in the codebase to declare empty — see module docstring.
        assert REQUIRED_CAPABILITIES == frozenset()
        assert isinstance(REQUIRED_CAPABILITIES, frozenset)

    def test_create_returns_memory_plugin(self):
        plug = create()
        assert isinstance(plug, MemoryPlugin)
        assert plug.name == "memory"


# ---------------------------------------------------------------------------
# TestConstruction — registration-time side effects = none
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_construct_does_not_resolve_factory(self):
        calls = []

        def factory():
            calls.append("called")
            return None

        # Construction must not call the factory — the factory is for tool
        # calls, not for construction. Pin so a future implementor doesn't
        # accidentally add eager resolution.
        MemoryPlugin(memory_factory=factory)
        assert calls == []


# ---------------------------------------------------------------------------
# TestSetMemoryFactory — module-level setter contract
# ---------------------------------------------------------------------------


class TestSetMemoryFactory:
    async def test_module_factory_used_when_constructor_factory_absent(self, fake_mgr):
        set_memory_factory(lambda: fake_mgr)
        plug = MemoryPlugin()  # no constructor factory
        result = await plug.call_tool("memory_remember", {"fact": "via module factory"})
        assert result.is_error is False
        assert fake_mgr.remember_calls == ["via module factory"]

    async def test_constructor_factory_beats_module_factory(self, fake_mgr):
        ignored = _FakeMemoryManager()
        set_memory_factory(lambda: ignored)
        plug = MemoryPlugin(memory_factory=lambda: fake_mgr)
        await plug.call_tool("memory_remember", {"fact": "via ctor"})
        assert fake_mgr.remember_calls == ["via ctor"]
        assert ignored.remember_calls == []


# ---------------------------------------------------------------------------
# TestExceptionMapping — unexpected MemoryManager failures collapse to a
# generic ToolResult so the plugin never raises into the orchestrator.
# ---------------------------------------------------------------------------


class TestExceptionMapping:
    async def test_remember_exception_maps_to_generic_error(self, plugin, fake_mgr):
        fake_mgr.raise_on = "remember"
        result = await plugin.call_tool("memory_remember", {"fact": "boom"})
        assert result.is_error is True
        assert result.content == "Memory operation failed"

    async def test_recall_exception_maps_to_generic_error(self, plugin, fake_mgr):
        fake_mgr.raise_on = "recall"
        result = await plugin.call_tool("memory_recall", {"query": "boom"})
        assert result.is_error is True
        assert result.content == "Memory operation failed"

    async def test_forget_exception_maps_to_generic_error(self, plugin, fake_mgr):
        fake_mgr.raise_on = "forget"
        result = await plugin.call_tool("memory_forget", {"memory_id": "boom"})
        assert result.is_error is True
        assert result.content == "Memory operation failed"


# ---------------------------------------------------------------------------
# TestIntegration — real MemoryManager + PersistentClient in tmp_path
# (mirroring test_memory.py's pattern; EphemeralClient leaks state across
# instances in the same process per the comment at test_memory.py:5).
# ---------------------------------------------------------------------------


import chromadb  # noqa: E402


@pytest.fixture
def real_mgr(tmp_path) -> MemoryManager:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return MemoryManager(profile_id=1, db_path=":memory:", chroma_client=client)


@pytest.fixture
def integration_plugin(real_mgr) -> MemoryPlugin:
    return MemoryPlugin(memory_factory=lambda: real_mgr)


class TestIntegration:
    async def test_remember_recall_roundtrip(self, integration_plugin):
        result = await integration_plugin.call_tool(
            "memory_remember", {"fact": "My sister's name is Alice"}
        )
        assert result.is_error is False
        memory_id = json.loads(result.content)["memory_id"]
        assert memory_id

        recall = await integration_plugin.call_tool(
            "memory_recall", {"query": "sister name"}
        )
        assert recall.is_error is False
        payload = json.loads(recall.content)
        assert payload["memories"]
        facts = [m["fact"] for m in payload["memories"]]
        assert any("Alice" in f for f in facts)
        # Plugin passes through Chroma's natural order — distance ascending.
        distances = [m["distance"] for m in payload["memories"]]
        assert distances == sorted(distances)

    async def test_remember_forget_recall_cycle(self, integration_plugin):
        remember = await integration_plugin.call_tool(
            "memory_remember", {"fact": "I prefer dark roast coffee"}
        )
        memory_id = json.loads(remember.content)["memory_id"]

        forget = await integration_plugin.call_tool(
            "memory_forget", {"memory_id": memory_id}
        )
        assert forget.is_error is False
        assert json.loads(forget.content) == {"forgotten": True}

        # Second forget of the same id is a clean "not found" — verifies the
        # forget contract returns False through MemoryManager.forget.
        again = await integration_plugin.call_tool(
            "memory_forget", {"memory_id": memory_id}
        )
        assert again.is_error is True
        assert again.content == f"Memory not found: '{memory_id}'"
