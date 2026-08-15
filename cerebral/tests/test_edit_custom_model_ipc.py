"""
IPC handler test for edit_custom_model.

The point of edit (vs remove+add) is that the connection keeps its id, so its
priority position and any per-task pins (coding/self_dev) that reference it
survive the edit. Also verifies a blank api_key reuses the stored credential.
"""
from types import SimpleNamespace
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


@pytest.fixture
def _env(monkeypatch):
    old = AsyncMock()
    r = ModelRouter(
        backends={"ollama/a": AsyncMock(), "custom/box": old},
        models={
            "ollama/a": {"label": "A", "is_cloud": False},
            "custom/box": {"label": "Box", "is_cloud": True},
        },
        default_model="ollama/a",
    )
    # Pin a coding task to the connection we're about to edit.
    r.set_task_model("coding", "custom/box")
    monkeypatch.setattr(main_mod, "_router", r)
    monkeypatch.setattr(main_mod, "_active_profile", SimpleNamespace(id=1))
    monkeypatch.setattr(main_mod, "_persist_priority", lambda: None)

    added = []
    monkeypatch.setattr(main_mod._custom_models, "add", lambda *a, **k: added.append(k))

    new_backend = AsyncMock()
    monkeypatch.setattr(
        main_mod, "build_custom_backend", lambda *a, **k: (new_backend, True)
    )

    async def ok_ping(_b):
        return None  # reachable

    monkeypatch.setattr(main_mod, "_ping_custom_model", ok_ping)

    sets = []
    cred = SimpleNamespace(
        get_secret=lambda pid, ref, field: "stored-key",
        set_secret=lambda pid, ref, field, val: sets.append(val),
    )
    monkeypatch.setattr(main_mod, "_get_credential_store", lambda: cred)
    return r, old, new_backend, added, sets


async def test_edit_preserves_id_position_and_task_pin(_env):
    r, old, new_backend, added, sets = _env
    await main_mod._handle_message({
        "type": "edit_custom_model",
        "data": {"id": "custom/box", "kind": "openai",
                 "url": "http://newhost", "model": "new-model",
                 "label": "Box Renamed", "api_key": ""},
    })
    # Backend replaced in place -- same id, same priority slot, pin intact.
    assert r._backends["custom/box"] is new_backend
    assert r.priority() == ["ollama/a", "custom/box"]
    assert r.get_task_model("coding") == "custom/box"
    # Row upserted under the same id with the new fields.
    assert added and added[-1]["id"] == "custom/box"
    assert added[-1]["url"] == "http://newhost"
    # Blank key reused the stored credential; keyring not rewritten.
    assert sets == []


async def test_edit_unknown_connection_errors(_env, _patch_broadcast):
    await main_mod._handle_message({
        "type": "edit_custom_model",
        "data": {"id": "custom/ghost", "kind": "openai",
                 "url": "http://h", "model": "m", "api_key": ""},
    })
    err = next((b for b in _patch_broadcast if b["type"] == "custom_model_error"), None)
    assert err is not None and "unknown connection" in err["data"]["error"]
