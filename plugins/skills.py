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

Enable-state (disabled-by-default, `enabled_skills`), install-from-GitHub, and the
lifecycle tools land in later slices (#538 / #541). This slice surfaces every
discovered skill.
"""
# NOTE: deliberately NO `from __future__ import annotations`. This module is
# loaded by the orchestrator via spec_from_file_location, which does NOT place
# it in sys.modules (a deliberate #153 choice). Under stringized annotations,
# dataclasses' ClassVar check resolves field types by looking the module up in
# sys.modules -- which fails here (module absent) and refuses the plugin at
# load. Real annotation objects avoid that lookup. See test_orchestrator.py
# ::test_every_real_plugin_declares_valid_required_capabilities.

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "skills"

# ADR-0005 / Issue #44 -- skill_list / skill_use only read SKILL.md files and
# their bundled resources (fs_read). Write-class capabilities (fs_write for
# install, fs_delete for uninstall) arrive with the lifecycle + install slices.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read"})

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

    def __init__(self, seed_dir: Path | None = None, installed_dir: Path | None = None) -> None:
        self._seed_dir = Path(seed_dir) if seed_dir else _REPO_ROOT / "skills"
        self._installed_dir = (
            Path(installed_dir) if installed_dir else data_dir() / "skills"
        )

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
            if f.is_file() and f.name != _SKILL_FILE:
                manifest.append(f.relative_to(skill.path).as_posix())
        return manifest

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="skill_list",
                description=(
                    "List installed skills (procedures Felix can follow), each with "
                    "its name and description. Call this to see what skills are "
                    "available before using one."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="skill_use",
                description=(
                    "Load a skill by name: returns its full instruction text (and a "
                    "manifest of any bundled resource files) so you can follow the "
                    "procedure. Use when a task matches a skill from skill_list, or "
                    "when the user asks for a named skill."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The skill's name."},
                    },
                    "required": ["name"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "skill_list":
            return self._skill_list()
        if tool_name == "skill_use":
            return self._skill_use(args or {})
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    def _skill_list(self) -> ToolResult:
        skills = self._discover()
        payload = [
            {
                "name": s.name,
                "description": s.description,
                "kind": s.kind,
                "tools": list(s.tools),
                "source": s.source,
            }
            for s in sorted(skills.values(), key=lambda s: s.name)
        ]
        return ToolResult(content=json.dumps({"skills": payload}))

    def _skill_use(self, args: dict) -> ToolResult:
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
                    "instructions": body,
                    "resources": self._resource_manifest(skill),
                }
            )
        )


def create(seed_dir: Path | None = None, installed_dir: Path | None = None) -> SkillsPlugin:
    return SkillsPlugin(seed_dir=seed_dir, installed_dir=installed_dir)
