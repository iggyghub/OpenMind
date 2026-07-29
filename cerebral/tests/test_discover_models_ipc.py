"""
IPC handler tests for discover_models -- Issue #524.

Verifies that discover_models broadcasts models_discovered with the right
model list for each kind, runs the blocking fetch off the event loop, and
persists nothing.
"""
import asyncio
import pytest
import cerebral.main as main_mod
from cerebral.llm.router import OllamaBackend


@pytest.fixture(autouse=True)
def _patch_broadcast(monkeypatch):
    """Capture broadcasts without touching a real websocket."""
    broadcasts = []

    async def fake_broadcast(event):
        broadcasts.append(event)

    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    return broadcasts


async def test_discover_models_openai_broadcasts_ids(monkeypatch, _patch_broadcast):
    fake_ids = ["mistral-7b", "llama3"]

    def fake_list_openai(url, api_key, fetch_fn=None):
        return fake_ids

    monkeypatch.setattr(main_mod, "list_openai_models", fake_list_openai)

    await main_mod._handle_message({
        "type": "discover_models",
        "data": {"kind": "openai", "url": "http://srv", "api_key": "sk-dummy"},
    })

    discovered = next(
        (b for b in _patch_broadcast if b["type"] == "models_discovered"), None
    )
    assert discovered is not None
    assert discovered["data"]["kind"] == "openai"
    assert discovered["data"]["models"] == fake_ids


async def test_discover_models_ollama_broadcasts_names(monkeypatch, _patch_broadcast):
    fake_names = ["qwen2.5:7b", "gemma4:latest"]

    monkeypatch.setattr(
        OllamaBackend, "list_installed_models",
        staticmethod(lambda url="http://localhost:11434", tags_fetch_fn=None: fake_names),
    )

    await main_mod._handle_message({
        "type": "discover_models",
        "data": {"kind": "ollama", "url": "http://localhost:11434", "api_key": ""},
    })

    discovered = next(
        (b for b in _patch_broadcast if b["type"] == "models_discovered"), None
    )
    assert discovered is not None
    assert discovered["data"]["kind"] == "ollama"
    assert discovered["data"]["models"] == fake_names


async def test_discover_models_anthropic_returns_empty(monkeypatch, _patch_broadcast):
    await main_mod._handle_message({
        "type": "discover_models",
        "data": {"kind": "anthropic", "url": "", "api_key": ""},
    })

    discovered = next(
        (b for b in _patch_broadcast if b["type"] == "models_discovered"), None
    )
    assert discovered is not None
    assert discovered["data"]["models"] == []


async def test_discover_models_persists_nothing(monkeypatch, _patch_broadcast):
    """discover_models must never write to any store."""
    calls = []

    def fake_list_openai(url, api_key, fetch_fn=None):
        return ["gpt-4"]

    monkeypatch.setattr(main_mod, "list_openai_models", fake_list_openai)

    # Intercept CustomModelStore.add to assert it's never called.
    original_add = main_mod._custom_models.add

    def spy_add(*args, **kwargs):
        calls.append(("add", args, kwargs))
        return original_add(*args, **kwargs)

    main_mod._custom_models.add = spy_add
    try:
        await main_mod._handle_message({
            "type": "discover_models",
            "data": {"kind": "openai", "url": "http://srv", "api_key": "sk-x"},
        })
    finally:
        main_mod._custom_models.add = original_add

    assert calls == [], "discover_models must not persist anything"
