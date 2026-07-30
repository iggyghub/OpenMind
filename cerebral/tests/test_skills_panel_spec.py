"""Tests for the Skills plugin's declarative panel_spec (S5 #542, ADR-0014
decision 8) and its set_broadcast_fn seam (mirrors documents.py, S3 #454).

A plugin returns a widget tree, never HTML or JavaScript, so the renderer
(tray/lib/panel-spec.js) can draw it safely inside the Main window that also
hosts the Credentials UI (ADR-0012 decision 3).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import plugins.skills as skills_mod
from cerebral.settings import SettingsStore
from plugins.skills import SkillsPlugin


@pytest.fixture(autouse=True)
def _reset_broadcast_fn():
    """The broadcast callback is a module global (matches how cerebral.main's
    seam wires it). Reset around each test so one test's wiring doesn't leak."""
    skills_mod.set_broadcast_fn(None)
    yield
    skills_mod.set_broadcast_fn(None)

VALID_WIDGETS = frozenset({"list", "detail", "text", "action", "toggle"})


def _write_skill(root: Path, name: str, *, description="A test skill.",
                  tools=None, body="Do the thing.") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"name: {name}", f"description: {description}"]
    if tools is not None:
        fm.append("tools: [" + ", ".join(tools) + "]")
    text = "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n"
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def _plugin(tmp_path: Path, enabled=None) -> SkillsPlugin:
    settings = SettingsStore(path=tmp_path / "felix-settings.json")
    if enabled:
        settings.set("enabled_skills", list(enabled))
    return SkillsPlugin(
        seed_dir=tmp_path / "seed", installed_dir=tmp_path / "installed", settings=settings
    )


def _walk_strings(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _walk_strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk_strings(x)
    elif isinstance(v, str):
        yield v


# ── shape ─────────────────────────────────────────────────────────────────────

def test_panel_spec_shape_with_no_skills(tmp_path):
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)

    assert isinstance(spec, dict)
    assert spec["title"] == "Skills"
    widgets = spec["widgets"]
    assert isinstance(widgets, list)
    # Summary detail + install action, no skills.
    assert [w["type"] for w in widgets] == ["detail", "action"]
    for w in widgets:
        assert w["type"] in VALID_WIDGETS

    summary = widgets[0]
    assert "0 installed" in summary["fields"][0]["value"]

    install = widgets[1]
    assert install["tool"] == "skill_install"
    assert install["input_arg"] == "repo"


def test_panel_spec_lists_each_skill_as_detail_plus_toggle(tmp_path):
    _write_skill(tmp_path / "seed", "grill-me", tools=["ask_user"])
    _write_skill(tmp_path / "installed", "custom", tools=[])
    plugin = _plugin(tmp_path, enabled=["grill-me"])

    spec = plugin.panel_spec(1)
    widgets = spec["widgets"]
    types = [w["type"] for w in widgets]
    # detail(summary), action(install), then per skill: detail + toggle(+action)
    # custom (installed) also gets an uninstall action; grill-me (seed) does not.
    assert types == [
        "detail", "action",
        "detail", "toggle",           # custom: name line + on/off switch
        "action",                     # custom: uninstall
        "detail", "toggle",           # grill-me: name line + on/off switch
    ]

    # One compact line per skill: the name is the value; description + source
    # live in the hover hint (renderer -> native title tooltip).
    custom_detail = widgets[2]
    name_field = custom_detail["fields"][0]
    assert name_field["value"] == "custom"
    assert name_field["label"] == ""
    assert "A test skill." in name_field["hint"]
    assert "Source: installed" in name_field["hint"]  # no provenance sidecar written

    # The switch carries the current state (unchecked = disabled) and both
    # enable/disable tools; the handler picks one based on the flip direction.
    custom_toggle = widgets[3]
    assert custom_toggle["checked"] is False
    assert custom_toggle["label"] == "Disabled"  # word reflects state
    assert custom_toggle["enable_tool"] == "skill_enable"
    assert custom_toggle["disable_tool"] == "skill_disable"
    assert custom_toggle["tool_args"] == {"name": "custom"}

    custom_uninstall = widgets[4]
    assert custom_uninstall["tool"] == "skill_uninstall"
    assert custom_uninstall["tool_args"] == {"name": "custom"}

    # grill-me is enabled -> its switch is checked (on).
    grill_toggle = widgets[6]
    assert grill_toggle["checked"] is True
    assert grill_toggle["label"] == "Enabled"


def test_panel_spec_seed_skill_has_no_uninstall_action(tmp_path):
    _write_skill(tmp_path / "seed", "grill-me")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    tools = [w.get("tool") for w in spec["widgets"] if w["type"] == "action"]
    assert "skill_uninstall" not in tools


def test_panel_spec_installed_skill_has_uninstall_action(tmp_path):
    _write_skill(tmp_path / "installed", "custom")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    uninstalls = [
        w for w in spec["widgets"]
        if w["type"] == "action" and w.get("tool") == "skill_uninstall"
    ]
    assert len(uninstalls) == 1
    assert uninstalls[0]["tool_args"] == {"name": "custom"}


def test_panel_spec_provenance_from_sidecar(tmp_path):
    d = _write_skill(tmp_path / "installed", "custom")
    (d / ".provenance.json").write_text(
        json.dumps({"repo": "owner/repo", "sha": "abc1234def"}), encoding="utf-8"
    )
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    detail = next(w for w in spec["widgets"] if w["type"] == "detail" and w["id"] == "skill-custom")
    assert "Source: owner/repo@abc1234" in detail["fields"][0]["hint"]


def test_panel_spec_seed_skill_source_is_seed(tmp_path):
    _write_skill(tmp_path / "seed", "grill-me")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    detail = next(w for w in spec["widgets"] if w["type"] == "detail" and w["id"] == "skill-grill-me")
    assert "Source: seed" in detail["fields"][0]["hint"]


def test_panel_spec_does_not_dump_skill_body_inline(tmp_path):
    """The full SKILL.md body is no longer inlined per skill -- it made the tab
    unreadable across many skills. Only the name (line) + description (hover)
    show; the body stays available via the skill_preview tool."""
    _write_skill(tmp_path / "seed", "grill-me", body="Ask five hard questions.")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    detail = next(w for w in spec["widgets"] if w["type"] == "detail" and w["id"] == "skill-grill-me")
    field = detail["fields"][0]
    assert field["value"] == "grill-me"
    assert "A test skill." in field["hint"]              # description on hover
    assert "Ask five hard questions." not in field["hint"]  # body not inlined


def test_panel_spec_carries_no_html_or_scripts(tmp_path):
    """The spec is data, not markup -- SAFETY #3 in UI2.md."""
    _write_skill(tmp_path / "seed", "safe-name", body="plain text body")
    plugin = _plugin(tmp_path, enabled=["safe-name"])
    spec = plugin.panel_spec(1)
    for s in _walk_strings(spec):
        low = s.lower()
        assert "<script" not in low
        assert "onerror=" not in low
        assert "javascript:" not in low


# ── set_broadcast_fn seam (mirrors documents.py S3 #454) ──────────────────────

async def test_broadcast_fn_called_after_successful_enable(tmp_path):
    _write_skill(tmp_path / "seed", "alpha")
    plugin = _plugin(tmp_path)
    calls = []

    async def fake_broadcast():
        calls.append(1)

    skills_mod.set_broadcast_fn(fake_broadcast)
    result = await plugin.call_tool("skill_enable", {"name": "alpha"})
    assert not result.is_error
    assert calls == [1]


async def test_broadcast_fn_not_called_on_error(tmp_path):
    plugin = _plugin(tmp_path)  # no skills at all
    calls = []

    async def fake_broadcast():
        calls.append(1)

    skills_mod.set_broadcast_fn(fake_broadcast)
    result = await plugin.call_tool("skill_enable", {"name": "ghost"})
    assert result.is_error
    assert calls == []


async def test_broadcast_fn_unwired_is_a_silent_no_op(tmp_path):
    _write_skill(tmp_path / "seed", "alpha")
    plugin = _plugin(tmp_path)  # set_broadcast_fn never called
    result = await plugin.call_tool("skill_enable", {"name": "alpha"})
    assert not result.is_error
