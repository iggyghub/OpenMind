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


# ── Slice 9: list_all() — the tray review surface (Issue #82) ─────────────────

def test_list_all_empty_when_nothing_stored(mgr):
    assert mgr.list_all() == []


async def test_list_all_returns_all_stored(mgr):
    await mgr.remember("Fact one")
    await mgr.remember("Fact two")
    await mgr.remember("Fact three")
    facts = {m.fact for m in mgr.list_all()}
    assert facts == {"Fact one", "Fact two", "Fact three"}


async def test_list_all_returns_memory_objects_with_fields(mgr):
    await mgr.remember("I drive a blue car")
    mems = mgr.list_all()
    assert len(mems) == 1
    m = mems[0]
    assert isinstance(m, Memory)
    assert m.fact == "I drive a blue car"
    assert m.profile_id == 1
    assert m.created_at != ""
    assert m.distance == 0.0


def test_list_all_newest_first(mgr):
    # Insert with controlled timestamps so ordering is deterministic
    # regardless of clock resolution.
    mgr._collection.add(
        documents=["oldest", "middle", "newest"],
        ids=["a", "b", "c"],
        metadatas=[
            {"profile_id": 1, "created_at": "2026-01-01T00:00:00+00:00"},
            {"profile_id": 1, "created_at": "2026-03-01T00:00:00+00:00"},
            {"profile_id": 1, "created_at": "2026-05-01T00:00:00+00:00"},
        ],
    )
    assert [m.fact for m in mgr.list_all()] == ["newest", "middle", "oldest"]


async def test_list_all_is_profile_scoped(chroma):
    mgr_a = MemoryManager(profile_id=1, db_path=":memory:", chroma_client=chroma)
    mgr_b = MemoryManager(profile_id=2, db_path=":memory:", chroma_client=chroma)
    await mgr_a.remember("Profile A fact")
    await mgr_b.remember("Profile B fact")
    assert [m.fact for m in mgr_a.list_all()] == ["Profile A fact"]
    assert [m.fact for m in mgr_b.list_all()] == ["Profile B fact"]


# ── Slice 10: edit() — in-place fact update (Issue #82) ──────────────────────

async def test_edit_returns_true_and_updates_fact(mgr):
    memory_id = await mgr.remember("My phone is an Android")
    ok = await mgr.edit(memory_id, "My phone is an iPhone")
    assert ok is True
    mems = mgr.list_all()
    assert len(mems) == 1
    assert mems[0].fact == "My phone is an iPhone"


async def test_edit_keeps_same_id(mgr):
    memory_id = await mgr.remember("original")
    await mgr.edit(memory_id, "updated")
    assert mgr.list_all()[0].id == memory_id


async def test_edit_preserves_created_at(mgr):
    memory_id = await mgr.remember("preserve my timestamp")
    before = mgr.list_all()[0].created_at
    await mgr.edit(memory_id, "timestamp must not change")
    assert mgr.list_all()[0].created_at == before


async def test_edit_unknown_id_returns_false(mgr):
    assert await mgr.edit("no-such-id", "whatever") is False


@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
async def test_edit_blank_fact_returns_false(mgr, bad):
    memory_id = await mgr.remember("keep me")
    assert await mgr.edit(memory_id, bad) is False
    assert mgr.list_all()[0].fact == "keep me"


async def test_edited_fact_is_recallable(mgr):
    memory_id = await mgr.remember("I have a goldfish")
    await mgr.edit(memory_id, "I have a parrot named Kiwi")
    results = await mgr.recall("parrot")
    assert any("Kiwi" in m.fact for m in results)


# ── S20: category tags group memories on the Memory page ──────────────────────

async def test_remember_stores_category(mgr):
    await mgr.remember("Grant curation makes $500-3k/mo", category="money-making idea")
    mems = mgr.list_all()
    assert mems[0].category == "money-making idea"


async def test_remember_defaults_to_blank_category(mgr):
    await mgr.remember("My favourite colour is blue")
    assert mgr.list_all()[0].category == ""


async def test_recall_carries_category(mgr):
    await mgr.remember("Unclaimed asset recovery pays per claim", category="money-making idea")
    results = await mgr.recall("unclaimed asset")
    assert results and results[0].category == "money-making idea"


# ── Memory folder system: drag between categories, manual reorder, copy/paste ──

async def test_remember_assigns_order_from_creation_time(mgr):
    memory_id = await mgr.remember("ordered by creation")
    mem = mgr.list_all()[0]
    assert mem.id == memory_id
    assert mem.order > 0


async def test_list_all_sorts_by_order_descending(mgr):
    id1 = await mgr.remember("first")
    id2 = await mgr.remember("second")
    # untouched order defaults to creation time, so newest (id2) still leads
    assert [m.id for m in mgr.list_all()] == [id2, id1]


async def test_set_category_moves_memory_to_new_folder(mgr):
    memory_id = await mgr.remember("uncategorised fact")
    ok = await mgr.set_category(memory_id, "New Folder")
    assert ok is True
    assert mgr.list_all()[0].category == "New Folder"


async def test_set_category_preserves_fact_and_created_at(mgr):
    memory_id = await mgr.remember("do not touch my text")
    before = mgr.list_all()[0]
    await mgr.set_category(memory_id, "moved")
    after = mgr.list_all()[0]
    assert after.fact == before.fact
    assert after.created_at == before.created_at


async def test_set_category_unknown_id_returns_false(mgr):
    assert await mgr.set_category("no-such-id", "whatever") is False


async def test_set_order_changes_manual_position(mgr):
    id1 = await mgr.remember("older")
    id2 = await mgr.remember("newer")
    # id2 normally sorts first (newer); drag id1 above it with a higher order.
    ok = await mgr.set_order(id1, 9_999_999_999)
    assert ok is True
    assert [m.id for m in mgr.list_all()] == [id1, id2]


async def test_set_order_unknown_id_returns_false(mgr):
    assert await mgr.set_order("no-such-id", 1.0) is False


async def test_duplicate_creates_independent_copy_with_same_fact(mgr):
    memory_id = await mgr.remember("copy me", category="folder-a")
    new_id = await mgr.duplicate(memory_id)
    assert new_id is not None
    assert new_id != memory_id
    mems = {m.id: m for m in mgr.list_all()}
    assert len(mems) == 2
    assert mems[new_id].fact == "copy me"
    assert mems[new_id].category == "folder-a"


async def test_duplicate_can_override_destination_category(mgr):
    memory_id = await mgr.remember("copy me elsewhere", category="folder-a")
    new_id = await mgr.duplicate(memory_id, category="folder-b")
    mems = {m.id: m for m in mgr.list_all()}
    assert mems[new_id].category == "folder-b"
    assert mems[memory_id].category == "folder-a"  # original untouched


async def test_duplicate_unknown_id_returns_none(mgr):
    assert await mgr.duplicate("no-such-id") is None
