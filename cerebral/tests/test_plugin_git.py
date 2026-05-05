"""
Git MCP plugin tests — Issue #24.

TDD vertical slices for GitPlugin:
  - git_status, git_commit, git_push, git_pull, git_diff, git_log, git_branch

All shell-outs go through an injected run_fn so no real git binary is needed.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure plugins/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a run_fn that records its argv and returns canned output."""
    captured: dict = {"argv": None, "kwargs": None, "calls": 0}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        captured["calls"] += 1
        return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

    return runner, captured


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools exposes all seven git tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_seven(self):
        from plugins.git import create

        names = {t.name for t in create().list_tools()}
        assert names == {
            "git_status",
            "git_commit",
            "git_push",
            "git_pull",
            "git_diff",
            "git_log",
            "git_branch",
        }

    def test_all_tools_have_correct_plugin_name(self):
        from plugins.git import create

        for tool in create().list_tools():
            assert tool.plugin == "git"

    def test_all_tools_have_descriptions_and_schemas(self):
        from plugins.git import create

        for tool in create().list_tools():
            assert isinstance(tool.description, str) and tool.description
            assert isinstance(tool.schema, dict) and tool.schema

    def test_create_returns_plugin_named_git(self):
        from plugins.git import create

        assert create().name == "git"


# ---------------------------------------------------------------------------
# Cycle 2 — every subcommand shells out via injected run_fn with correct argv
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tool_name,args,expected_subcmd",
    [
        ("git_status", {}, "status"),
        ("git_push", {}, "push"),
        ("git_pull", {}, "pull"),
        ("git_diff", {}, "diff"),
        ("git_log", {}, "log"),
    ],
)
class TestSubcommandsArgv:
    @pytest.mark.asyncio
    async def test_subcommand_shells_out_to_git(self, tool_name, args, expected_subcmd):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run(stdout="ok")
        plugin = GitPlugin(run_fn=run_fn)

        result = await plugin.call_tool(tool_name, args)
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "git"
        assert expected_subcmd in argv


# ---------------------------------------------------------------------------
# Cycle 3 — git_commit requires a message
# ---------------------------------------------------------------------------

class TestGitCommit:
    @pytest.mark.asyncio
    async def test_commit_missing_message_returns_error(self):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run()
        plugin = GitPlugin(run_fn=run_fn)
        result = await plugin.call_tool("git_commit", {})
        assert result.is_error
        # Did NOT shell out
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_commit_with_message_passes_m_flag(self):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run(stdout="[main abc] msg\n")
        plugin = GitPlugin(run_fn=run_fn)

        result = await plugin.call_tool("git_commit", {"message": "fix: bug"})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "git"
        assert "commit" in argv
        assert "-m" in argv
        assert "fix: bug" in argv


# ---------------------------------------------------------------------------
# Cycle 4 — git_branch optional name
# ---------------------------------------------------------------------------

class TestGitBranch:
    @pytest.mark.asyncio
    async def test_branch_no_name_lists_branches(self):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run(stdout="* main\n  feature\n")
        plugin = GitPlugin(run_fn=run_fn)

        result = await plugin.call_tool("git_branch", {})
        assert not result.is_error
        argv = captured["argv"]
        assert argv[:2] == ["git", "branch"]
        assert len(argv) == 2  # no extra branch name

    @pytest.mark.asyncio
    async def test_branch_with_name_creates_branch(self):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run()
        plugin = GitPlugin(run_fn=run_fn)

        result = await plugin.call_tool("git_branch", {"name": "feat/x"})
        assert not result.is_error
        argv = captured["argv"]
        assert "branch" in argv
        assert "feat/x" in argv


# ---------------------------------------------------------------------------
# Cycle 5 — git_log accepts max_count
# ---------------------------------------------------------------------------

class TestGitLog:
    @pytest.mark.asyncio
    async def test_log_default_max_count(self):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run(stdout="commit abc\n")
        plugin = GitPlugin(run_fn=run_fn)

        await plugin.call_tool("git_log", {})
        argv = captured["argv"]
        assert "log" in argv

    @pytest.mark.asyncio
    async def test_log_custom_max_count_flag(self):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run(stdout="commit abc\n")
        plugin = GitPlugin(run_fn=run_fn)

        await plugin.call_tool("git_log", {"max_count": 5})
        argv = captured["argv"]
        # -n 5 or --max-count=5 both acceptable; check 5 appears
        joined = " ".join(str(a) for a in argv)
        assert "5" in joined


# ---------------------------------------------------------------------------
# Cycle 6 — repo_path defaults to os.getcwd, can be overridden
# ---------------------------------------------------------------------------

class TestRepoPath:
    @pytest.mark.asyncio
    async def test_default_repo_path_is_cwd(self):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run()
        plugin = GitPlugin(run_fn=run_fn)

        await plugin.call_tool("git_status", {})
        cwd = captured["kwargs"].get("cwd")
        assert cwd == os.getcwd()

    @pytest.mark.asyncio
    async def test_custom_repo_path_is_passed(self, tmp_path):
        from plugins.git import GitPlugin

        run_fn, captured = _fake_run()
        plugin = GitPlugin(run_fn=run_fn)

        await plugin.call_tool("git_status", {"repo_path": str(tmp_path)})
        assert captured["kwargs"].get("cwd") == str(tmp_path)


# ---------------------------------------------------------------------------
# Cycle 7 — non-zero exit returns error
# ---------------------------------------------------------------------------

class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_non_zero_exit_is_error(self):
        from plugins.git import GitPlugin

        run_fn, _ = _fake_run(stdout="", stderr="fatal: not a git repo", returncode=128)
        plugin = GitPlugin(run_fn=run_fn)

        result = await plugin.call_tool("git_status", {})
        assert result.is_error
        assert "fatal" in result.content.lower() or "128" in result.content

    @pytest.mark.asyncio
    async def test_run_fn_raises_returns_error(self):
        from plugins.git import GitPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("git: command not found")

        plugin = GitPlugin(run_fn=boom)
        result = await plugin.call_tool("git_status", {})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.git import GitPlugin

        run_fn, _ = _fake_run()
        plugin = GitPlugin(run_fn=run_fn)
        result = await plugin.call_tool("git_does_not_exist", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 8 — success path returns shell-style dict
# ---------------------------------------------------------------------------

class TestSuccessShape:
    @pytest.mark.asyncio
    async def test_success_returns_stdout_stderr_exit(self):
        from plugins.git import GitPlugin

        run_fn, _ = _fake_run(stdout="On branch main\n", stderr="", returncode=0)
        plugin = GitPlugin(run_fn=run_fn)

        result = await plugin.call_tool("git_status", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["stdout"] == "On branch main\n"
        assert data["stderr"] == ""
        assert data["exit_code"] == 0
