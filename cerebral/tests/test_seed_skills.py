"""
Seed skills smoke test -- S6 (Issue #540, ADR-0014).

Loads every skill shipped under <repo>/skills/ (the seed root) through the
real SkillsPlugin and asserts each parses cleanly, is discoverable via
skill_list, and returns a coherent (non-empty instructions + description)
procedure via skill_use. Catches front-matter typos in the seed skills
themselves, not the plugin's parsing logic (already covered by
test_plugin_skills.py).
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.skills import SkillsPlugin

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SEED_DIR = _REPO_ROOT / "skills"

SEED_SKILL_NAMES = {"caveman", "grill-me", "tdd", "diagnose", "to-issues", "plugin-scaffold"}


def _plugin(tmp_path: Path) -> SkillsPlugin:
    # installed_dir points at an empty tmp dir so only the real seed skills load.
    return SkillsPlugin(seed_dir=_SEED_DIR, installed_dir=tmp_path / "installed")


async def test_all_seed_skills_discovered_no_warnings(tmp_path, caplog):
    plugin = _plugin(tmp_path)
    with caplog.at_level(logging.WARNING, logger="plugins.skills"):
        result = await plugin.call_tool("skill_list", {})
    assert not result.is_error
    skills = json.loads(result.content)["skills"]
    names = {s["name"] for s in skills}
    assert SEED_SKILL_NAMES <= names
    assert not caplog.records, f"parse warnings: {[r.message for r in caplog.records]}"


async def test_each_seed_skill_has_description_and_kind(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_list", {})
    skills = {s["name"]: s for s in json.loads(result.content)["skills"]}
    for name in SEED_SKILL_NAMES:
        skill = skills[name]
        assert skill["description"].strip()
        assert skill["kind"] == "procedure"
        assert skill["source"] == "seed"


async def test_each_seed_skill_loads_nonempty_instructions(tmp_path):
    plugin = _plugin(tmp_path)
    for name in SEED_SKILL_NAMES:
        result = await plugin.call_tool("skill_use", {"name": name})
        assert not result.is_error, f"{name}: {result.content}"
        data = json.loads(result.content)
        assert data["instructions"].strip(), f"{name} has empty instructions"
