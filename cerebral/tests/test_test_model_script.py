"""
Tests for scripts/test_model.py — Issue #757.

A stale `profiles.active_model` (server renamed/removed) must not be reported
as NOT CONNECTED when another stored model actually answers. Hermetic: no
network, no real Cerebral, no real model — profile lookup, custom-model
store, and backend construction are all monkeypatched.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import test_model as tm  # noqa: E402


class _FakeBackend:
    def __init__(self, reply="OK", raise_exc=None, supports_vision=False):
        self._reply = reply
        self._raise_exc = raise_exc
        self.supports_vision = supports_vision

    async def complete(self, prompt, task_type="chat"):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._reply


def _row(mid, kind="openai", url="http://x"):
    return {"id": mid, "kind": kind, "url": url, "model": "m", "label": "",
            "is_cloud": False, "secret_ref": "", "dynamic": False,
            "supports_vision": False}


@pytest.fixture
def profile():
    return SimpleNamespace(id=1, active_model="custom/budd")


def _patch_profile(monkeypatch, profile):
    fake_pm = SimpleNamespace(get_active=lambda: profile)
    monkeypatch.setattr(
        "cerebral.db.profiles.ProfileManager", lambda *a, **k: fake_pm)


def _patch_store(monkeypatch, rows):
    fake_store = SimpleNamespace(list=lambda profile_id: rows)
    monkeypatch.setattr(
        "cerebral.db.custom_models.CustomModelStore", lambda *a, **k: fake_store)


def _patch_build_custom_backend(monkeypatch, backend_by_model):
    def fake_build(kind, url, model, api_key=None, supports_vision=False):
        return backend_by_model[model], False
    monkeypatch.setattr("cerebral.llm.router.build_custom_backend", fake_build)


async def test_stale_active_model_falls_back_and_connects(monkeypatch, profile, capsys):
    """custom/budd is gone; custom/budd-quick is still stored and answers."""
    _patch_profile(monkeypatch, profile)
    _patch_store(monkeypatch, [_row("custom/budd-quick")])
    _patch_build_custom_backend(monkeypatch, {"m": _FakeBackend(reply="OK")})

    code = await tm._run()
    out = capsys.readouterr().out

    assert code == 0
    assert "CONNECTED" in out
    assert "custom/budd" in out  # names the stale id


async def test_nothing_resolvable_reports_not_connected(monkeypatch, profile, capsys):
    _patch_profile(monkeypatch, profile)
    _patch_store(monkeypatch, [])  # no stored models at all -> no fallback

    code = await tm._run()
    out = capsys.readouterr().out

    assert code == 1
    assert "NOT CONNECTED" in out


async def test_resolvable_model_ping_failure_reports_not_connected(monkeypatch, profile, capsys):
    """active_model resolves directly (not stale) but the ping itself raises."""
    profile.active_model = "custom/budd-quick"
    _patch_profile(monkeypatch, profile)
    _patch_store(monkeypatch, [_row("custom/budd-quick")])
    _patch_build_custom_backend(
        monkeypatch, {"m": _FakeBackend(raise_exc=RuntimeError("boom"))})

    code = await tm._run()
    out = capsys.readouterr().out

    assert code == 1
    assert "NOT CONNECTED" in out
    assert "RuntimeError" in out
