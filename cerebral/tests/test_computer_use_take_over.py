"""S15 #609 IPC handlers: computer_use_take_over + computer_use_release.

Same shape as the existing computer_use_stop handler tests (implicit via
test_computer_use_gate.py) -- stub _orc so main.py's handler finds a fake
plugin module, capture broadcasts, assert the pause/resume + flip.
"""
from __future__ import annotations

from types import SimpleNamespace

import cerebral.main as main_mod


class _FakePluginModule:
    """Records pause/resume calls; matches the plugins/computer_use.py surface
    the handler actually reaches for (module-level functions)."""

    def __init__(self) -> None:
        self.pause_calls = 0
        self.resume_calls = 0

    def pause_current(self) -> None:
        self.pause_calls += 1

    def resume_current(self) -> None:
        self.resume_calls += 1


def _install_fake(monkeypatch):
    fake = _FakePluginModule()
    monkeypatch.setattr(
        main_mod, "_orc",
        SimpleNamespace(get_plugin_module=lambda name: fake if name == "computer_use" else (_ for _ in ()).throw(KeyError(name))),
    )
    events: list[dict] = []

    async def fake_broadcast(e):
        events.append(e)

    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_computer_use_taken_over", False)
    return fake, events


async def test_take_over_pauses_plugin_and_broadcasts(monkeypatch):
    fake, events = _install_fake(monkeypatch)
    await main_mod._handle_message({"type": "computer_use_take_over"})
    assert fake.pause_calls == 1
    assert main_mod._computer_use_taken_over is True
    assert any(
        e["type"] == "computer_use:taken_over" and e["data"]["taken_over"] is True
        for e in events
    )


async def test_release_resumes_plugin_and_broadcasts(monkeypatch):
    fake, events = _install_fake(monkeypatch)
    monkeypatch.setattr(main_mod, "_computer_use_taken_over", True)
    await main_mod._handle_message({"type": "computer_use_release"})
    assert fake.resume_calls == 1
    assert main_mod._computer_use_taken_over is False
    assert any(
        e["type"] == "computer_use:taken_over" and e["data"]["taken_over"] is False
        for e in events
    )


async def test_take_over_when_plugin_not_loaded_is_a_noop(monkeypatch):
    """The IPC handler tolerates a not-loaded plugin (early boot / hot reload)
    the same way computer_use_stop does -- log & return, no crash."""
    events: list[dict] = []

    async def fake_broadcast(e):
        events.append(e)

    def raise_keyerror(name):
        raise KeyError(name)

    monkeypatch.setattr(
        main_mod, "_orc",
        SimpleNamespace(get_plugin_module=raise_keyerror),
    )
    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    await main_mod._handle_message({"type": "computer_use_take_over"})
    # No broadcast when the plugin wasn't loaded -- no state to advertise.
    assert events == []


async def test_take_over_reuses_pause_seam_not_a_new_wire(monkeypatch):
    """AC 2 -- take-over MUST reuse the same pause pipe as the kill switch
    seam (not a new IPC path). Regression guard: if this test ever fails
    because pause_current is gone, the wiring drifted from the AC."""
    import plugins.computer_use as cu
    assert hasattr(cu, "pause_current")
    assert hasattr(cu, "resume_current")
