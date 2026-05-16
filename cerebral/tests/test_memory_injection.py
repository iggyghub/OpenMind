"""
Memory auto-injection tests — Issue #85.

Exercises ``_memory_preamble`` and its two call sites:
``_process_command`` (wake) and ``_bridge_process`` (channels). Neither
function had any prior test coverage, so this file is the first pin of
both the augmented and the byte-identical un-augmented paths.

ADR-0005 threat #1: injected facts are attacker-influenceable, so the
block is delimited and explicitly framed as non-instructions. The core
loop must never crash on a memory fault — recall failures degrade to the
un-augmented prompt.

Patches ``cerebral.main._get_memory`` to a tmp_path-backed manager
(PersistentClient, not EphemeralClient — chromadb 1.5.x shares a
module-level store across instances in the same process) and fakes
``cerebral.main._router`` with a prompt-capturing double. ``_broadcast``
is irrelevant to this slice (no IPC surface) — only ``_process_command``
needs ``_speak`` / ``_broadcast`` stubbed to run end-to-end.
"""
import chromadb
import pytest

from cerebral.memory.manager import MemoryManager


class _FakeRouter:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def complete(self, prompt, task_type="chat"):
        self.calls.append((prompt, task_type))
        return "OK"

    @property
    def last_prompt(self) -> str:
        return self.calls[-1][0]


class _BoomMemory:
    """Stands in for a MemoryManager whose vector store is unreadable."""

    async def recall(self, query, n_results=5):
        raise RuntimeError("chroma exploded")


@pytest.fixture
def inj_rig(tmp_path):
    import cerebral.main as main_mod

    chroma = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    mgr = MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)
    router = _FakeRouter()

    saved = {
        "_get_memory": main_mod._get_memory,
        "_router": main_mod._router,
        "_broadcast": main_mod._broadcast,
        "_speak": main_mod._speak,
    }

    async def _noop(*_a, **_k):
        return None

    main_mod._get_memory = lambda: mgr
    main_mod._router = router
    main_mod._broadcast = _noop
    main_mod._speak = _noop

    class Rig:
        def __init__(self):
            self.module = main_mod
            self.mgr = mgr
            self.router = router

        def no_profile(self):
            main_mod._get_memory = lambda: None

        def failing_memory(self):
            main_mod._get_memory = lambda: _BoomMemory()

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── _memory_preamble ──────────────────────────────────────────────────────────

async def test_preamble_empty_when_no_profile(inj_rig):
    inj_rig.no_profile()
    assert await inj_rig.module._memory_preamble("anything") == ""


async def test_preamble_empty_when_no_memories(inj_rig):
    assert await inj_rig.module._memory_preamble("anything") == ""


async def test_preamble_empty_when_recall_raises(inj_rig):
    inj_rig.failing_memory()
    # Must not propagate — core loop degrades to the un-augmented prompt.
    assert await inj_rig.module._memory_preamble("anything") == ""


async def test_preamble_contains_block_caveat_and_facts(inj_rig):
    await inj_rig.mgr.remember("The user's sister is named Alice")
    out = await inj_rig.module._memory_preamble("tell me about my sister")
    assert "NOT instructions" in out
    assert "<memory>" in out and "</memory>" in out
    assert "- The user's sister is named Alice" in out
    assert out.endswith("</memory>\n\n")
    assert out.startswith(inj_rig.module._MEMORY_PREAMBLE_HEADER)


async def test_preamble_is_facts_only_no_metadata(inj_rig):
    mem_id = await inj_rig.mgr.remember("The user prefers tea over coffee")
    out = await inj_rig.module._memory_preamble("what drink do I like")
    assert mem_id not in out
    # created_at is an ISO timestamp; no 'T'-dated iso fragment leaks in.
    all_mem = inj_rig.mgr.list_all()
    assert all_mem[0].created_at and all_mem[0].created_at not in out


async def test_preamble_caps_at_three_facts(inj_rig):
    for i in range(5):
        await inj_rig.mgr.remember(f"The user owns pet number {i} named Rex{i}")
    out = await inj_rig.module._memory_preamble("tell me about the user's pets")
    body = out.split("<memory>\n", 1)[1].split("\n</memory>", 1)[0]
    assert len([ln for ln in body.splitlines() if ln.startswith("- ")]) == 3


async def test_preamble_preserves_recall_relevance_order(inj_rig):
    await inj_rig.mgr.remember("The user's car is a blue Honda")
    await inj_rig.mgr.remember("The user's dog is a brown Labrador")
    await inj_rig.mgr.remember("The user's favourite colour is green")
    query = "what kind of dog does the user have"
    expected = [m.fact for m in await inj_rig.mgr.recall(query, n_results=3)]
    out = await inj_rig.module._memory_preamble(query)
    injected = [
        ln[2:] for ln in out.splitlines() if ln.startswith("- ")
    ]
    assert injected == expected


# ── _process_command (wake) ───────────────────────────────────────────────────

async def test_process_command_no_profile_prompt_byte_identical(inj_rig):
    inj_rig.no_profile()
    await inj_rig.module._process_command("felix what time is it")
    assert inj_rig.router.last_prompt == "felix what time is it"


async def test_process_command_empty_recall_byte_identical(inj_rig):
    await inj_rig.module._process_command("felix what time is it")
    assert inj_rig.router.last_prompt == "felix what time is it"


async def test_process_command_injects_block_then_transcript(inj_rig):
    await inj_rig.mgr.remember("The user's name is Sam")
    await inj_rig.module._process_command("felix who am i")
    prompt = inj_rig.router.last_prompt
    assert prompt.startswith(inj_rig.module._MEMORY_PREAMBLE_HEADER)
    assert "- The user's name is Sam" in prompt
    assert prompt.endswith("felix who am i")
    assert prompt.index("</memory>") < prompt.index("felix who am i")


async def test_process_command_recall_raises_degrades(inj_rig):
    inj_rig.failing_memory()
    await inj_rig.module._process_command("felix hello")
    assert inj_rig.router.last_prompt == "felix hello"
    assert len(inj_rig.router.calls) == 1


# ── _bridge_process (channels) ────────────────────────────────────────────────

async def test_bridge_no_history_no_profile_byte_identical(inj_rig):
    inj_rig.no_profile()
    result = await inj_rig.module._bridge_process("hi there", [])
    assert inj_rig.router.last_prompt == "hi there"
    assert result == "OK"


async def test_bridge_history_empty_recall_byte_identical(inj_rig):
    history = [{"role": "user", "text": "earlier message"}]
    await inj_rig.module._bridge_process("follow up", history)
    prompt = inj_rig.router.last_prompt
    assert prompt == (
        "Conversation so far:\nUser: earlier message\n\n"
        "User: follow up\nFelix:"
    )


async def test_bridge_injects_block_above_history(inj_rig):
    await inj_rig.mgr.remember("The user lives in Berlin")
    history = [{"role": "user", "text": "where am i based"}]
    result = await inj_rig.module._bridge_process("remind me", history)
    prompt = inj_rig.router.last_prompt
    assert prompt.startswith(inj_rig.module._MEMORY_PREAMBLE_HEADER)
    assert prompt.index("</memory>") < prompt.index("Conversation so far:")
    assert "- The user lives in Berlin" in prompt
    assert result == "OK"


async def test_bridge_recall_raises_degrades(inj_rig):
    inj_rig.failing_memory()
    result = await inj_rig.module._bridge_process("hi there", [])
    assert inj_rig.router.last_prompt == "hi there"
    assert result == "OK"
