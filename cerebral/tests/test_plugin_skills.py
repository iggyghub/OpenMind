"""
Skills plugin tests -- S1 (Issue #537, ADR-0014).

Covers: SKILL.md front-matter parsing (happy + malformed), two-root discovery
with installed-shadows-seed, skill_list shape, and skill_use body + resource
manifest + unknown-name handling.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.skills import Skill, SkillsPlugin, _load_skill_dir, _split_frontmatter


def _write_skill(root: Path, name: str, *, description="A test skill.",
                 kind=None, tools=None, body="Do the thing.", resources=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"name: {name}", f"description: {description}"]
    if kind is not None:
        fm.append(f"kind: {kind}")
    if tools is not None:
        fm.append("tools: [" + ", ".join(tools) + "]")
    text = "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n"
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    for rel, content in (resources or {}).items():
        rp = d / rel
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(content, encoding="utf-8")
    return d


def _plugin(tmp_path: Path) -> SkillsPlugin:
    return SkillsPlugin(seed_dir=tmp_path / "seed", installed_dir=tmp_path / "installed")


# ---------------------------------------------------------------------------
# front-matter parsing
# ---------------------------------------------------------------------------

def test_split_frontmatter_happy():
    meta, body = _split_frontmatter("---\nname: x\ndescription: y\n---\n\nHello body\n")
    assert meta == {"name": "x", "description": "y"}
    assert body == "Hello body"


def test_split_frontmatter_no_fence_raises():
    import pytest
    with pytest.raises(ValueError):
        _split_frontmatter("no front-matter here")


def test_split_frontmatter_unterminated_raises():
    import pytest
    with pytest.raises(ValueError):
        _split_frontmatter("---\nname: x\nstill going\n")


def test_load_skill_dir_defaults_kind_and_tools(tmp_path):
    d = _write_skill(tmp_path, "alpha")  # no kind, no tools
    skill = _load_skill_dir(d, "seed")
    assert isinstance(skill, Skill)
    assert skill.kind == "procedure"
    assert skill.tools == ()


def test_load_skill_dir_missing_description_skipped(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: bad\n---\n\nbody\n", encoding="utf-8")
    assert _load_skill_dir(d, "seed") is None


def test_load_skill_dir_no_skill_md_returns_none(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert _load_skill_dir(d, "seed") is None


# ---------------------------------------------------------------------------
# discovery + skill_list
# ---------------------------------------------------------------------------

async def test_skill_list_returns_discovered(tmp_path):
    _write_skill(tmp_path / "seed", "alpha", tools=["git", "github"])
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_list", {})
    assert not result.is_error
    names = {s["name"] for s in json.loads(result.content)["skills"]}
    assert names == {"alpha"}
    alpha = json.loads(result.content)["skills"][0]
    assert alpha["tools"] == ["git", "github"]
    assert alpha["source"] == "seed"


async def test_installed_shadows_seed(tmp_path):
    _write_skill(tmp_path / "seed", "dup", description="seed version")
    _write_skill(tmp_path / "installed", "dup", description="installed version")
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_list", {})
    skills = json.loads(result.content)["skills"]
    assert len(skills) == 1
    assert skills[0]["source"] == "installed"
    assert skills[0]["description"] == "installed version"


async def test_malformed_skill_skipped_not_crash(tmp_path):
    _write_skill(tmp_path / "seed", "good")
    bad = tmp_path / "seed" / "broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("not front matter at all", encoding="utf-8")
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_list", {})
    names = {s["name"] for s in json.loads(result.content)["skills"]}
    assert names == {"good"}


# ---------------------------------------------------------------------------
# skill_use
# ---------------------------------------------------------------------------

async def test_skill_use_returns_body_and_resources(tmp_path):
    _write_skill(
        tmp_path / "seed", "beta", body="Follow these steps.",
        resources={"template.txt": "hi", "data/ref.md": "x"},
    )
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_use", {"name": "beta"})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["instructions"] == "Follow these steps."
    assert sorted(data["resources"]) == ["data/ref.md", "template.txt"]


async def test_skill_use_unknown_name_errors(tmp_path):
    _write_skill(tmp_path / "seed", "beta")
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_use", {"name": "nope"})
    assert result.is_error
    assert "nope" in result.content


async def test_skill_use_requires_name(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_use", {})
    assert result.is_error


async def test_unknown_tool_errors(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("nope", {})
    assert result.is_error
