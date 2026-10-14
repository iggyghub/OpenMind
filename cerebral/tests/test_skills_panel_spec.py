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

VALID_WIDGETS = frozenset({"list", "detail", "text", "action", "toggle", "registry"})


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
    # Summary detail + install action + an empty registry, no skills.
    assert [w["type"] for w in widgets] == ["detail", "action", "registry"]
    for w in widgets:
        assert w["type"] in VALID_WIDGETS

    summary = widgets[0]
    assert "0 installed" in summary["fields"][0]["value"]

    install = widgets[1]
    assert install["tool"] == "skill_install"
    assert install["input_arg"] == "repo"

    assert widgets[2]["items"] == []


def _registry_items(spec):
    reg = next(w for w in spec["widgets"] if w["type"] == "registry")
    return {it["name"]: it for it in reg["items"]}


def test_panel_spec_lists_each_skill_as_one_registry_row(tmp_path):
    _write_skill(tmp_path / "seed", "grill-me", tools=["ask_user"])
    _write_skill(tmp_path / "installed", "custom", tools=[])
    plugin = _plugin(tmp_path, enabled=["grill-me"])

    spec = plugin.panel_spec(1)
    widgets = spec["widgets"]
    types = [w["type"] for w in widgets]
    assert types == ["detail", "action", "registry"]

    items = _registry_items(spec)
    assert set(items) == {"grill-me", "custom"}

    # One compact row per skill: the name is the value; description + source
    # live in the hover hint (renderer -> native title tooltip).
    custom = items["custom"]
    assert custom["status"] == "disabled"
    assert "A test skill." in custom["hint"]
    assert "Source: installed" in custom["hint"]  # no provenance sidecar written

    # Enable/disable is one relabeled action whose tool matches current state.
    custom_toggle = custom["actions"][0]
    assert custom_toggle["label"] == "Enable"  # currently disabled
    assert custom_toggle["tool"] == "skill_enable"
    assert custom_toggle["tool_args"] == {"name": "custom"}

    # Installed (non-seed) skills also get Update (ADR-0035 slice K), then
    # Uninstall.
    custom_update = custom["actions"][1]
    assert custom_update["tool"] == "skill_update"
    assert custom_update["tool_args"] == {"name": "custom"}

    custom_uninstall = custom["actions"][2]
    assert custom_uninstall["tool"] == "skill_uninstall"
    assert custom_uninstall["tool_args"] == {"name": "custom"}

    # grill-me is enabled -> its action offers Disable, and (seed) has no uninstall.
    grill = items["grill-me"]
    assert grill["status"] == "enabled"
    assert len(grill["actions"]) == 1
    assert grill["actions"][0]["label"] == "Disable"
    assert grill["actions"][0]["tool"] == "skill_disable"


def test_panel_spec_seed_skill_has_no_uninstall_action(tmp_path):
    _write_skill(tmp_path / "seed", "grill-me")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    grill = _registry_items(spec)["grill-me"]
    tools = [a["tool"] for a in grill["actions"]]
    assert "skill_uninstall" not in tools


def test_panel_spec_installed_skill_has_uninstall_action(tmp_path):
    _write_skill(tmp_path / "installed", "custom")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    custom = _registry_items(spec)["custom"]
    uninstalls = [a for a in custom["actions"] if a["tool"] == "skill_uninstall"]
    assert len(uninstalls) == 1
    assert uninstalls[0]["tool_args"] == {"name": "custom"}


def test_panel_spec_provenance_from_sidecar(tmp_path):
    d = _write_skill(tmp_path / "installed", "custom")
    (d / ".provenance.json").write_text(
        json.dumps({"repo": "owner/repo", "sha": "abc1234def"}), encoding="utf-8"
    )
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    custom = _registry_items(spec)["custom"]
    assert "Source: owner/repo@abc1234" in custom["hint"]


def test_panel_spec_seed_skill_source_is_seed(tmp_path):
    _write_skill(tmp_path / "seed", "grill-me")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    grill = _registry_items(spec)["grill-me"]
    assert "Source: seed" in grill["hint"]


def test_panel_spec_does_not_dump_skill_body_inline(tmp_path):
    """The full SKILL.md body is no longer inlined per skill -- it made the tab
    unreadable across many skills. Only the name (row) + description (hover)
    show; the body stays available via the skill_preview tool."""
    _write_skill(tmp_path / "seed", "grill-me", body="Ask five hard questions.")
    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    grill = _registry_items(spec)["grill-me"]
    assert grill["name"] == "grill-me"
    assert "A test skill." in grill["hint"]              # description on hover
    assert "Ask five hard questions." not in grill["hint"]  # body not inlined


def test_panel_spec_registry_item_carries_verify_result(tmp_path):
    """ADR-0034/ADR-0035 integration: each skill row's verify badge reflects
    whether verified_evidence is set in its SKILL.md frontmatter."""
    _write_skill(tmp_path / "seed", "unverified-skill")
    d = _write_skill(tmp_path / "seed", "verified-skill")
    text = (d / "SKILL.md").read_text(encoding="utf-8")
    text = text.replace(
        "description: A test skill.",
        "description: A test skill.\nverified_evidence: Confirmed working on task X",
    )
    (d / "SKILL.md").write_text(text, encoding="utf-8")

    plugin = _plugin(tmp_path)
    spec = plugin.panel_spec(1)
    items = _registry_items(spec)

    assert items["unverified-skill"]["verify"]["passed"] is False
    assert items["verified-skill"]["verify"]["passed"] is True
    assert items["verified-skill"]["verify"]["evidence"] == "Confirmed working on task X"


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
