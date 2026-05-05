"""
SSH MCP plugin tests — Issue #24.

Tool: ssh_run_command(host, command, port?, key_path?).

Builds an `ssh user@host -i key -p port "command"` line via injected run_fn.
Never writes keys, never prompts, fails loudly on auth interaction.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    captured: dict = {"argv": None, "kwargs": None, "calls": 0}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        captured["calls"] += 1
        return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

    return runner, captured


# ---------------------------------------------------------------------------
# Cycle 1 — list_tools
# ---------------------------------------------------------------------------

class TestListTools:
    def test_list_tools_exposes_one(self):
        from plugins.ssh import create

        names = {t.name for t in create().list_tools()}
        assert names == {"ssh_run_command"}

    def test_create_plugin_named_ssh(self):
        from plugins.ssh import create

        assert create().name == "ssh"

    def test_tool_schema_requires_host_and_command(self):
        from plugins.ssh import create

        tool = create().list_tools()[0]
        assert "host" in tool.schema["required"]
        assert "command" in tool.schema["required"]


# ---------------------------------------------------------------------------
# Cycle 2 — required args
# ---------------------------------------------------------------------------

class TestRequiredArgs:
    @pytest.mark.asyncio
    async def test_missing_host_returns_error(self):
        from plugins.ssh import SshPlugin

        run_fn, captured = _fake_run()
        plugin = SshPlugin(run_fn=run_fn)
        result = await plugin.call_tool("ssh_run_command", {"command": "uptime"})
        assert result.is_error
        assert captured["calls"] == 0

    @pytest.mark.asyncio
    async def test_missing_command_returns_error(self):
        from plugins.ssh import SshPlugin

        run_fn, captured = _fake_run()
        plugin = SshPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "ssh_run_command", {"host": "user@server"}
        )
        assert result.is_error
        assert captured["calls"] == 0


# ---------------------------------------------------------------------------
# Cycle 3 — argv shape
# ---------------------------------------------------------------------------

class TestArgvShape:
    @pytest.mark.asyncio
    async def test_basic_invocation_uses_ssh(self):
        from plugins.ssh import SshPlugin

        run_fn, captured = _fake_run(stdout="up 5 days")
        plugin = SshPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "ssh_run_command", {"host": "user@host.example", "command": "uptime"}
        )
        assert not result.is_error
        argv = captured["argv"]
        assert argv[0] == "ssh"
        assert "user@host.example" in argv
        assert "uptime" in argv

    @pytest.mark.asyncio
    async def test_port_added_with_p_flag(self):
        from plugins.ssh import SshPlugin

        run_fn, captured = _fake_run()
        plugin = SshPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "ssh_run_command",
            {"host": "user@host", "command": "ls", "port": 2222},
        )
        argv = captured["argv"]
        assert "-p" in argv
        # port appears as a string right after -p
        assert "2222" in argv

    @pytest.mark.asyncio
    async def test_key_path_added_with_i_flag(self):
        from plugins.ssh import SshPlugin

        run_fn, captured = _fake_run()
        plugin = SshPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "ssh_run_command",
            {
                "host": "user@host",
                "command": "ls",
                "key_path": "/home/me/.ssh/id_ed25519",
            },
        )
        argv = captured["argv"]
        assert "-i" in argv
        assert "/home/me/.ssh/id_ed25519" in argv


# ---------------------------------------------------------------------------
# Cycle 4 — fail loud on auth interaction
# ---------------------------------------------------------------------------

class TestNoInteractiveAuth:
    @pytest.mark.asyncio
    async def test_uses_batch_mode_no_password_prompt(self):
        """The plugin should disable interactive password prompts entirely."""
        from plugins.ssh import SshPlugin

        run_fn, captured = _fake_run()
        plugin = SshPlugin(run_fn=run_fn)
        await plugin.call_tool(
            "ssh_run_command", {"host": "user@host", "command": "ls"}
        )
        argv = captured["argv"]
        # Either via -o BatchMode=yes or PasswordAuthentication=no, the call
        # must NOT allow an interactive password prompt to block.
        joined = " ".join(argv)
        assert "BatchMode=yes" in joined or "PasswordAuthentication=no" in joined


# ---------------------------------------------------------------------------
# Cycle 5 — error paths
# ---------------------------------------------------------------------------

class TestErrors:
    @pytest.mark.asyncio
    async def test_non_zero_exit_is_error(self):
        from plugins.ssh import SshPlugin

        run_fn, _ = _fake_run(stderr="Permission denied", returncode=255)
        plugin = SshPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "ssh_run_command", {"host": "user@host", "command": "x"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_run_fn_raises_returns_error(self):
        from plugins.ssh import SshPlugin

        def boom(argv, **kwargs):
            raise FileNotFoundError("ssh not on PATH")

        plugin = SshPlugin(run_fn=boom)
        result = await plugin.call_tool(
            "ssh_run_command", {"host": "user@host", "command": "ls"}
        )
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from plugins.ssh import SshPlugin

        run_fn, _ = _fake_run()
        plugin = SshPlugin(run_fn=run_fn)
        result = await plugin.call_tool("ssh_nope", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# Cycle 6 — success shape
# ---------------------------------------------------------------------------

class TestSuccessShape:
    @pytest.mark.asyncio
    async def test_success_returns_stdout_stderr_exit(self):
        from plugins.ssh import SshPlugin

        run_fn, _ = _fake_run(stdout="hello world\n", returncode=0)
        plugin = SshPlugin(run_fn=run_fn)
        result = await plugin.call_tool(
            "ssh_run_command", {"host": "user@host", "command": "echo hi"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["stdout"] == "hello world\n"
        assert data["exit_code"] == 0
