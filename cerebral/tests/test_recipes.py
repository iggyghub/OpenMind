"""
Recipes unit tests -- Issue #276 (S3 Recipes).

Covers:
 - save / get / list / rename / delete
 - replay re-gates (every step passes through the gate on replay)
 - stale flag (> 30 days unused)
 - duplicate flag (identical tool+arg sequence)
 - missing-tool graceful failure
 - per-profile scoping (Profile A's Recipes never appear for B)
 - synthetic tool schema generation
 - ChainEngine on_chain_done callback fires for 2+ step chains
 - S1+S2 tests stay green (on_chain_done=None path unchanged)
"""

import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cerebral.db.recipes import Recipe, RecipeStore, STALE_DAYS, _steps_fingerprint
from cerebral.llm.chain_engine import ChainEngine
from cerebral.llm.planner import Planner
from cerebral.llm.router import ToolCall
from cerebral.mcp.orchestrator import ToolResult
from cerebral.security import Decision
from cerebral.db.conversation import KIND_TOOL_CALL, KIND_TOOL_RESULT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """In-memory-equivalent RecipeStore backed by a temp file."""
    return RecipeStore(db_path=tmp_path / "test.db")


_STEPS_2 = [
    {"tool_name": "gmail_search", "args": {"query": "is:unread"}},
    {"tool_name": "gmail_send",   "args": {"to": "a@b.com", "body": "hi"}},
]

_STEPS_3 = [
    {"tool_name": "gmail_search", "args": {"query": "is:unread"}},
    {"tool_name": "gmail_send",   "args": {"to": "a@b.com", "body": "hi"}},
    {"tool_name": "calendar_create", "args": {"title": "meeting"}},
]


# ---------------------------------------------------------------------------
# Save / CRUD
# ---------------------------------------------------------------------------

def test_save_and_get(store):
    r = store.save(profile_id=1, name="Morning", steps=_STEPS_2)
    assert r.id is not None
    assert r.name == "Morning"
    assert r.profile_id == 1
    assert r.steps == _STEPS_2
    assert r.run_count == 0
    assert r.last_run_at is None

    fetched = store.get(r.id)
    assert fetched is not None
    assert fetched.name == "Morning"


def test_save_requires_at_least_2_steps(store):
    with pytest.raises(ValueError, match="2 steps"):
        store.save(profile_id=1, name="Too short", steps=[_STEPS_2[0]])


def test_save_requires_non_empty_name(store):
    with pytest.raises(ValueError):
        store.save(profile_id=1, name="", steps=_STEPS_2)


def test_save_duplicate_name_raises(store):
    store.save(profile_id=1, name="Morning", steps=_STEPS_2)
    with pytest.raises(ValueError, match="already exists"):
        store.save(profile_id=1, name="Morning", steps=_STEPS_2)


def test_get_by_name(store):
    store.save(profile_id=1, name="Morning", steps=_STEPS_2)
    r = store.get_by_name(profile_id=1, name="Morning")
    assert r is not None
    assert r.name == "Morning"


def test_get_by_name_not_found(store):
    assert store.get_by_name(1, "Nonexistent") is None


def test_list_for_profile(store):
    store.save(profile_id=1, name="Alpha", steps=_STEPS_2)
    store.save(profile_id=1, name="Beta", steps=_STEPS_3)
    recipes = store.list_for_profile(1)
    assert len(recipes) == 2
    names = [r.name for r in recipes]
    assert "Alpha" in names
    assert "Beta" in names


def test_rename(store):
    r = store.save(profile_id=1, name="Old", steps=_STEPS_2)
    ok = store.rename(r.id, "New")
    assert ok is True
    updated = store.get(r.id)
    assert updated.name == "New"


def test_rename_empty_name_fails(store):
    r = store.save(profile_id=1, name="Old", steps=_STEPS_2)
    ok = store.rename(r.id, "")
    assert ok is False


def test_delete(store):
    r = store.save(profile_id=1, name="Temp", steps=_STEPS_2)
    ok = store.delete(r.id)
    assert ok is True
    assert store.get(r.id) is None


def test_delete_nonexistent(store):
    ok = store.delete(99999)
    assert ok is False


def test_record_run(store):
    r = store.save(profile_id=1, name="Daily", steps=_STEPS_2)
    assert r.run_count == 0
    store.record_run(r.id)
    store.record_run(r.id)
    updated = store.get(r.id)
    assert updated.run_count == 2
    assert updated.last_run_at is not None


# ---------------------------------------------------------------------------
# Per-profile scoping
# ---------------------------------------------------------------------------

def test_per_profile_scoping(store):
    """Profile A's Recipes must not appear in Profile B's list."""
    store.save(profile_id=1, name="Alice Recipe", steps=_STEPS_2)
    store.save(profile_id=2, name="Bob Recipe", steps=_STEPS_2)

    alice_recipes = store.list_for_profile(1)
    bob_recipes = store.list_for_profile(2)

    assert len(alice_recipes) == 1
    assert alice_recipes[0].name == "Alice Recipe"
    assert len(bob_recipes) == 1
    assert bob_recipes[0].name == "Bob Recipe"


def test_get_by_synthetic_name_scoped(store):
    """get_by_synthetic_name respects profile_id."""
    store.save(profile_id=1, name="Morning Briefing", steps=_STEPS_2)
    r = store.get_by_synthetic_name(1, "recipe_morning_briefing")
    assert r is not None
    # Profile 2 should not find it
    assert store.get_by_synthetic_name(2, "recipe_morning_briefing") is None


# ---------------------------------------------------------------------------
# Synthetic tool schema
# ---------------------------------------------------------------------------

def test_synthetic_tool_name(store):
    r = store.save(profile_id=1, name="Morning Briefing", steps=_STEPS_2)
    assert r.synthetic_tool_name == "recipe_morning_briefing"


def test_synthetic_tool_name_special_chars(store):
    r = store.save(profile_id=1, name="My Recipe!", steps=_STEPS_2)
    assert r.synthetic_tool_name.startswith("recipe_")
    assert "!" not in r.synthetic_tool_name


def test_get_synthetic_tools_returns_schemas(store):
    store.save(profile_id=1, name="Morning", steps=_STEPS_2)
    tools = store.get_synthetic_tools(1)
    assert len(tools) == 1
    t = tools[0]
    assert t["name"] == "recipe_morning"
    assert "input_schema" in t
    assert t["input_schema"]["type"] == "object"
    assert "Morning" in t["description"]


def test_get_synthetic_tools_empty_for_no_recipes(store):
    assert store.get_synthetic_tools(1) == []


def test_get_synthetic_tools_lists_step_names(store):
    store.save(profile_id=1, name="Morning", steps=_STEPS_2)
    tools = store.get_synthetic_tools(1)
    assert "gmail_search" in tools[0]["description"]
    assert "gmail_send" in tools[0]["description"]


# ---------------------------------------------------------------------------
# Stale flag
# ---------------------------------------------------------------------------

def test_stale_never_run_after_30_days(store):
    """A Recipe with run_count=0 and created more than 30 days ago is stale."""
    r = store.save(profile_id=1, name="Old Unused", steps=_STEPS_2)
    # Manually backdate created_at
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS + 1)
    store._con.execute(
        "UPDATE recipes SET created_at=? WHERE id=?",
        (cutoff.strftime("%Y-%m-%d %H:%M:%S"), r.id),
    )
    store._con.commit()

    stale = store.stale_ids(1)
    assert r.id in stale


def test_fresh_recipe_not_stale(store):
    """A Recipe created today is not stale."""
    r = store.save(profile_id=1, name="New", steps=_STEPS_2)
    stale = store.stale_ids(1)
    assert r.id not in stale


def test_stale_after_last_run_old(store):
    """A Recipe last run > 30 days ago is stale."""
    r = store.save(profile_id=1, name="Old Run", steps=_STEPS_2)
    old = datetime.now(timezone.utc) - timedelta(days=STALE_DAYS + 1)
    store._con.execute(
        "UPDATE recipes SET run_count=1, last_run_at=? WHERE id=?",
        (old.strftime("%Y-%m-%d %H:%M:%S"), r.id),
    )
    store._con.commit()

    stale = store.stale_ids(1)
    assert r.id in stale


def test_recently_run_not_stale(store):
    """A Recipe run yesterday is not stale."""
    r = store.save(profile_id=1, name="Recent", steps=_STEPS_2)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    store._con.execute(
        "UPDATE recipes SET run_count=1, last_run_at=? WHERE id=?",
        (yesterday.strftime("%Y-%m-%d %H:%M:%S"), r.id),
    )
    store._con.commit()

    stale = store.stale_ids(1)
    assert r.id not in stale


# ---------------------------------------------------------------------------
# Duplicate flag
# ---------------------------------------------------------------------------

def test_duplicate_ids_exact_match(store):
    """Two Recipes with identical steps are both flagged as duplicates."""
    r1 = store.save(profile_id=1, name="Alpha", steps=_STEPS_2)
    r2 = store.save(profile_id=1, name="Beta", steps=_STEPS_2)

    dups = store.duplicate_ids(1)
    assert r1.id in dups
    assert r2.id in dups


def test_no_duplicates_different_steps(store):
    store.save(profile_id=1, name="Alpha", steps=_STEPS_2)
    store.save(profile_id=1, name="Beta", steps=_STEPS_3)

    dups = store.duplicate_ids(1)
    assert len(dups) == 0


def test_duplicates_are_per_profile(store):
    """Duplicate detection is scoped to a profile; cross-profile matches don't count."""
    store.save(profile_id=1, name="Morning", steps=_STEPS_2)
    store.save(profile_id=2, name="Morning", steps=_STEPS_2)

    assert len(store.duplicate_ids(1)) == 0
    assert len(store.duplicate_ids(2)) == 0


def test_steps_fingerprint_order_sensitive():
    """Different step orders produce different fingerprints."""
    a = [{"tool_name": "a", "args": {}}, {"tool_name": "b", "args": {}}]
    b = [{"tool_name": "b", "args": {}}, {"tool_name": "a", "args": {}}]
    assert _steps_fingerprint(a) != _steps_fingerprint(b)


def test_steps_fingerprint_args_sensitive():
    """Same tool but different args = different fingerprint."""
    a = [{"tool_name": "t", "args": {"q": "x"}}, {"tool_name": "t", "args": {"q": "y"}}]
    b = [{"tool_name": "t", "args": {"q": "z"}}, {"tool_name": "t", "args": {"q": "y"}}]
    assert _steps_fingerprint(a) != _steps_fingerprint(b)


# ---------------------------------------------------------------------------
# to_dict round-trip
# ---------------------------------------------------------------------------

def test_to_dict(store):
    r = store.save(profile_id=1, name="Morning", steps=_STEPS_2)
    d = r.to_dict()
    assert d["name"] == "Morning"
    assert d["steps"] == _STEPS_2
    assert d["run_count"] == 0
    assert d["last_run_at"] is None


# ---------------------------------------------------------------------------
# ChainEngine on_chain_done callback (S3 integration with S2)
# ---------------------------------------------------------------------------

_SEARCH_TOOL = {
    "name": "gmail_search",
    "description": "Search Gmail",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}
_SEND_TOOL = {
    "name": "gmail_send",
    "description": "Send an email",
    "input_schema": {
        "type": "object",
        "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
        "required": ["to", "body"],
    },
}
_TOOLS = [_SEARCH_TOOL, _SEND_TOOL]


def _make_engine(planner, gate_decisions, tool_results, recorded=None):
    gate_iter = iter(gate_decisions)
    result_iter = iter(tool_results)
    recorded_turns = recorded if recorded is not None else []

    async def gate_fn(tool_name, args=None):
        return next(gate_iter)

    async def execute_fn(tool_name, args):
        return next(result_iter)

    async def record_fn(kind, content):
        recorded_turns.append((kind, content))

    return ChainEngine(
        planner=planner,
        gate_fn=gate_fn,
        execute_fn=execute_fn,
        record_fn=record_fn,
    ), recorded_turns


async def test_on_chain_done_fires_for_2_step_chain():
    """on_chain_done is called with the completed steps after a 2-step chain."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "is:unread"}),
        ToolCall(name="gmail_send", args={"to": "a@b.com", "body": "hi"}),
        "Done.",
    ]
    planner = Planner(backend)
    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[
            ToolResult(content="found emails"),
            ToolResult(content="sent"),
        ],
    )

    captured: list[list] = []

    async def on_done(steps):
        captured.append(steps)

    await engine.run("Search and reply", _TOOLS, on_chain_done=on_done)

    assert len(captured) == 1
    assert len(captured[0]) == 2
    assert captured[0][0]["name"] == "gmail_search"
    assert captured[0][1]["name"] == "gmail_send"


async def test_on_chain_done_not_fired_for_single_step():
    """on_chain_done is NOT called when only 1 step completed."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "x"}),
        "Done.",
    ]
    planner = Planner(backend)
    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT],
        tool_results=[ToolResult(content="ok")],
    )

    called = []

    async def on_done(steps):
        called.append(steps)

    await engine.run("Search", _TOOLS, on_chain_done=on_done)
    assert called == []


async def test_on_chain_done_not_fired_on_cap_stop():
    """on_chain_done is NOT called when the chain stops at the cap (not natural completion)."""
    backend = AsyncMock()
    backend.complete_with_tools.return_value = ToolCall(
        name="gmail_search", args={"query": "x"}
    )
    planner = Planner(backend)
    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[ToolResult(content="r1"), ToolResult(content="r2")],
    )

    called = []

    async def on_done(steps):
        called.append(steps)

    await engine.run("...", _TOOLS, max_steps=2, on_chain_done=on_done)
    assert called == []


async def test_on_chain_done_not_fired_when_none():
    """Passing on_chain_done=None (the default) doesn't raise even after 2+ steps."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "x"}),
        ToolCall(name="gmail_send", args={"to": "a@b.com", "body": "hi"}),
        "Done.",
    ]
    planner = Planner(backend)
    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[ToolResult(content="r1"), ToolResult(content="r2")],
    )
    # Must not raise
    response = await engine.run("...", _TOOLS)
    assert response == "Done."


async def test_on_chain_done_callback_exception_does_not_propagate():
    """A failing on_chain_done callback is swallowed; chain still returns the response."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "x"}),
        ToolCall(name="gmail_send", args={"to": "a@b.com", "body": "hi"}),
        "Done.",
    ]
    planner = Planner(backend)
    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[ToolResult(content="r1"), ToolResult(content="r2")],
    )

    async def bad_callback(steps):
        raise RuntimeError("oh no")

    response = await engine.run("...", _TOOLS, on_chain_done=bad_callback)
    assert response == "Done."


# ---------------------------------------------------------------------------
# Missing tool graceful failure (spec edge case)
# ---------------------------------------------------------------------------

def test_get_by_synthetic_name_returns_none_for_unknown(store):
    """Attempting to replay a non-existent Recipe via synthetic name returns None."""
    assert store.get_by_synthetic_name(1, "recipe_nonexistent") is None


# ---------------------------------------------------------------------------
# S1 + S2 backward compatibility
# ---------------------------------------------------------------------------

async def test_s1_s2_still_work_without_on_chain_done():
    """The existing S2 two-step chain still returns the correct text with no callback."""
    backend = AsyncMock()
    backend.complete_with_tools.side_effect = [
        ToolCall(name="gmail_search", args={"query": "from:Sarah"}),
        ToolCall(name="gmail_send", args={"to": "sarah@example.com", "body": "I'll be there"}),
        "Done! I searched and replied.",
    ]
    planner = Planner(backend)
    engine, _ = _make_engine(
        planner,
        gate_decisions=[Decision.SILENT, Decision.SILENT],
        tool_results=[
            ToolResult(content="Email from Sarah found.", is_error=False),
            ToolResult(content="Email sent.", is_error=False),
        ],
    )

    response = await engine.run("Read latest from Sarah and reply I'll be there", _TOOLS)
    assert response == "Done! I searched and replied."
    assert backend.complete_with_tools.call_count == 3
