"""
Memory manager tests — Issue #12.

Uses PersistentClient with tmp_path for true per-test isolation — chromadb 1.5.x
EphemeralClient shares a module-level store across instances in the same process.
"""
import pytest
import chromadb

from cerebral.memory.manager import Memory, MemoryManager


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def chroma(tmp_path):
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


@pytest.fixture
def mgr(chroma):
    return MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)


# ── Slice 1: remember() stores a fact and returns a string ID ─────────────────

async def test_remember_returns_string_id(mgr):
    memory_id = await mgr.remember("My favourite colour is blue")
    assert isinstance(memory_id, str)
    assert len(memory_id) > 0


async def test_remember_assigns_unique_ids(mgr):
    id1 = await mgr.remember("I like cats")
    id2 = await mgr.remember("I like dogs")
    assert id1 != id2


# ── Slice 2: recall() returns relevant memories ───────────────────────────────

async def test_recall_returns_list(mgr):
    await mgr.remember("I enjoy hiking on weekends")
    results = await mgr.recall("hiking")
    assert isinstance(results, list)


async def test_recall_returns_memory_objects(mgr):
    await mgr.remember("The cat's name is Whiskers")
    results = await mgr.recall("cat name")
    assert all(isinstance(m, Memory) for m in results)


async def test_recall_finds_stored_fact(mgr):
    await mgr.remember("My sister's name is Alice")
    results = await mgr.recall("sister name")
    facts = [m.fact for m in results]
    assert any("Alice" in f for f in facts)


async def test_recall_empty_when_nothing_stored(mgr):
    results = await mgr.recall("anything")
    assert results == []


async def test_recall_memory_has_required_fields(mgr):
    await mgr.remember("I work at a tech company")
    results = await mgr.recall("work")
    assert len(results) > 0
    m = results[0]
    assert hasattr(m, "id")
    assert hasattr(m, "fact")
    assert hasattr(m, "profile_id")
    assert hasattr(m, "created_at")
    assert hasattr(m, "distance")


async def test_recall_memory_profile_id_matches(chroma):
    mgr = MemoryManager(profile_id=7, db_path=":memory:", chroma_client=chroma)
    await mgr.remember("I prefer tea over coffee")
    results = await mgr.recall("tea")
    assert all(m.profile_id == 7 for m in results)


async def test_recall_n_results_limits_output(mgr):
    for i in range(5):
        await mgr.remember(f"fact number {i} about food")
    results = await mgr.recall("food", n_results=2)
    assert len(results) <= 2


# ── Slice 3: profile isolation — no cross-profile recall ─────────────────────

async def test_profile_isolation_recall_only_own_memories(chroma):
    mgr_a = MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)
    mgr_b = MemoryManager(profile_id=2, db_path=":memory:", chroma_client=chroma)

    await mgr_a.remember("Profile A secret: favourite food is sushi")
    results_b = await mgr_b.recall("sushi")

    facts_b = [m.fact for m in results_b]
    assert not any("sushi" in f for f in facts_b)


async def test_profile_isolation_own_memories_visible(chroma):
    mgr_a = MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)
    mgr_b = MemoryManager(profile_id=2, db_path=":memory:", chroma_client=chroma)

    await mgr_a.remember("Profile A loves hiking")
    await mgr_b.remember("Profile B loves swimming")

    results_a = await mgr_a.recall("outdoor activity")
    results_b = await mgr_b.recall("outdoor activity")

    facts_a = [m.fact for m in results_a]
    facts_b = [m.fact for m in results_b]
    assert any("hiking" in f for f in facts_a)
    assert any("swimming" in f for f in facts_b)


# ── Slice 4: forget() removes a specific memory ───────────────────────────────

async def test_forget_returns_true_on_success(mgr):
    memory_id = await mgr.remember("I have a dentist appointment")
    result = await mgr.forget(memory_id)
    assert result is True


async def test_forget_returns_false_for_unknown_id(mgr):
    result = await mgr.forget("no-such-id")
    assert result is False


async def test_forgotten_memory_not_returned_by_recall(mgr):
    memory_id = await mgr.remember("I need to cancel my dentist appointment")
    await mgr.forget(memory_id)
    results = await mgr.recall("dentist appointment")
    facts = [m.fact for m in results]
    assert not any("dentist" in f for f in facts)


# ── Slice 5: set_preference / get_preference ─────────────────────────────────

def test_set_and_get_preference_round_trip(mgr):
    mgr.set_preference("theme", "dark")
    assert mgr.get_preference("theme") == "dark"


def test_get_preference_missing_key_returns_default(mgr):
    assert mgr.get_preference("nonexistent", default="fallback") == "fallback"


def test_get_preference_missing_key_default_is_empty_string(mgr):
    assert mgr.get_preference("nonexistent") == ""


def test_set_preference_overwrites_existing(mgr):
    mgr.set_preference("volume", "50")
    mgr.set_preference("volume", "80")
    assert mgr.get_preference("volume") == "80"


# ── Slice 6: list_preferences ────────────────────────────────────────────────

def test_list_preferences_returns_all_set_prefs(mgr):
    mgr.set_preference("theme", "dark")
    mgr.set_preference("language", "en")
    prefs = mgr.list_preferences()
    assert prefs.get("theme") == "dark"
    assert prefs.get("language") == "en"


def test_list_preferences_empty_when_none_set(mgr):
    assert mgr.list_preferences() == {}


# ── Slice 7: preference profile isolation ────────────────────────────────────

def test_preferences_are_profile_scoped(chroma):
    mgr_a = MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)
    mgr_b = MemoryManager(profile_id=2, db_path=":memory:", chroma_client=chroma)

    mgr_a.set_preference("theme", "dark")
    assert mgr_b.get_preference("theme") == ""


# ── Slice 8: persistence across manager instances ────────────────────────────

def test_preferences_persist_across_instances(tmp_path, chroma):
    db = str(tmp_path / "mem.db")

    mgr1 = MemoryManager(profile_id=1, db_path=db, chroma_client=chroma)
    mgr1.set_preference("wake_name", "felix")

    mgr2 = MemoryManager(profile_id=1, db_path=db, chroma_client=chroma)
    assert mgr2.get_preference("wake_name") == "felix"
