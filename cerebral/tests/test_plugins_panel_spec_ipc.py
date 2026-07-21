"""IPC tests for plugins:panels + plugins:panel_spec (UI2 A3 -- #483).

Plugins declare panels by implementing ``panel_spec(profile_id) -> dict``. The
main dispatcher exposes:

  - ``plugins:panels``      -> ``{type, data: {panels: [{plugin_name, title}]}}``
  - ``plugins:panel_spec``  -> ``{type, data: {plugin_name, spec}}``

Plugins without ``panel_spec`` are silently omitted, and a plugin that raises
inside its ``panel_spec`` returns ``spec=None`` rather than crashing the WS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class _Profile:
    def __init__(self, pid: int) -> None:
        self.id = pid


class _PanelPlugin:
    """A plugin that declares a panel via panel_spec()."""

    def __init__(self, name: str, title: str, seen_profile_ids: list) -> None:
        self.name = name
        self._title = title
        self._seen = seen_profile_ids

    def list_tools(self):
        return []

    def panel_spec(self, profile_id):
        self._seen.append(profile_id)
        return {
            "title": self._title,
            "widgets": [
                {"type": "list", "items": [{"title": "row"}]},
                {"type": "detail", "fields": [{"label": "Profile", "value": str(profile_id)}]},
            ],
        }


class _PlainPlugin:
    """A plugin with no panel_spec method -- must be omitted from plugins:panels."""

    def __init__(self, name: str) -> None:
        self.name = name

    def list_tools(self):
        return []


class _RaisingPanelPlugin:
    """A plugin whose panel_spec explodes -- must map to spec=None, no crash."""

    def __init__(self, name: str) -> None:
        self.name = name

    def list_tools(self):
        return []

    def panel_spec(self, _profile_id):
        raise RuntimeError("boom")


class FakeOrchestrator:
    def __init__(self, plugins: dict) -> None:
        self._plugins = plugins


@pytest.fixture
def rig(monkeypatch):
    import cerebral.main as main_mod

    profile = _Profile(7)
    sent: list[dict] = []
    seen_profile_ids: list = []

    async def fake_broadcast(event):
        sent.append(event)

    plugins = {
        "documents": _PanelPlugin("documents", "Documents", seen_profile_ids),
        "gmail":     _PlainPlugin("gmail"),
        "broken":    _RaisingPanelPlugin("broken"),
    }

    monkeypatch.setattr(main_mod, "_orc", FakeOrchestrator(plugins))
    monkeypatch.setattr(main_mod, "_active_profile", profile)
    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_connected", set())

    class Rig:
        def __init__(self):
            self.main = main_mod
            self.sent = sent
            self.seen_profile_ids = seen_profile_ids

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def only(self, event_type: str) -> dict:
            evts = [e for e in self.sent if e.get("type") == event_type]
            assert len(evts) == 1, f"expected one {event_type} event, got {evts}"
            return evts[0]["data"]

    return Rig()


# ── plugins:panels ──────────────────────────────────────────────────────────

async def test_plugins_panels_lists_only_plugins_with_panel_spec(rig):
    await rig.handle({"type": "plugins:panels"})
    data = rig.only("plugins:panels")
    names = [p["plugin_name"] for p in data["panels"]]
    # documents declares one; gmail has no panel_spec; broken raises -> excluded.
    assert names == ["documents"]
    # Title comes from the spec, not the plugin name.
    assert data["panels"][0]["title"] == "Documents"


async def test_plugins_panels_empty_when_no_plugin_declares_one(monkeypatch, rig):
    monkeypatch.setattr(rig.main, "_orc", FakeOrchestrator({
        "gmail": _PlainPlugin("gmail"),
    }))
    await rig.handle({"type": "plugins:panels"})
    data = rig.only("plugins:panels")
    assert data == {"panels": []}


# ── plugins:panel_spec ──────────────────────────────────────────────────────

async def test_plugins_panel_spec_returns_full_spec(rig):
    await rig.handle({"type": "plugins:panel_spec", "data": {"plugin_name": "documents"}})
    data = rig.only("plugins:panel_spec")
    assert data["plugin_name"] == "documents"
    spec = data["spec"]
    assert spec is not None
    assert spec["title"] == "Documents"
    assert [w["type"] for w in spec["widgets"]] == ["list", "detail"]


async def test_plugins_panel_spec_passes_active_profile_id(rig):
    await rig.handle({"type": "plugins:panel_spec", "data": {"plugin_name": "documents"}})
    assert rig.seen_profile_ids == [7]


async def test_plugins_panel_spec_none_for_plugin_without_method(rig):
    await rig.handle({"type": "plugins:panel_spec", "data": {"plugin_name": "gmail"}})
    data = rig.only("plugins:panel_spec")
    assert data == {"plugin_name": "gmail", "spec": None}


async def test_plugins_panel_spec_none_for_raising_plugin(rig):
    await rig.handle({"type": "plugins:panel_spec", "data": {"plugin_name": "broken"}})
    data = rig.only("plugins:panel_spec")
    assert data == {"plugin_name": "broken", "spec": None}


async def test_plugins_panel_spec_none_for_unknown_plugin(rig):
    await rig.handle({"type": "plugins:panel_spec", "data": {"plugin_name": "ghost"}})
    data = rig.only("plugins:panel_spec")
    assert data == {"plugin_name": "ghost", "spec": None}


async def test_plugins_panel_spec_ignored_when_name_blank(rig):
    await rig.handle({"type": "plugins:panel_spec", "data": {"plugin_name": ""}})
    # No broadcast for a blank name -- silent no-op.
    assert not any(e.get("type") == "plugins:panel_spec" for e in rig.sent)


async def test_plugins_panel_spec_no_active_profile_still_returns_spec(monkeypatch, rig):
    monkeypatch.setattr(rig.main, "_active_profile", None)
    await rig.handle({"type": "plugins:panel_spec", "data": {"plugin_name": "documents"}})
    data = rig.only("plugins:panel_spec")
    assert data["spec"] is not None
    # panel_spec still gets called -- but with profile_id=None.
    assert rig.seen_profile_ids == [None]
