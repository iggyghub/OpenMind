"""
Obsidian MCP plugin tests — Issue #106.

Tools: obsidian_list_notes, obsidian_read_note, obsidian_search_notes.

Filesystem-direct over a local Obsidian vault — no network, no auth, no db.
The vault root is injected via create(vault_root=tmp_path) (the
wikipedia.create(fetch_fn=) test-injection pattern); production reads the
OBSIDIAN_VAULT env var.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _vault(tmp_path: Path, files: dict[str, str]) -> Path:
    """Materialise {vault-relative-path: content} under tmp_path and return it."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools, create() factory, capabilities
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_three(self):
        from plugins.obsidian import create

        names = {t.name for t in create().list_tools()}
        assert names == {
            "obsidian_list_notes",
            "obsidian_read_note",
            "obsidian_search_notes",
        }

    def test_create_plugin_named_obsidian(self):
        from plugins.obsidian import create

        assert create().name == "obsidian"

    def test_tools_have_required_args_in_schema(self):
        from plugins.obsidian import create

        tools = {t.name: t for t in create().list_tools()}
        assert tools["obsidian_list_notes"].schema.get("required", []) == []
        assert "path" in tools["obsidian_read_note"].schema.get("required", [])
        assert "query" in tools["obsidian_search_notes"].schema.get("required", [])

    def test_required_capabilities_is_fs_read_only(self):
        from plugins.obsidian import REQUIRED_CAPABILITIES

        assert REQUIRED_CAPABILITIES == frozenset({"fs_read"})

    def test_create_reads_env_var_when_no_arg(self, tmp_path, monkeypatch):
        from plugins.obsidian import create

        monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path))
        plugin = create()
        assert plugin._vault == tmp_path.resolve()

    def test_create_arg_overrides_env(self, tmp_path, monkeypatch):
        from plugins.obsidian import create

        monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path / "from_env"))
        injected = tmp_path / "from_arg"
        injected.mkdir()
        plugin = create(vault_root=injected)
        assert plugin._vault == injected.resolve()


# ---------------------------------------------------------------------------
# Cycle 2 — unconfigured vault → every tool is_error, no crash
# ---------------------------------------------------------------------------

class TestUnconfigured:
    @pytest.mark.asyncio
    async def test_create_constructs_without_env_or_arg(self, monkeypatch):
        from plugins.obsidian import create

        monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
        plugin = create()  # must not raise
        assert plugin._vault is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool, args",
        [
            ("obsidian_list_notes", {}),
            ("obsidian_read_note", {"path": "a.md"}),
            ("obsidian_search_notes", {"query": "x"}),
        ],
    )
    async def test_tools_error_when_unconfigured(self, tool, args, monkeypatch):
        from plugins.obsidian import create

        monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
        plugin = create()
        result = await plugin.call_tool(tool, args)
        assert result.is_error
        assert "OBSIDIAN_VAULT is not configured" in result.content


# ---------------------------------------------------------------------------
# Cycle 3 — obsidian_list_notes
# ---------------------------------------------------------------------------

class TestListNotes:
    @pytest.mark.asyncio
    async def test_lists_sorted_vault_relative_md(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {
            "z.md": "z",
            "a.md": "a",
            "sub/b.md": "b",
        })
        plugin = create(vault_root=vault)
        result = await plugin.call_tool("obsidian_list_notes", {})
        assert not result.is_error
        assert json.loads(result.content) == {"notes": ["a.md", "sub/b.md", "z.md"]}

    @pytest.mark.asyncio
    async def test_excludes_non_md_and_dotdirs(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {
            "note.md": "ok",
            "data.txt": "skip",
            ".obsidian/workspace.md": "skip",
            ".hidden.md": "skip",
            ".trash/old.md": "skip",
        })
        plugin = create(vault_root=vault)
        result = await plugin.call_tool("obsidian_list_notes", {})
        assert json.loads(result.content) == {"notes": ["note.md"]}

    @pytest.mark.asyncio
    async def test_empty_vault_returns_empty_list(self, tmp_path):
        from plugins.obsidian import create

        plugin = create(vault_root=tmp_path)
        result = await plugin.call_tool("obsidian_list_notes", {})
        assert json.loads(result.content) == {"notes": []}


# ---------------------------------------------------------------------------
# Cycle 4 — obsidian_read_note
# ---------------------------------------------------------------------------

class TestReadNote:
    @pytest.mark.asyncio
    async def test_reads_raw_markdown_verbatim(self, tmp_path):
        from plugins.obsidian import create

        body = "# Title\n\n- [[wikilink]] #tag\n---\nfrontmatter-ish\n"
        vault = _vault(tmp_path, {"sub/note.md": body})
        plugin = create(vault_root=vault)
        result = await plugin.call_tool("obsidian_read_note", {"path": "sub/note.md"})
        assert not result.is_error
        assert result.content == body  # verbatim, no frontmatter/wikilink processing

    @pytest.mark.asyncio
    async def test_missing_path_arg_errors(self, tmp_path):
        from plugins.obsidian import create

        plugin = create(vault_root=tmp_path)
        result = await plugin.call_tool("obsidian_read_note", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_md_path_errors(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {"data.txt": "x"})
        plugin = create(vault_root=vault)
        result = await plugin.call_tool("obsidian_read_note", {"path": "data.txt"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_missing_file_errors(self, tmp_path):
        from plugins.obsidian import create

        plugin = create(vault_root=tmp_path)
        result = await plugin.call_tool("obsidian_read_note", {"path": "nope.md"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_non_utf8_file_errors_no_crash(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {"bad.md": b"\xff\xfe\x00bad"})
        plugin = create(vault_root=vault)
        result = await plugin.call_tool("obsidian_read_note", {"path": "bad.md"})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 5 — traversal containment (the differentiator vs files.py)
# ---------------------------------------------------------------------------

class TestContainment:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [
        "../outside.md",
        "../../etc/passwd.md",
        "sub/../../escape.md",
    ])
    async def test_relative_escape_rejected(self, tmp_path, bad):
        from plugins.obsidian import create

        vault = tmp_path / "vault"
        vault.mkdir()
        (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
        plugin = create(vault_root=vault)
        result = await plugin.call_tool("obsidian_read_note", {"path": bad})
        assert result.is_error
        assert "path escapes the vault" in result.content

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, tmp_path):
        from plugins.obsidian import create

        vault = tmp_path / "vault"
        vault.mkdir()
        secret = tmp_path / "secret.md"
        secret.write_text("secret", encoding="utf-8")
        plugin = create(vault_root=vault)
        result = await plugin.call_tool(
            "obsidian_read_note", {"path": str(secret)}
        )
        assert result.is_error
        assert "path escapes the vault" in result.content

    @pytest.mark.asyncio
    async def test_symlink_escape_rejected(self, tmp_path):
        from plugins.obsidian import create

        vault = tmp_path / "vault"
        vault.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        link = vault / "link.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform/user")
        plugin = create(vault_root=vault)
        result = await plugin.call_tool("obsidian_read_note", {"path": "link.md"})
        assert result.is_error
        assert "path escapes the vault" in result.content


# ---------------------------------------------------------------------------
# Cycle 6 — obsidian_search_notes
# ---------------------------------------------------------------------------

class TestSearchNotes:
    @pytest.mark.asyncio
    async def test_missing_query_errors(self, tmp_path):
        from plugins.obsidian import create

        plugin = create(vault_root=tmp_path)
        result = await plugin.call_tool("obsidian_search_notes", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_content_match_case_insensitive_with_snippet(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {
            "a.md": "nothing here\nThe Quick Brown Fox\nmore",
            "b.md": "unrelated",
        })
        plugin = create(vault_root=vault)
        result = await plugin.call_tool(
            "obsidian_search_notes", {"query": "quick brown"}
        )
        payload = json.loads(result.content)
        assert payload == {"results": [
            {"path": "a.md", "snippet": "The Quick Brown Fox"},
        ]}

    @pytest.mark.asyncio
    async def test_filename_match_has_empty_snippet(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {"meeting-notes.md": "agenda"})
        plugin = create(vault_root=vault)
        result = await plugin.call_tool(
            "obsidian_search_notes", {"query": "meeting"}
        )
        assert json.loads(result.content) == {
            "results": [{"path": "meeting-notes.md", "snippet": ""}]
        }

    @pytest.mark.asyncio
    async def test_max_results_caps(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {f"n{i}.md": "match me" for i in range(10)})
        plugin = create(vault_root=vault)
        result = await plugin.call_tool(
            "obsidian_search_notes", {"query": "match", "max_results": 3}
        )
        assert len(json.loads(result.content)["results"]) == 3

    @pytest.mark.asyncio
    async def test_default_limit_is_25(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {f"n{i:02d}.md": "match" for i in range(30)})
        plugin = create(vault_root=vault)
        result = await plugin.call_tool(
            "obsidian_search_notes", {"query": "match"}
        )
        assert len(json.loads(result.content)["results"]) == 25

    @pytest.mark.asyncio
    async def test_search_skips_unreadable_files(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {
            "good.md": "findme",
            "bad.md": b"\xff\xfendme",
        })
        plugin = create(vault_root=vault)
        result = await plugin.call_tool(
            "obsidian_search_notes", {"query": "findme"}
        )
        assert json.loads(result.content) == {
            "results": [{"path": "good.md", "snippet": "findme"}]
        }

    @pytest.mark.asyncio
    async def test_search_excludes_dotdirs(self, tmp_path):
        from plugins.obsidian import create

        vault = _vault(tmp_path, {
            "real.md": "needle",
            ".obsidian/cfg.md": "needle",
        })
        plugin = create(vault_root=vault)
        result = await plugin.call_tool(
            "obsidian_search_notes", {"query": "needle"}
        )
        assert json.loads(result.content) == {
            "results": [{"path": "real.md", "snippet": "needle"}]
        }


# ---------------------------------------------------------------------------
# Cycle 7 — unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_errors(self, tmp_path):
        from plugins.obsidian import create

        plugin = create(vault_root=tmp_path)
        result = await plugin.call_tool("obsidian_nope", {})
        assert result.is_error
        assert "Unknown tool" in result.content
