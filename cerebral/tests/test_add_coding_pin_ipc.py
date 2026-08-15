"""
add_custom_model with for_coding=True auto-pins the new connection to both the
coding-chat and self_dev task types -- the "just add the server" turnkey path.
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
    r = ModelRouter(
        backends={"ollama/a": AsyncMock()},
        models={"ollama/a": {"label": "A", "is_cloud": False}},
        default_model="ollama/a",
    )
    monkeypatch.setattr(main_mod, "_router", r)
    monkeypatch.setattr(main_mod, "_active_profile", SimpleNamespace(id=1))
    monkeypatch.setattr(main_mod, "_persist_priority", lambda: None)

    persisted = []
    monkeypatch.setattr(main_mod, "_persist_task_models", lambda: persisted.append(dict(r.task_models())))
    monkeypatch.setattr(main_mod._custom_models, "add", lambda *a, **k: None)
    monkeypatch.setattr(
        main_mod, "build_custom_backend", lambda *a, **k: (AsyncMock(), True)
    )

    async def ok_ping(_b):
        return None

    monkeypatch.setattr(main_mod, "_ping_custom_model", ok_ping)
    cred = SimpleNamespace(set_secret=lambda *a, **k: None, get_secret=lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_get_credential_store", lambda: cred)
    return r, persisted


async def test_add_for_coding_pins_coding_and_self_dev(_env):
    r, persisted = _env
    await main_mod._handle_message({
        "type": "add_custom_model",
        "data": {"kind": "openai", "url": "http://h", "model": "gpt-x",
                 "label": "coder", "api_key": "", "for_coding": True},
    })
    mid = "custom/coder"
    assert r.get_task_model("coding") == mid
    assert r.get_task_model("self_dev") == mid
    assert persisted  # pins were persisted


async def test_add_without_for_coding_leaves_pins_unset(_env):
    r, persisted = _env
    await main_mod._handle_message({
        "type": "add_custom_model",
        "data": {"kind": "openai", "url": "http://h", "model": "gpt-x",
                 "label": "plain", "api_key": ""},
    })
    assert "coding" not in r.task_models()
    assert "self_dev" not in r.task_models()
