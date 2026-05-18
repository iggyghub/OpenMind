"""
Obsidian MCP plugin — Issue #106.

Tools: obsidian_list_notes, obsidian_read_note, obsidian_search_notes.

Reads a local Obsidian vault (a directory of Markdown notes) directly off
disk — filesystem-direct, no network, no auth. This does NOT talk to the
Obsidian Local REST API community plugin.

The vault root comes from the OBSIDIAN_VAULT env var (read at create() time),
or an injected vault_root for tests — the wikipedia.create(fetch_fn=) pattern.
All tool paths are vault-relative; the vault root is never a tool argument and
every path is contained to the vault root.
"""
import json
import logging
import os
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "obsidian"

# ADR-0005 / Issue #106 — all three tools only read *.md files from the
# configured local vault. Reading the OBSIDIAN_VAULT env var and the
# pathlib resolve/containment ops require no capability.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read"})

_DEFAULT_LIMIT = 25


def _is_hidden(rel: Path) -> bool:
    """True if any component of the vault-relative path starts with '.'
    (covers `.obsidian/` and dotfiles/dot-dirs)."""
    return any(part.startswith(".") for part in rel.parts)


class ObsidianPlugin:
    name = PLUGIN_NAME

    def __init__(self, vault_root: str | Path | None = None) -> None:
        self._vault: Path | None = (
            Path(vault_root).resolve() if vault_root is not None else None
        )

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="obsidian_list_notes",
                description=(
                    "List every Markdown note in the local Obsidian vault, "
                    "as sorted vault-relative paths."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="obsidian_read_note",
                description=(
                    "Read and return the raw Markdown text of a note by its "
                    "vault-relative path."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Vault-relative path, e.g. 'projects/openmind.md'",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="obsidian_search_notes",
                description=(
                    "Case-insensitive substring search over note filenames and "
                    "content. Returns matching vault-relative paths and a snippet."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search term"},
                        "max_results": {
                            "type": "integer",
                            "description": f"Maximum results to return (default {_DEFAULT_LIMIT})",
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "obsidian_list_notes":
            return self._list(args)
        if tool_name == "obsidian_read_note":
            return self._read(args)
        if tool_name == "obsidian_search_notes":
            return self._search(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _vault_or_error(self) -> tuple[Path | None, ToolResult | None]:
        if self._vault is None:
            return None, ToolResult(
                content="OBSIDIAN_VAULT is not configured", is_error=True
            )
        return self._vault, None

    def _contained(self, vault: Path, rel: str) -> Path | None:
        """Resolve a vault-relative path and return it iff it stays inside the
        vault root (after resolving symlinks and `..`); else None."""
        candidate = (vault / rel).resolve()
        if not candidate.is_relative_to(vault):
            return None
        return candidate

    def _iter_notes(self, vault: Path) -> list[Path]:
        """Sorted *.md files under the vault, excluding hidden components."""
        out: list[Path] = []
        for p in vault.rglob("*.md"):
            rel = p.relative_to(vault)
            if _is_hidden(rel):
                continue
            out.append(p)
        return sorted(out, key=lambda p: p.relative_to(vault).as_posix())

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #

    def _list(self, args: dict) -> ToolResult:
        vault, err = self._vault_or_error()
        if err is not None:
            return err
        try:
            notes = [p.relative_to(vault).as_posix() for p in self._iter_notes(vault)]
        except OSError as exc:
            logger.error("[obsidian] obsidian_list_notes failed: %s", exc)
            return ToolResult(content=str(exc), is_error=True)
        return ToolResult(content=json.dumps({"notes": notes}))

    def _read(self, args: dict) -> ToolResult:
        vault, err = self._vault_or_error()
        if err is not None:
            return err
        rel = args.get("path")
        if not rel:
            return ToolResult(
                content="'path' is required for obsidian_read_note", is_error=True
            )
        if not str(rel).endswith(".md"):
            return ToolResult(
                content="obsidian_read_note only reads .md notes", is_error=True
            )
        target = self._contained(vault, str(rel))
        if target is None:
            return ToolResult(content="path escapes the vault", is_error=True)
        try:
            return ToolResult(content=target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            logger.error("[obsidian] obsidian_read_note failed: %s", exc)
            return ToolResult(content=str(exc), is_error=True)

    def _search(self, args: dict) -> ToolResult:
        vault, err = self._vault_or_error()
        if err is not None:
            return err
        query = args.get("query")
        if not query:
            return ToolResult(
                content="'query' is required for obsidian_search_notes", is_error=True
            )
        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        needle = str(query).lower()
        results: list[dict] = []
        try:
            notes = self._iter_notes(vault)
        except OSError as exc:
            logger.error("[obsidian] obsidian_search_notes failed: %s", exc)
            return ToolResult(content=str(exc), is_error=True)
        for p in notes:
            relpath = p.relative_to(vault).as_posix()
            try:
                content = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            in_path = needle in relpath.lower()
            in_body = needle in content.lower()
            if not (in_path or in_body):
                continue
            snippet = ""
            if in_body:
                for line in content.splitlines():
                    if needle in line.lower():
                        snippet = line.strip()
                        break
            results.append({"path": relpath, "snippet": snippet})
            if len(results) >= max_results:
                break
        return ToolResult(content=json.dumps({"results": results}))


def create(vault_root: str | Path | None = None) -> ObsidianPlugin:
    if vault_root is None:
        vault_root = os.environ.get("OBSIDIAN_VAULT") or None
    return ObsidianPlugin(vault_root=vault_root)
