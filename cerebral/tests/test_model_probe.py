"""Model reachability probe for the status dots (feat/model-status-dot).

Hermetic: injected fake backends, no HTTP. probe_model/probe_enabled must never
raise and must resolve a slow/hung endpoint as "down" within the probe timeout.
"""

import asyncio

from unittest.mock import AsyncMock

from cerebral.llm.router import ModelRouter


def _router(backends, models=None):
    return ModelRouter(backends=backends, models=models)


async def test_probe_model_up():
    b = AsyncMock(); b.complete.return_value = "pong"
    r = _router({"ollama/x": b})
    assert await r.probe_model("ollama/x") is True


async def test_probe_model_down_on_exception():
    b = AsyncMock(); b.complete.side_effect = ConnectionError("boom")
    r = _router({"ollama/x": b})
    assert await r.probe_model("ollama/x") is False


async def test_probe_model_unknown_id_is_false():
    b = AsyncMock(); b.complete.return_value = "pong"
    r = _router({"ollama/x": b})
    assert await r.probe_model("nope") is False


async def test_probe_model_times_out_as_down(monkeypatch):
    import cerebral.llm.router as mod
    monkeypatch.setattr(mod, "_PROBE_TIMEOUT_SEC", 0.05)

    class _Hang:
        async def complete(self, prompt, task_type="chat"):
            await asyncio.sleep(1)  # far longer than the patched timeout
            return "late"

    r = _router({"ollama/x": _Hang()})
    assert await r.probe_model("ollama/x") is False


async def test_probe_enabled_maps_all_and_skips_disabled():
    up = AsyncMock(); up.complete.return_value = "ok"
    down = AsyncMock(); down.complete.side_effect = RuntimeError("x")
    off = AsyncMock(); off.complete.return_value = "ok"
    r = _router({"a": up, "b": down, "c": off})
    r.set_model_enabled("c", False)

    health = await r.probe_enabled()

    assert health == {"a": True, "b": False}  # disabled 'c' is never probed
    off.complete.assert_not_called()


async def test_probe_enabled_hides_cloud_in_local_only():
    local = AsyncMock(); local.complete.return_value = "ok"
    cloud = AsyncMock(); cloud.complete.return_value = "ok"
    r = _router(
        {"ollama/x": local, "claude/y": cloud},
        models={
            "ollama/x": {"label": "x", "is_cloud": False},
            "claude/y": {"label": "y", "is_cloud": True},
        },
    )
    r.set_local_only(True)

    health = await r.probe_enabled()

    assert "claude/y" not in health  # cloud hidden in local-only, not probed
    assert health["ollama/x"] is True
    cloud.complete.assert_not_called()
