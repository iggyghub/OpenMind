"""
IPC handler tests for P1 #531 -- model priority + enabled + master fallback.

Verifies that set_model_priority / set_model_enabled / set_model_fallback:
- Mutate the router's state.
- Persist through the priority store (or profile row for fallback).
- Broadcast an updated models_list event carrying the new fields.
"""
from unittest.mock import AsyncMock

import pytest

import cerebral.main as main_mod
from cerebral.llm.router import ModelRouter


@pytest.fixture(autouse=True)
def _patch_broadcast(monkeypatch):
    broadcasts = []

    async def fake_broadcast(event):
        broadcasts.append(event)

    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    return broadcasts


@pytest.fixture(autouse=True)
def _swap_router(monkeypatch):
    """Replace the module-level router with a deterministic 3-backend one so the
    priority IPC handlers have something to reorder without hitting live Ollama."""
    r = ModelRouter(
        backends={"ollama/a": AsyncMock(), "ollama/b": AsyncMock(), "claude/haiku": AsyncMock()},
        models={
            "ollama/a": {"label": "A", "is_cloud": False},
            "ollama/b": {"label": "B", "is_cloud": False},
            "claude/haiku": {"label": "Haiku", "is_cloud": True},
        },
        default_model="ollama/a",
    )
    monkeypatch.setattr(main_mod, "_router", r)
    return r


async def test_set_model_priority_reorders_and_broadcasts(_swap_router, _patch_broadcast):
    await main_mod._handle_message({
        "type": "set_model_priority",
        "data": {"order": ["claude/haiku", "ollama/b", "ollama/a"]},
    })
    assert _swap_router.priority() == ["claude/haiku", "ollama/b", "ollama/a"]
    assert _swap_router.active_model == "claude/haiku"
    event = next(b for b in _patch_broadcast if b["type"] == "models_list")
    assert [m["id"] for m in event["data"]["models"]] == [
        "claude/haiku", "ollama/b", "ollama/a",
    ]


async def test_set_model_enabled_toggles_and_broadcasts(_swap_router, _patch_broadcast):
    await main_mod._handle_message({
        "type": "set_model_enabled",
        "data": {"model_id": "ollama/a", "enabled": False},
    })
    assert _swap_router.enabled_map()["ollama/a"] is False
    # Derived active moves to the next enabled model in priority order
    assert _swap_router.active_model == "ollama/b"
    event = next(b for b in _patch_broadcast if b["type"] == "models_list")
    row_a = next(m for m in event["data"]["models"] if m["id"] == "ollama/a")
    assert row_a["enabled"] is False


async def test_set_model_fallback_toggles_and_persists(monkeypatch, _swap_router, _patch_broadcast):
    calls = []

    def spy(pid, val):
        calls.append((pid, val))

    monkeypatch.setattr(main_mod._pm, "update_fallback_enabled", spy)
    await main_mod._handle_message({
        "type": "set_model_fallback", "data": {"enabled": True},
    })
    assert _swap_router.fallback_enabled is True
    if main_mod._active_profile:
        assert calls and calls[-1][1] is True
    event = next(b for b in _patch_broadcast if b["type"] == "models_list")
    assert event["data"]["fallback_enabled"] is True


async def test_set_model_priority_unknown_ids_dropped(_swap_router, _patch_broadcast):
    """Set-priority is forgiving: unknown ids are dropped, missing backends appended."""
    await main_mod._handle_message({
        "type": "set_model_priority",
        "data": {"order": ["bogus/x", "ollama/b"]},
    })
    order = _swap_router.priority()
    assert order[0] == "ollama/b"
    assert "bogus/x" not in order
    assert set(order) == {"ollama/a", "ollama/b", "claude/haiku"}


async def test_models_list_event_carries_fallback_flag(_swap_router, _patch_broadcast):
    _swap_router.set_fallback(True)
    await main_mod._handle_message({"type": "list_models"})
    event = next(b for b in _patch_broadcast if b["type"] == "models_list")
    assert event["data"]["fallback_enabled"] is True
    assert "models" in event["data"]
