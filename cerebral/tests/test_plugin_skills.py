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

from cerebral.settings import SettingsStore
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


def _plugin(tmp_path: Path, enabled=None) -> SkillsPlugin:
    # A real (non-fallback) SettingsStore rooted at tmp_path -- the fallback
    # in SkillsPlugin._settings_obj() would otherwise touch this machine's
    # actual felix-settings.json (S2 #538 gate landed here without tests).
    settings = SettingsStore(path=tmp_path / "felix-settings.json")
    if enabled:
        settings.set("enabled_skills", list(enabled))
    return SkillsPlugin(
        seed_dir=tmp_path / "seed", installed_dir=tmp_path / "installed", settings=settings
    )


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
    plugin = _plugin(tmp_path, enabled=["alpha"])
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
    plugin = _plugin(tmp_path, enabled=["dup"])
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
    plugin = _plugin(tmp_path, enabled=["good"])
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
    plugin = _plugin(tmp_path, enabled=["beta"])
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


# ---------------------------------------------------------------------------
# lifecycle -- enabled_skills gate (S2 #538)
# ---------------------------------------------------------------------------

async def test_skills_disabled_by_default(tmp_path):
    _write_skill(tmp_path / "seed", "alpha")
    plugin = _plugin(tmp_path)  # no enabled= -> enabled_skills defaults to []
    result = await plugin.call_tool("skill_list", {})
    assert json.loads(result.content)["skills"] == []
    use = await plugin.call_tool("skill_use", {"name": "alpha"})
    assert use.is_error
    assert "disabled" in use.content


async def test_skill_enable_makes_skill_use_resolve(tmp_path):
    _write_skill(tmp_path / "seed", "alpha")
    plugin = _plugin(tmp_path)
    enable = await plugin.call_tool("skill_enable", {"name": "alpha"})
    assert not enable.is_error
    assert json.loads(enable.content) == {"name": "alpha", "enabled": True}

    listed = await plugin.call_tool("skill_list", {})
    assert {s["name"] for s in json.loads(listed.content)["skills"]} == {"alpha"}

    used = await plugin.call_tool("skill_use", {"name": "alpha"})
    assert not used.is_error
    assert json.loads(used.content)["name"] == "alpha"


async def test_skill_disable_hides_it_again(tmp_path):
    _write_skill(tmp_path / "seed", "alpha")
    plugin = _plugin(tmp_path, enabled=["alpha"])

    disable = await plugin.call_tool("skill_disable", {"name": "alpha"})
    assert not disable.is_error
    assert json.loads(disable.content) == {"name": "alpha", "enabled": False}

    listed = await plugin.call_tool("skill_list", {})
    assert json.loads(listed.content)["skills"] == []
    used = await plugin.call_tool("skill_use", {"name": "alpha"})
    assert used.is_error


async def test_skill_enable_unknown_name_errors(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_enable", {"name": "nope"})
    assert result.is_error


async def test_skill_catalog_lists_all_with_enabled_flag(tmp_path):
    _write_skill(tmp_path / "seed", "alpha")
    _write_skill(tmp_path / "seed", "beta")
    plugin = _plugin(tmp_path, enabled=["alpha"])
    result = await plugin.call_tool("skill_catalog", {})
    by_name = {s["name"]: s["enabled"] for s in json.loads(result.content)["skills"]}
    assert by_name == {"alpha": True, "beta": False}


async def test_skill_uninstall_removes_installed_dir_and_enabled_entry(tmp_path):
    installed_dir = _write_skill(tmp_path / "installed", "gamma")
    plugin = _plugin(tmp_path, enabled=["gamma"])

    result = await plugin.call_tool("skill_uninstall", {"name": "gamma"})
    assert not result.is_error
    assert json.loads(result.content) == {"name": "gamma", "uninstalled": True}
    assert not installed_dir.exists()

    catalog = await plugin.call_tool("skill_catalog", {})
    assert json.loads(catalog.content)["skills"] == []  # gone from discovery entirely

    # enabled_skills no longer references the removed skill
    settings = SettingsStore(path=tmp_path / "felix-settings.json")
    assert "gamma" not in settings.get("enabled_skills")


async def test_skill_uninstall_on_seed_skill_refused(tmp_path):
    seed_dir = _write_skill(tmp_path / "seed", "alpha")
    plugin = _plugin(tmp_path, enabled=["alpha"])

    result = await plugin.call_tool("skill_uninstall", {"name": "alpha"})
    assert result.is_error
    assert "disable" in result.content.lower()
    assert seed_dir.exists()  # not deleted

    # still enabled and still usable -- uninstall was a pure refusal
    used = await plugin.call_tool("skill_use", {"name": "alpha"})
    assert not used.is_error


async def test_skill_uninstall_unknown_name_errors(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_uninstall", {"name": "nope"})
    assert result.is_error


# ---------------------------------------------------------------------------
# skill_install (S3 #541) -- hermetic, injected fetch_fn (no network)
# ---------------------------------------------------------------------------
import io
import tarfile


def _skill_md(name, description="Installed skill.", body="Body."):
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def _tarball(top, skills):
    """Build a gzip tarball laid out as <top>/<dirname>/SKILL.md, like GitHub's."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for dirname, md in skills.items():
            data = md.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{dirname}/SKILL.md")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _install_plugin(tmp_path, tarball):
    settings = SettingsStore(path=tmp_path / "felix-settings.json")
    return SkillsPlugin(
        seed_dir=tmp_path / "seed",
        installed_dir=tmp_path / "installed",
        settings=settings,
        fetch_fn=lambda repo, ref: tarball,
    )


async def test_skill_install_writes_dir_and_provenance(tmp_path):
    plugin = _install_plugin(tmp_path, _tarball("myrepo-main", {"alpha": _skill_md("alpha")}))
    result = await plugin.call_tool("skill_install", {"repo": "owner/myrepo"})
    assert not result.is_error, result.content
    assert json.loads(result.content) == {
        "name": "alpha", "installed": True, "enabled": False, "source": "owner/myrepo",
    }
    inst = tmp_path / "installed" / "alpha"
    assert (inst / "SKILL.md").is_file()
    prov = json.loads((inst / ".provenance.json").read_text(encoding="utf-8"))
    assert prov["repo"] == "owner/myrepo"
    assert prov["sha"] == "main"
    assert prov["installed_at"]


async def test_skill_install_lands_disabled(tmp_path):
    plugin = _install_plugin(tmp_path, _tarball("myrepo-main", {"alpha": _skill_md("alpha")}))
    await plugin.call_tool("skill_install", {"repo": "owner/myrepo"})
    use = await plugin.call_tool("skill_use", {"name": "alpha"})
    assert use.is_error and "disabled" in use.content
    cat = json.loads((await plugin.call_tool("skill_catalog", {})).content)["skills"]
    alpha = next(s for s in cat if s["name"] == "alpha")
    assert alpha["enabled"] is False and alpha["source"] == "installed"
    assert json.loads((await plugin.call_tool("skill_list", {})).content)["skills"] == []


async def test_skill_install_multi_returns_picklist(tmp_path):
    tb = _tarball("r-main", {"alpha": _skill_md("alpha"), "beta": _skill_md("beta")})
    plugin = _install_plugin(tmp_path, tb)
    data = json.loads((await plugin.call_tool("skill_install", {"repo": "owner/r"})).content)
    assert data["multiple"] is True
    assert data["skills"] == ["alpha", "beta"]
    assert not (tmp_path / "installed" / "alpha").exists()


async def test_skill_install_pick_one(tmp_path):
    tb = _tarball("r-main", {"alpha": _skill_md("alpha"), "beta": _skill_md("beta")})
    plugin = _install_plugin(tmp_path, tb)
    result = await plugin.call_tool("skill_install", {"repo": "owner/r", "name": "beta"})
    assert not result.is_error, result.content
    assert (tmp_path / "installed" / "beta" / "SKILL.md").is_file()
    assert not (tmp_path / "installed" / "alpha").exists()


async def test_skill_install_refuses_overwrite(tmp_path):
    tb = _tarball("r-main", {"alpha": _skill_md("alpha")})
    plugin = _install_plugin(tmp_path, tb)
    await plugin.call_tool("skill_install", {"repo": "owner/r"})
    again = await plugin.call_tool("skill_install", {"repo": "owner/r"})
    assert again.is_error and "already installed" in again.content


async def test_skill_install_bad_repo_errors(tmp_path):
    plugin = _install_plugin(tmp_path, b"")
    result = await plugin.call_tool("skill_install", {"repo": "not-a-repo"})
    assert result.is_error and "owner/repo" in result.content


async def test_skill_install_subpath(tmp_path):
    tb = _tarball("r-main", {"pkg/alpha": _skill_md("alpha")})
    plugin = _install_plugin(tmp_path, tb)
    result = await plugin.call_tool("skill_install", {"repo": "owner/r", "subpath": "pkg"})
    assert not result.is_error, result.content
    assert (tmp_path / "installed" / "alpha" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# skill_preview (S5 follow-up) -- ungated review read
# ---------------------------------------------------------------------------

async def test_skill_preview_returns_disabled_skill_body(tmp_path):
    _write_skill(tmp_path / "seed", "grill-me", body="Ask five hard questions.")
    plugin = _plugin(tmp_path)  # not enabled
    result = await plugin.call_tool("skill_preview", {"name": "grill-me"})
    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["enabled"] is False
    assert "Ask five hard questions." in data["instructions"]


async def test_skill_preview_unknown_errors(tmp_path):
    plugin = _plugin(tmp_path)
    result = await plugin.call_tool("skill_preview", {"name": "nope"})
    assert result.is_error and "nope" in result.content
