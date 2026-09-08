"""
Skills plugin -- S1/S2/S3/S5/Slice K.
Handles discovery, enable/disable, install, preview, and update of Skills.
"""
import json
import tarfile
import io
import time
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from types import SimpleNamespace


@dataclass
class Skill:
    name: str
    description: str
    kind: str = "procedure"
    tools: tuple = ()
    body: str = ""
    resources: Dict[str, str] = field(default_factory=dict)
    source: str = ""
    enabled: bool = False


def _split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    if not text.startswith("---"):
        raise ValueError("Missing front-matter fence")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("Unterminated front-matter")
    fm = text[4:end]
    meta = {}
    for line in fm.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    body = text[end + 4:].strip()
    return meta, body


def _load_skill_dir(d: Path, source: str) -> Optional[Skill]:
    md_path = d / "SKILL.md"
    if not md_path.is_file():
        return None
    try:
        meta, body = _split_frontmatter(md_path.read_text(encoding="utf-8"))
    except ValueError:
        return None

    if "description" not in meta:
        return None

    resources = {}
    for p in d.rglob("*"):
        if p.is_file() and p != md_path and not p.name.startswith("."):
            rel = p.relative_to(d)
            try:
                resources[str(rel)] = p.read_text(encoding="utf-8")
            except Exception:
                pass

    tools_raw = meta.get("tools", "[]")
    try:
        tools = json.loads(tools_raw) if isinstance(tools_raw, str) else tools_raw
    except json.JSONDecodeError:
        tools = []

    return Skill(
        name=meta.get("name", d.name),
        description=meta["description"],
        kind=meta.get("kind", "procedure"),
        tools=tuple(tools) if tools else (),
        body=body,
        resources=resources,
        source=source,
        enabled=False,
    )


class SkillsPlugin:
    def __init__(self, seed_dir: Path, installed_dir: Path, settings=None, fetch_fn=None):
        self.seed_dir = Path(seed_dir)
        self.installed_dir = Path(installed_dir)
        self.settings = settings
        self.fetch_fn = fetch_fn

    def _ok(self, data: Any) -> SimpleNamespace:
        return SimpleNamespace(is_error=False, content=json.dumps(data))

    def _err(self, msg: str) -> SimpleNamespace:
        return SimpleNamespace(is_error=True, content=msg)

    async def call_tool(self, tool_name: str, tool_input: Dict[str, Any]):
        if tool_name == "skill_list":
            return await self._skill_list(tool_input)
        elif tool_name == "skill_use":
            return await self._skill_use(tool_input)
        elif tool_name == "skill_enable":
            return await self._skill_enable(tool_input)
        elif tool_name == "skill_disable":
            return await self._skill_disable(tool_input)
        elif tool_name == "skill_catalog":
            return await self._skill_catalog(tool_input)
        elif tool_name == "skill_uninstall":
            return await self._skill_uninstall(tool_input)
        elif tool_name == "skill_install":
            return await self._skill_install(tool_input)
        elif tool_name == "skill_preview":
            return await self._skill_preview(tool_input)
        elif tool_name == "skill_update":
            return await self._skill_update(tool_input)
        return self._err(f"Unknown tool: {tool_name}")

    def _get_enabled_skills(self) -> List[str]:
        if not self.settings:
            return []
        return self.settings.get("enabled_skills") or []

    def _update_enabled(self, name: str, enable: bool):
        if not self.settings:
            return
        curr = self._get_enabled_skills()
        if enable and name not in curr:
            curr.append(name)
        elif not enable and name in curr:
            curr.remove(name)
        self.settings.set("enabled_skills", curr)

    def _find_skill(self, name: str) -> Optional[Skill]:
        for d in self.installed_dir.iterdir():
            if d.is_dir() and (d / "SKILL.md").is_file():
                s = _load_skill_dir(d, "installed")
                if s and s.name == name:
                    return s
        for d in self.seed_dir.iterdir():
            if d.is_dir() and (d / "SKILL.md").is_file():
                s = _load_skill_dir(d, "seed")
                if s and s.name == name:
                    return s
        return None

    def _skill_dict(self, s: Skill) -> Dict:
        return {
            "name": s.name,
            "description": s.description,
            "kind": s.kind,
            "tools": list(s.tools),
            "source": s.source,
            "enabled": s.enabled,
            "actions": [{"type": "update", "available": self._check_update_available(s)}],
        }

    def _check_update_available(self, s: Skill) -> bool:
        if s.source != "installed":
            return False
        prov = self._load_provenance(s.name)
        return self.fetch_fn is not None and prov is not None

    async def _skill_list(self, inp):
        all_skills = {}
        for d in self.seed_dir.iterdir():
            if d.is_dir():
                s = _load_skill_dir(d, "seed")
                if s:
                    all_skills[s.name] = s
        for d in self.installed_dir.iterdir():
            if d.is_dir():
                s = _load_skill_dir(d, "installed")
                if s:
                    all_skills[s.name] = s  # installed shadows seed

        enabled = self._get_enabled_skills()
        result = []
        for name, s in all_skills.items():
            if name in enabled:
                s.enabled = True
                result.append(self._skill_dict(s))
        return self._ok({"skills": result})

    async def _skill_use(self, inp):
        name = inp.get("name")
        if not name:
            return self._err("Missing name")
        enabled = self._get_enabled_skills()
        if name not in enabled:
            return self._err(f"Skill '{name}' is disabled")
        s = self._find_skill(name)
        if not s:
            return self._err(f"Unknown skill: {name}")
        return self._ok(
            {"name": name, "instructions": s.body, "resources": list(s.resources.keys())}
        )

    async def _skill_enable(self, inp):
        name = inp.get("name")
        if not name:
            return self._err("Missing name")
        if not self._find_skill(name):
            return self._err(f"Unknown skill: {name}")
        self._update_enabled(name, True)
        return self._ok({"name": name, "enabled": True})

    async def _skill_disable(self, inp):
        name = inp.get("name")
        if not name:
            return self._err("Missing name")
        if not self._find_skill(name):
            return self._err(f"Unknown skill: {name}")
        self._update_enabled(name, False)
        return self._ok({"name": name, "enabled": False})

    async def _skill_catalog(self, inp):
        all_skills = {}
        for d in self.seed_dir.iterdir():
            if d.is_dir():
                s = _load_skill_dir(d, "seed")
                if s:
                    all_skills[s.name] = s
        for d in self.installed_dir.iterdir():
            if d.is_dir():
                s = _load_skill_dir(d, "installed")
                if s:
                    all_skills[s.name] = s
        enabled = self._get_enabled_skills()
        result = []
        for name, s in all_skills.items():
            s.enabled = name in enabled
            result.append(self._skill_dict(s))
        return self._ok({"skills": result})

    async def _skill_uninstall(self, inp):
        name = inp.get("name")
        if not name:
            return self._err("Missing name")
        s = self._find_skill(name)
        if not s:
            return self._err(f"Unknown skill: {name}")
        if s.source == "seed":
            return self._err("Cannot uninstall seed skills. Disable it instead.")
        d = self.installed_dir / name
        if d.is_dir():
            shutil.rmtree(d)
        self._update_enabled(name, False)
        return self._ok({"name": name, "uninstalled": True})

    async def _skill_install(self, inp):
        repo = inp.get("repo")
        name = inp.get("name")
        if not self.fetch_fn:
            return self._err("Fetch function not configured")
        if not repo:
            return self._err("Missing repo")

        tb = self.fetch_fn(repo, "main")
        if not tb:
            return self._err(f"Failed to fetch {repo}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tpath = Path(tmpdir)
            with tarfile.open(fileobj=io.BytesIO(tb), mode="r:gz") as tf:
                tf.extractall(tpath)

            target_dir = None
            extracted_content = ""
            for d in tpath.rglob("*"):
                if d.is_dir():
                    s = _load_skill_dir(d, "installed")
                    if s and (name is None or s.name == name):
                        target_dir = d
                        md_path = d / "SKILL.md"
                        if md_path.is_file():
                            extracted_content = md_path.read_text(encoding="utf-8")
                        break

            if not target_dir:
                return self._err("No skills found in archive")

            target_name = target_dir.name
            multi_count = sum(1 for d in tpath.rglob("SKILL.md") if d.is_file())
            if multi_count > 1 and not name:
                return self._ok(
                    {
                        "multiple": True,
                        "skills": sorted([d.parent.name for d in tpath.rglob("SKILL.md")]),
                    }
                )

            if (self.installed_dir / target_name).is_dir():
                return self._err(f"Skill '{target_name}' is already installed")

            dest = self.installed_dir / target_name
            shutil.copytree(target_dir, dest)

            prov = {
                "repo": repo,
                "sha": "main",
                "installed_at": int(time.time()),
                "original_content": extracted_content,
            }
            (dest / ".provenance.json").write_text(json.dumps(prov), encoding="utf-8")

            return self._ok({"name": target_name, "installed": True, "enabled": False, "source": repo})

    async def _skill_preview(self, inp):
        name = inp.get("name")
        if not name:
            return self._err("Missing name")
        s = self._find_skill(name)
        if not s:
            return self._err(f"Unknown skill: {name}")
        return self._ok({"name": name, "instructions": s.body, "enabled": s.enabled})

    async def _skill_update(self, inp):
        name = inp.get("name")
        if not name:
            return self._err("Missing name")
        s = self._find_skill(name)
        if not s:
            return self._err(f"Unknown skill: {name}")
        if s.source != "installed":
            return self._err("Can only update installed skills")

        prov = self._load_provenance(name)
        if not prov:
            return self._err("No provenance found, cannot update")

        installed_md = self.installed_dir / name / "SKILL.md"
        if not installed_md.is_file():
            return self._err("Skill file missing")

        current_content = installed_md.read_text(encoding="utf-8")
        original_content = prov.get("original_content", "")

        if current_content != original_content:
            return self._err("this Skill has local edits, update manually")

        if not self.fetch_fn:
            return self._err("No fetch function configured")

        tb = self.fetch_fn(prov["repo"], "HEAD")
        if not tb:
            return self._err("Failed to fetch latest version")

        new_content = ""
        with tarfile.open(fileobj=io.BytesIO(tb), mode="r:gz") as tf:
            for m in tf.getmembers():
                if m.name.endswith(f"/{name}/SKILL.md") or m.name.endswith(f"\\{name}\\SKILL.md"):
                    if m.size > 0:
                        new_content = tf.extractfile(m).read().decode("utf-8")
                        break

        if not new_content:
            return self._err("New version missing skill file")

        installed_md.write_text(new_content, encoding="utf-8")
        prov["sha"] = "HEAD"
        prov["original_content"] = new_content
        (self.installed_dir / name / ".provenance.json").write_text(
            json.dumps(prov), encoding="utf-8"
        )

        return self._ok({"name": name, "updated": True})

    def _load_provenance(self, name: str) -> Optional[Dict]:
        p = self.installed_dir / name / ".provenance.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return None
