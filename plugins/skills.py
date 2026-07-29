"""
Skills plugin -- the Skills subsystem foundation (S1, Issue #537, ADR-0014).

A **Skill** is an installable package of instructions (a procedure), not code and
not a frozen chain, that Felix loads into the planner's context to change how it
approaches a class of task. See CONTEXT.md "Skill" and ADR-0014.

This plugin ships the subsystem the same way the growth loop ships as
`plugins/builder.py`. In this slice it is read-only:

  - `skill_list`      -> {name, description, kind, tools, source} for every skill
  - `skill_use(name)` -> the skill's full instruction body + a manifest of its
                          bundled resource files

Skills are discovered from **two roots**:
  - seed:      <repo>/skills/<name>/SKILL.md      (version-controlled, ships with Felix)
  - installed: <data_dir>/skills/<name>/SKILL.md  (gitignored, added at runtime)
The installed root shadows the seed root on a name collision.

Each skill is a directory containing a `SKILL.md` whose YAML front-matter carries
`name`, `description`, `kind` (default `procedure`), and `tools` (list). Malformed
or incomplete front-matter is skipped with a logged warning, never a crash.

Enable-state (S2 #538): skills are **disabled by default**. A skill is visible to
the planner (`skill_list` / `skill_use`) only if its name is in the opt-in
`enabled_skills` list in `felix-settings.json` (the inverse of `disabled_plugins`).
Lifecycle tools: `skill_enable`, `skill_disable`, `skill_uninstall`, plus
`skill_catalog` (management view of every discovered skill + its enabled flag).

Install-from-GitHub (S3 #541): `skill_install(repo[, subpath, ref, name])` fetches
a public GitHub repo tarball into the installed root (disabled on arrival) and
records provenance (repo + sha) in a `.provenance.json` sidecar.
"""
# NOTE: deliberately NO `from __future__ import annotations`. This module is
# loaded by the orchestrator via spec_from_file_location, which does NOT place
# it in sys.modules (a deliberate #153 choice). Under stringized annotations,
# dataclasses' ClassVar check resolves field types by looking the module up in
# sys.modules -- which fails here (module absent) and refuses the plugin at
# load. Real annotation objects avoid that lookup. See test_orchestrator.py
# ::test_every_real_plugin_declares_valid_required_capabilities.

import io
import json
import logging
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "skills"

# ADR-0005 / Issue #44 -- skill_list / skill_use / skill_catalog read SKILL.md
# files (fs_read); skill_enable / skill_disable write enabled_skills into
# felix-settings.json (fs_write); skill_uninstall removes an installed skill's
# directory (fs_delete); skill_install (#541) fetches a public GitHub tarball
# (network_egress_cloud) and writes it under the installed root (fs_write).
# No new capability class -- the ADR-0005 vocabulary is closed and a skill adds
# no capability of its own (ADR-0014).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset(
    {"fs_read", "fs_write", "fs_delete", "network_egress_cloud"}
)

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _default_fetch(repo: str, ref: str | None) -> bytes:
    """Download a public GitHub repo tarball. Injected out in tests.

    Uses the GitHub tarball endpoint, which redirects to the (default-branch or
    ref) codeload tar.gz. Public repos need no auth.
    """
    owner, name = repo.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{name}/tarball"
    if ref:
        url = f"{url}/{ref}"
    resp = httpx.get(
        url,
        follow_redirects=True,
        timeout=30.0,
        headers={"Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    return resp.content

# Settings-store seam (Issue #153 pattern): cerebral.main wires the singleton
# SettingsStore in via _wire_plugin_seams so the plugin reads/writes the SAME
# enabled_skills the rest of Cerebral sees. Until wired (tests, bare process),
# the plugin falls back to a default-path SettingsStore.
_settings_store = None


def set_settings_store(store) -> None:
    """Inject the SettingsStore singleton from cerebral.main."""
    global _settings_store
    _settings_store = store

# <repo>/plugins/skills.py -> parent is plugins/, parent.parent is the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_FILE = "SKILL.md"
_DEFAULT_KIND = "procedure"


@dataclass(frozen=True)
class Skill:
    """One discovered skill and where it came from."""

    name: str
    description: str
    kind: str
    tools: tuple[str, ...]
    source: str  # "seed" | "installed"
    path: Path   # the skill's directory


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (front-matter mapping, body).

    Standard YAML front-matter: the file opens with a ``---`` line, YAML runs
    until the next ``---`` line, and the body follows. Raises ``ValueError`` on
    anything that is not well-formed front-matter so the caller can skip + warn.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening '---' front-matter fence")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = yaml.safe_load("\n".join(lines[1:i])) or {}
            if not isinstance(meta, dict):
                raise ValueError("front-matter is not a YAML mapping")
            body = "\n".join(lines[i + 1:]).lstrip("\n")
            return meta, body
    raise ValueError("unterminated front-matter (no closing '---')")


def _load_skill_dir(dir_path: Path, source: str) -> Skill | None:
    """Load one skill directory, or return None (with a warning) if invalid."""
    md = dir_path / _SKILL_FILE
    if not md.is_file():
        return None
    try:
        meta, _body = _split_frontmatter(md.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        logger.warning("[skills] Skipping %s -- %s", md, exc)
        return None

    name = meta.get("name")
    description = meta.get("description")
    if not name or not description:
        logger.warning(
            "[skills] Skipping %s -- front-matter needs both 'name' and 'description'",
            md,
        )
        return None

    kind = str(meta.get("kind") or _DEFAULT_KIND)
    raw_tools = meta.get("tools") or []
    if not isinstance(raw_tools, list):
        logger.warning("[skills] %s -- 'tools' must be a list; treating as empty", md)
        raw_tools = []
    tools = tuple(str(t) for t in raw_tools)

    return Skill(
        name=str(name),
        description=str(description),
        kind=kind,
        tools=tools,
        source=source,
        path=dir_path,
    )


class SkillsPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        seed_dir: Path | None = None,
        installed_dir: Path | None = None,
        settings=None,
        fetch_fn=None,
    ) -> None:
        self._seed_dir = Path(seed_dir) if seed_dir else _REPO_ROOT / "skills"
        self._installed_dir = (
            Path(installed_dir) if installed_dir else data_dir() / "skills"
        )
        self._settings = settings
        # fetch_fn(repo, ref) -> tarball bytes. Default hits GitHub; tests inject.
        self._fetch = fetch_fn or _default_fetch
        # S5 #542 -- optional async no-arg callback; cerebral.main wires it to
        # re-broadcast the Skills panel_spec after a mutation (mirrors the
        # documents plugin's set_broadcast_fn, S3 #454).
        self._broadcast_fn = None

    def set_broadcast_fn(self, fn) -> None:
        """Inject the broadcast callback from cerebral.main (S5 #542)."""
        self._broadcast_fn = fn

    async def _maybe_broadcast(self) -> None:
        if self._broadcast_fn is not None:
            await self._broadcast_fn()

    # ------------------------------------------------------------------
    # Enable-state (S2 #538) -- opt-in enabled_skills in felix-settings.json
    # ------------------------------------------------------------------

    def _settings_obj(self):
        if self._settings is not None:
            return self._settings
        if _settings_store is not None:
            return _settings_store
        # Fallback: a default-path store. Self-coherent for a bare process;
        # production wires the singleton via set_settings_store.
        from cerebral.settings import SettingsStore

        self._settings = SettingsStore()
        return self._settings

    def _enabled_names(self) -> set[str]:
        return set(self._settings_obj().get("enabled_skills") or [])

    def _set_enabled(self, names: set[str]) -> None:
        self._settings_obj().set("enabled_skills", sorted(names))

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self) -> dict[str, Skill]:
        """Map of name -> Skill across both roots; installed shadows seed.

        Scanned live on each call so a skill installed by a later slice shows
        up without a restart.
        """
        found: dict[str, Skill] = {}
        # Seed first, installed second, so the installed root wins on collision.
        for root, source in ((self._seed_dir, "seed"), (self._installed_dir, "installed")):
            if not root.is_dir():
                continue
            for sub in sorted(p for p in root.iterdir() if p.is_dir()):
                if sub.name.startswith("."):
                    continue
                skill = _load_skill_dir(sub, source)
                if skill is not None:
                    found[skill.name] = skill
        return found

    @staticmethod
    def _resource_manifest(skill: Skill) -> list[str]:
        """Relative paths of a skill's bundled files (everything but SKILL.md)."""
        manifest: list[str] = []
        for f in sorted(skill.path.rglob("*")):
            # Skip SKILL.md itself and dotfiles (e.g. the .provenance.json sidecar).
            if f.is_file() and f.name != _SKILL_FILE and not f.name.startswith("."):
                manifest.append(f.relative_to(skill.path).as_posix())
        return manifest

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        _name_schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The skill's name."}},
            "required": ["name"],
        }
        return [
            Tool(
                name="skill_list",
                description=(
                    "List ENABLED skills (procedures Felix can follow), each with its "
                    "name and description. Call this to see what skills are available "
                    "before using one. Disabled skills are not shown here."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="skill_use",
                description=(
                    "Load an enabled skill by name: returns its full instruction text "
                    "(and a manifest of any bundled resource files) so you can follow "
                    "the procedure. Use when a task matches a skill from skill_list, or "
                    "when the user asks for a named skill."
                ),
                plugin=PLUGIN_NAME,
                schema=_name_schema,
            ),
            Tool(
                name="skill_catalog",
                description=(
                    "Management view: list EVERY discovered skill (enabled or not) "
                    "with its enabled flag and source. Use to see what can be enabled "
                    "or uninstalled."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="skill_preview",
                description=(
                    "Preview a skill's full instruction text + metadata WITHOUT "
                    "enabling it. Use to review an installed-but-disabled skill before "
                    "deciding to enable it. Unlike skill_use, this works on disabled "
                    "skills (it is a read for review, not a load into the planner)."
                ),
                plugin=PLUGIN_NAME,
                schema=_name_schema,
            ),
            Tool(
                name="skill_enable",
                description="Enable a skill by name so the planner can use it.",
                plugin=PLUGIN_NAME,
                schema=_name_schema,
            ),
            Tool(
                name="skill_disable",
                description="Disable a skill by name (keeps it installed, hides it from the planner).",
                plugin=PLUGIN_NAME,
                schema=_name_schema,
            ),
            Tool(
                name="skill_uninstall",
                description=(
                    "Remove an installed skill's files and drop it from enabled_skills. "
                    "Seed skills that ship with Felix cannot be uninstalled -- disable them instead."
                ),
                plugin=PLUGIN_NAME,
                schema=_name_schema,
                irreversible=True,
            ),
            Tool(
                name="skill_install",
                description=(
                    "Install a skill from a PUBLIC GitHub repo (owner/repo). If the "
                    "repo holds several skills, call returns the list to choose from; "
                    "pass 'name' to install one. The skill lands DISABLED -- review it, "
                    "then skill_enable it."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "GitHub 'owner/repo'."},
                        "subpath": {
                            "type": "string",
                            "description": "Optional folder within the repo to look in.",
                        },
                        "ref": {
                            "type": "string",
                            "description": "Optional branch/tag/commit (default branch if omitted).",
                        },
                        "name": {
                            "type": "string",
                            "description": "Which skill to install when the repo has several.",
                        },
                    },
                    "required": ["repo"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        args = args or {}
        if tool_name == "skill_list":
            return self._skill_list()
        if tool_name == "skill_use":
            return self._skill_use(args)
        if tool_name == "skill_catalog":
            return self._skill_catalog()
        if tool_name == "skill_preview":
            return self._skill_preview(args)
        # S5 #542 -- the Skills panel re-fetches panel_spec after a mutation
        # so the enable toggle / uninstall / install reflect fresh state
        # without an optimistic UI (mirrors the Plugins panel S4 #472).
        if tool_name == "skill_enable":
            result = self._skill_enable(args)
            if not result.is_error:
                await self._maybe_broadcast()
            return result
        if tool_name == "skill_disable":
            result = self._skill_disable(args)
            if not result.is_error:
                await self._maybe_broadcast()
            return result
        if tool_name == "skill_uninstall":
            result = self._skill_uninstall(args)
            if not result.is_error:
                await self._maybe_broadcast()
            return result
        if tool_name == "skill_install":
            result = self._skill_install(args)
            if not result.is_error:
                await self._maybe_broadcast()
            return result
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    def _skill_list(self) -> ToolResult:
        enabled = self._enabled_names()
        payload = [
            {
                "name": s.name,
                "description": s.description,
                "kind": s.kind,
                "tools": list(s.tools),
                "source": s.source,
            }
            for s in sorted(self._discover().values(), key=lambda s: s.name)
            if s.name in enabled
        ]
        return ToolResult(content=json.dumps({"skills": payload}))

    def _skill_catalog(self) -> ToolResult:
        enabled = self._enabled_names()
        payload = [
            {
                "name": s.name,
                "description": s.description,
                "kind": s.kind,
                "tools": list(s.tools),
                "source": s.source,
                "enabled": s.name in enabled,
            }
            for s in sorted(self._discover().values(), key=lambda s: s.name)
        ]
        return ToolResult(content=json.dumps({"skills": payload}))

    def _skill_enable(self, args: dict) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        if name not in self._discover():
            return ToolResult(content=f"No skill named {name!r}", is_error=True)
        enabled = self._enabled_names()
        enabled.add(name)
        self._set_enabled(enabled)
        return ToolResult(content=json.dumps({"name": name, "enabled": True}))

    def _skill_disable(self, args: dict) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        enabled = self._enabled_names()
        enabled.discard(name)
        self._set_enabled(enabled)
        return ToolResult(content=json.dumps({"name": name, "enabled": False}))

    def _skill_uninstall(self, args: dict) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        skill = self._discover().get(name)
        if skill is None:
            return ToolResult(content=f"No skill named {name!r}", is_error=True)
        if skill.source == "seed":
            return ToolResult(
                content=(
                    f"Skill {name!r} is a built-in seed skill and cannot be "
                    "uninstalled -- disable it instead."
                ),
                is_error=True,
            )
        shutil.rmtree(skill.path)
        enabled = self._enabled_names()
        if name in enabled:
            enabled.discard(name)
            self._set_enabled(enabled)
        return ToolResult(content=json.dumps({"name": name, "uninstalled": True}))

    # ------------------------------------------------------------------
    # skill_install (S3 #541) -- fetch a skill from a public GitHub repo
    # ------------------------------------------------------------------

    def _skill_install(self, args: dict) -> ToolResult:
        repo = str(args.get("repo", "")).strip()
        subpath = str(args.get("subpath") or "").strip().strip("/")
        ref = args.get("ref")
        ref = str(ref).strip() if ref else None
        pick = str(args.get("name") or "").strip()

        if not _REPO_RE.match(repo):
            return ToolResult(
                content=f"repo must be 'owner/repo', got {repo!r}", is_error=True
            )

        try:
            tarball = self._fetch(repo, ref)
        except Exception as exc:
            return ToolResult(content=f"Fetch failed for {repo!r}: {exc}", is_error=True)

        with tempfile.TemporaryDirectory(prefix="skill_install_") as td:
            tmp = Path(td)
            try:
                with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
                    names = tf.getnames()
                    # filter="data" (3.12+) blocks path traversal / device files.
                    tf.extractall(tmp, filter="data")
            except (tarfile.TarError, OSError, ValueError) as exc:
                return ToolResult(content=f"Bad archive for {repo!r}: {exc}", is_error=True)

            tops = {n.split("/", 1)[0] for n in names if n}
            if len(tops) != 1:
                return ToolResult(
                    content=f"Unexpected archive layout for {repo!r}", is_error=True
                )
            top = next(iter(tops))
            base = tmp / top
            if subpath:
                base = base / subpath
            if not base.is_dir():
                return ToolResult(
                    content=f"Path {subpath!r} not found in {repo!r}", is_error=True
                )

            # GitHub tarball top dir is "<reponame>-<ref-or-sha>".
            reponame = repo.split("/", 1)[1]
            sha = top[len(reponame) + 1:] if top.startswith(reponame + "-") else top

            # A skill is any dir with a SKILL.md; base itself may be one skill.
            candidates: dict[str, Path] = {}
            if (base / _SKILL_FILE).is_file():
                sk = _load_skill_dir(base, "installed")
                if sk is not None:
                    candidates[sk.name] = base
            for sub in sorted(p for p in base.iterdir() if p.is_dir()):
                sk = _load_skill_dir(sub, "installed")
                if sk is not None:
                    candidates[sk.name] = sub

            if not candidates:
                return ToolResult(
                    content=f"No SKILL.md found in {repo!r}"
                    + (f"/{subpath}" if subpath else ""),
                    is_error=True,
                )
            if len(candidates) > 1 and not pick:
                return ToolResult(
                    content=json.dumps(
                        {
                            "multiple": True,
                            "skills": sorted(candidates),
                            "message": (
                                "Multiple skills found -- call skill_install again "
                                "with name=<one of these>."
                            ),
                        }
                    )
                )
            if pick:
                if pick not in candidates:
                    return ToolResult(
                        content=f"{pick!r} not among {sorted(candidates)}", is_error=True
                    )
                chosen = pick
            else:
                chosen = next(iter(candidates))

            target = self._installed_dir / chosen
            if target.exists():
                return ToolResult(
                    content=f"Skill {chosen!r} is already installed -- uninstall first.",
                    is_error=True,
                )
            self._installed_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(candidates[chosen], target)
            (target / ".provenance.json").write_text(
                json.dumps(
                    {
                        "repo": repo,
                        "ref": ref,
                        "sha": sha,
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        # Lands disabled: enabled_skills is deliberately untouched (ADR-0014).
        return ToolResult(
            content=json.dumps(
                {"name": chosen, "installed": True, "enabled": False, "source": repo}
            )
        )

    def _skill_use(self, args: dict) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        skill = self._discover().get(name)
        if skill is None:
            return ToolResult(content=f"No skill named {name!r}", is_error=True)
        if skill.name not in self._enabled_names():
            return ToolResult(
                content=f"Skill {name!r} is installed but disabled -- enable it first.",
                is_error=True,
            )
        md = skill.path / _SKILL_FILE
        try:
            _meta, body = _split_frontmatter(md.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return ToolResult(content=f"Could not read skill {name!r}: {exc}", is_error=True)
        return ToolResult(
            content=json.dumps(
                {
                    "name": skill.name,
                    "kind": skill.kind,
                    "tools": list(skill.tools),
                    "source": skill.source,
                    "instructions": body,
                    "resources": self._resource_manifest(skill),
                }
            )
        )

    def _skill_preview(self, args: dict) -> ToolResult:
        """Read a skill's full text for REVIEW, ignoring enabled state.

        The review-before-enable read (ADR-0014 decision 6): installed skills
        arrive disabled, and the user must be able to inspect the plain text
        before enabling. skill_use deliberately refuses a disabled skill (it is
        the planner-facing load); this is the management/UI counterpart.
        """
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(content="name is required", is_error=True)
        skill = self._discover().get(name)
        if skill is None:
            return ToolResult(content=f"No skill named {name!r}", is_error=True)
        md = skill.path / _SKILL_FILE
        try:
            _meta, body = _split_frontmatter(md.read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return ToolResult(content=f"Could not read skill {name!r}: {exc}", is_error=True)
        return ToolResult(
            content=json.dumps(
                {
                    "name": skill.name,
                    "kind": skill.kind,
                    "tools": list(skill.tools),
                    "source": skill.source,
                    "enabled": skill.name in self._enabled_names(),
                    "instructions": body,
                    "resources": self._resource_manifest(skill),
                }
            )
        )

    # ------------------------------------------------------------------
    # Panel spec (S5 #542, ADR-0012 decision 3 / ADR-0014 decision 8) --
    # declarative widget tree for the Skills panel. Returns data only; the
    # renderer (tray/lib/panel-spec.js) owns all drawing (SAFETY #3).
    # ------------------------------------------------------------------

    def _provenance_str(self, skill: Skill) -> str:
        """Human-readable provenance: 'seed', or 'owner/repo@sha' when known."""
        if skill.source != "installed":
            return "seed"
        sidecar = skill.path / ".provenance.json"
        if not sidecar.is_file():
            return "installed"
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "installed"
        repo = str(meta.get("repo") or "")
        sha = str(meta.get("sha") or "")
        if repo and sha:
            return f"{repo}@{sha[:7]}"
        return repo or "installed"

    def panel_spec(self, profile_id: "int | None") -> "dict | None":
        """Declarative panel spec for the Skills panel (Harness nav, sibling
        to Plugins). Widgets: an install action, then per skill a detail
        block (name/source/provenance/tools/enabled/instructions) plus an
        enable-or-disable action and, for installed (non-seed) skills, an
        uninstall action.
        """
        enabled = self._enabled_names()
        skills = sorted(self._discover().values(), key=lambda s: s.name)

        widgets: list = [
            {"type": "detail", "id": "skills-summary", "fields": [
                {"label": "Skills", "value": f"{len(skills)} installed, {len(enabled)} enabled"},
            ]},
            {
                "type": "action",
                "id": "skills-install",
                "label": "Install",
                "tool": "skill_install",
                "tool_args": {},
                "input_arg": "repo",
                "input_placeholder": "owner/repo",
            },
        ]

        for s in skills:
            is_enabled = s.name in enabled
            # Use the ungated review read so the user can inspect a disabled
            # skill's text before enabling it (ADR-0014 review-before-enable).
            # skill_use stays planner-facing (enabled-only); skill_preview is
            # the management/review counterpart.
            preview = self._skill_preview({"name": s.name})
            body = (
                json.loads(preview.content)["instructions"]
                if not preview.is_error
                else preview.content
            )
            widgets.append({"type": "detail", "id": f"skill-{s.name}", "fields": [
                {"label": "Name", "value": s.name},
                {"label": "Source", "value": self._provenance_str(s)},
                {"label": "Tools", "value": ", ".join(s.tools) or "none"},
                {"label": "Enabled", "value": "yes" if is_enabled else "no"},
                {"label": "Instructions", "value": body},
            ]})
            widgets.append({
                "type": "action",
                "id": f"skill-{s.name}-toggle",
                "label": "Disable" if is_enabled else "Enable",
                "tool": "skill_disable" if is_enabled else "skill_enable",
                "tool_args": {"name": s.name},
            })
            if s.source == "installed":
                widgets.append({
                    "type": "action",
                    "id": f"skill-{s.name}-uninstall",
                    "label": "Uninstall",
                    "tool": "skill_uninstall",
                    "tool_args": {"name": s.name},
                })

        return {"title": "Skills", "widgets": widgets}


def create(seed_dir: Path | None = None, installed_dir: Path | None = None) -> SkillsPlugin:
    return SkillsPlugin(seed_dir=seed_dir, installed_dir=installed_dir)
