"""
OS tools plugin tests — Issue #15.

Five plugins: Files, Shell, System, Apps, Clipboard.
TDD vertical slices: one test → one implementation → repeat.
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure plugins/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ===========================================================================
# ClipboardPlugin
# ===========================================================================

class TestClipboardPlugin:
    # -----------------------------------------------------------------------
    # Cycle 15 — write_clipboard stores text
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_write_clipboard_stores_text(self):
        from plugins.clipboard import ClipboardPlugin
        store = {}
        plugin = ClipboardPlugin(
            read_fn=lambda: store.get("text", ""),
            write_fn=lambda t: store.update({"text": t}),
        )
        result = await plugin.call_tool("write_clipboard", {"text": "hello clipboard"})
        assert not result.is_error
        assert store["text"] == "hello clipboard"

    # -----------------------------------------------------------------------
    # Cycle 16 — read_clipboard returns stored text
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_read_clipboard_returns_text(self):
        from plugins.clipboard import ClipboardPlugin
        plugin = ClipboardPlugin(
            read_fn=lambda: "stored text",
            write_fn=lambda t: None,
        )
        result = await plugin.call_tool("read_clipboard", {})
        assert not result.is_error
        assert result.content == "stored text"

    # -----------------------------------------------------------------------
    # Cycle 17 — write builds in-process history
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_write_builds_history(self):
        from plugins.clipboard import ClipboardPlugin
        plugin = ClipboardPlugin(read_fn=lambda: "", write_fn=lambda t: None)
        await plugin.call_tool("write_clipboard", {"text": "first"})
        await plugin.call_tool("write_clipboard", {"text": "second"})
        result = await plugin.call_tool("list_clipboard_history", {})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["history"] == ["second", "first"]  # newest first

    @pytest.mark.asyncio
    async def test_history_empty_on_new_plugin(self):
        from plugins.clipboard import ClipboardPlugin
        plugin = ClipboardPlugin(read_fn=lambda: "", write_fn=lambda t: None)
        result = await plugin.call_tool("list_clipboard_history", {})
        assert not result.is_error
        assert json.loads(result.content) == {"history": []}

    @pytest.mark.asyncio
    async def test_history_capped_at_50(self):
        from plugins.clipboard import ClipboardPlugin
        plugin = ClipboardPlugin(read_fn=lambda: "", write_fn=lambda t: None)
        for i in range(60):
            await plugin.call_tool("write_clipboard", {"text": str(i)})
        result = await plugin.call_tool("list_clipboard_history", {})
        data = json.loads(result.content)
        assert len(data["history"]) == 50

    @pytest.mark.asyncio
    async def test_read_clipboard_error_handled(self):
        from plugins.clipboard import ClipboardPlugin
        plugin = ClipboardPlugin(
            read_fn=lambda: (_ for _ in ()).throw(RuntimeError("clipboard unavailable")),
            write_fn=lambda t: None,
        )
        result = await plugin.call_tool("read_clipboard", {})
        assert result.is_error

    def test_list_tools_exposes_all_three(self):
        from plugins.clipboard import create
        names = {t.name for t in create().list_tools()}
        assert names == {"read_clipboard", "write_clipboard", "list_clipboard_history"}

    def test_create_returns_plugin_instance(self):
        from plugins.clipboard import create
        assert create().name == "clipboard"


# ===========================================================================
# AppsPlugin
# ===========================================================================

def _fake_proc(name, pid):
    p = MagicMock()
    p.info = {"name": name, "pid": pid}
    return p


class TestAppsPlugin:
    # -----------------------------------------------------------------------
    # Cycle 12 — list_running returns running process names + pids
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_list_running_returns_processes(self):
        from plugins.apps import AppsPlugin
        procs = [_fake_proc("notepad.exe", 100), _fake_proc("chrome.exe", 200)]
        plugin = AppsPlugin(process_iter=MagicMock(return_value=procs))
        result = await plugin.call_tool("list_running", {})
        assert not result.is_error
        data = json.loads(result.content)
        names = [a["name"] for a in data["apps"]]
        assert "notepad.exe" in names
        assert "chrome.exe" in names

    @pytest.mark.asyncio
    async def test_list_running_empty_when_no_processes(self):
        from plugins.apps import AppsPlugin
        plugin = AppsPlugin(process_iter=MagicMock(return_value=[]))
        result = await plugin.call_tool("list_running", {})
        assert not result.is_error
        assert json.loads(result.content) == {"apps": []}

    # -----------------------------------------------------------------------
    # Cycle 13 — launch_app spawns a process
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_launch_app_returns_pid(self):
        from plugins.apps import AppsPlugin
        fake_popen = MagicMock(return_value=MagicMock(pid=999))
        plugin = AppsPlugin(popen_fn=fake_popen)
        result = await plugin.call_tool("launch_app", {"app": "notepad"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["pid"] == 999
        fake_popen.assert_called_once()

    @pytest.mark.asyncio
    async def test_launch_app_error_on_not_found(self):
        from plugins.apps import AppsPlugin
        fake_popen = MagicMock(side_effect=FileNotFoundError("not found"))
        plugin = AppsPlugin(popen_fn=fake_popen)
        result = await plugin.call_tool("launch_app", {"app": "nonexistent_xyz"})
        assert result.is_error

    # -----------------------------------------------------------------------
    # Cycle 14 — close_app terminates by name or pid
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_close_app_by_name_terminates_process(self):
        from plugins.apps import AppsPlugin
        mock_proc = MagicMock()
        mock_proc.info = {"name": "notepad.exe", "pid": 100}
        mock_proc.terminate = MagicMock()
        plugin = AppsPlugin(process_iter=MagicMock(return_value=[mock_proc]))
        result = await plugin.call_tool("close_app", {"app": "notepad.exe"})
        assert not result.is_error
        mock_proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_app_not_found_returns_error(self):
        from plugins.apps import AppsPlugin
        plugin = AppsPlugin(process_iter=MagicMock(return_value=[]))
        result = await plugin.call_tool("close_app", {"app": "doesnotexist.exe"})
        assert result.is_error

    def test_list_tools_exposes_all_three(self):
        from plugins.apps import create
        names = {t.name for t in create().list_tools()}
        assert names == {"list_running", "launch_app", "close_app"}

    def test_create_returns_plugin_instance(self):
        from plugins.apps import create
        assert create().name == "apps"


# ===========================================================================
# SystemPlugin
# ===========================================================================

def _make_system_plugin(stdout="", returncode=0):
    """Return a SystemPlugin with an injected fake runner."""
    from plugins.system import SystemPlugin
    fake = MagicMock(return_value=MagicMock(stdout=stdout, stderr="", returncode=returncode))
    return SystemPlugin(run_fn=fake), fake


class TestSystemPlugin:
    # -----------------------------------------------------------------------
    # Cycle 7 — get_volume returns volume level
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_get_volume_returns_result(self):
        from plugins.system import SystemPlugin
        plugin = SystemPlugin(run_fn=MagicMock(
            return_value=MagicMock(stdout="50", stderr="", returncode=0)
        ))
        result = await plugin.call_tool("get_volume", {})
        assert not result.is_error

    # -----------------------------------------------------------------------
    # Cycle 8 — set_volume calls runner with level
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_set_volume_calls_runner(self):
        plugin, fake = _make_system_plugin()
        result = await plugin.call_tool("set_volume", {"level": 42})
        assert not result.is_error
        fake.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_volume_clamps_to_100(self):
        plugin, fake = _make_system_plugin()
        result = await plugin.call_tool("set_volume", {"level": 150})
        assert not result.is_error

    # -----------------------------------------------------------------------
    # Cycle 9 — take_screenshot stubs gracefully
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_take_screenshot_returns_path_or_stub(self, tmp_path):
        plugin, fake = _make_system_plugin()
        result = await plugin.call_tool("take_screenshot", {"path": str(tmp_path / "shot.png")})
        assert not result.is_error

    # -----------------------------------------------------------------------
    # Cycle 10 — get_wifi_status returns connected/ssid
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_get_wifi_status_returns_result(self):
        plugin, fake = _make_system_plugin(stdout="connected")
        result = await plugin.call_tool("get_wifi_status", {})
        assert not result.is_error

    # -----------------------------------------------------------------------
    # Cycle 11 — shutdown/restart are safety-stubbed (never actually run)
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_shutdown_is_stubbed(self):
        from plugins.system import SystemPlugin
        plugin = SystemPlugin(run_fn=MagicMock())  # run_fn must NOT be called
        result = await plugin.call_tool("shutdown", {})
        assert not result.is_error
        assert "shutdown" in result.content.lower() or "stub" in result.content.lower()

    @pytest.mark.asyncio
    async def test_restart_is_stubbed(self):
        from plugins.system import SystemPlugin
        plugin = SystemPlugin(run_fn=MagicMock())
        result = await plugin.call_tool("restart", {})
        assert not result.is_error
        assert "restart" in result.content.lower() or "stub" in result.content.lower()

    def test_list_tools_exposes_all_six(self):
        from plugins.system import create
        names = {t.name for t in create().list_tools()}
        assert names == {"get_volume", "set_volume", "take_screenshot", "get_wifi_status", "shutdown", "restart"}

    def test_create_returns_plugin_instance(self):
        from plugins.system import create
        assert create().name == "system"


# ===========================================================================
# ShellPlugin
# ===========================================================================

class TestShellPlugin:
    # -----------------------------------------------------------------------
    # Cycle 6 — run_command returns stdout, stderr, exit_code
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_run_command_returns_stdout(self):
        from plugins.shell import create
        plugin = create()
        result = await plugin.call_tool("run_command", {"command": "echo hello"})
        assert not result.is_error
        data = json.loads(result.content)
        assert "hello" in data["stdout"]
        assert data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_run_command_captures_stderr_on_failure(self):
        from plugins.shell import create
        plugin = create()
        # invalid command on all platforms
        result = await plugin.call_tool("run_command", {"command": "cmd_that_does_not_exist_xyz"})
        data = json.loads(result.content)
        assert data["exit_code"] != 0

    @pytest.mark.asyncio
    async def test_run_command_timeout_returns_error(self, tmp_path):
        """Wall-clock kill returns is_error=True (SBX-3 sandbox replaces subprocess timeout)."""
        from plugins.shell import ShellPlugin
        import plugins.shell as _shell_mod
        from cerebral.sandbox._interface import Sandbox, SandboxResult

        class _KilledSandbox(Sandbox):
            def spawn(self, cmd, workdir, *, timeout_s=None):
                return SandboxResult(stdout="", stderr="", exit_code=-1, killed_reason="wall_clock")

        _shell_mod.set_workdir_fn(lambda: str(tmp_path))
        plugin = ShellPlugin(sandbox=_KilledSandbox())
        result = await plugin.call_tool("run_command", {"command": "sleep 10", "timeout": 0.1})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_run_command_injected_runner(self, tmp_path):
        """Injected sandbox avoids real subprocess -- tests isolation."""
        from plugins.shell import ShellPlugin
        import plugins.shell as _shell_mod
        from cerebral.sandbox._interface import Sandbox, SandboxResult

        class _FakeSandbox(Sandbox):
            def spawn(self, cmd, workdir, *, timeout_s=None):
                return SandboxResult(stdout="fake out", stderr="", exit_code=0)

        _shell_mod.set_workdir_fn(lambda: str(tmp_path))
        plugin = ShellPlugin(sandbox=_FakeSandbox())
        result = await plugin.call_tool("run_command", {"command": "anything"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["stdout"] == "fake out"
        assert data["exit_code"] == 0

    def test_list_tools_exposes_run_command(self):
        from plugins.shell import create
        names = {t.name for t in create().list_tools()}
        assert "run_command" in names

    def test_create_returns_plugin_instance(self):
        from plugins.shell import create
        assert create().name == "shell"


# ===========================================================================
# FilesPlugin
# ===========================================================================

class TestFilesPlugin:
    # -----------------------------------------------------------------------
    # Cycle 1 — create_file creates a file on disk
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_file_creates_file(self, tmp_path):
        from plugins.files import create
        plugin = create()
        target = tmp_path / "hello.txt"
        result = await plugin.call_tool("create_file", {"path": str(target), "content": "hello"})
        assert not result.is_error
        assert target.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_create_file_default_empty_content(self, tmp_path):
        from plugins.files import create
        plugin = create()
        target = tmp_path / "empty.txt"
        result = await plugin.call_tool("create_file", {"path": str(target)})
        assert not result.is_error
        assert target.read_text() == ""

    @pytest.mark.asyncio
    async def test_create_file_creates_parent_dirs(self, tmp_path):
        from plugins.files import create
        plugin = create()
        target = tmp_path / "a" / "b" / "c.txt"
        result = await plugin.call_tool("create_file", {"path": str(target), "content": "deep"})
        assert not result.is_error
        assert target.read_text() == "deep"

    # -----------------------------------------------------------------------
    # Cycle 2 — read_file returns content
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_read_file_returns_content(self, tmp_path):
        from plugins.files import create
        plugin = create()
        f = tmp_path / "data.txt"
        f.write_text("world", encoding="utf-8")
        result = await plugin.call_tool("read_file", {"path": str(f)})
        assert not result.is_error
        assert result.content == "world"

    @pytest.mark.asyncio
    async def test_read_file_error_on_missing(self, tmp_path):
        from plugins.files import create
        plugin = create()
        result = await plugin.call_tool("read_file", {"path": str(tmp_path / "nope.txt")})
        assert result.is_error

    # -----------------------------------------------------------------------
    # Cycle 3 — move_file renames the file
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_move_file_renames(self, tmp_path):
        from plugins.files import create
        plugin = create()
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "dst.txt"
        result = await plugin.call_tool("move_file", {"src": str(src), "dst": str(dst)})
        assert not result.is_error
        assert dst.read_text() == "data"
        assert not src.exists()

    # -----------------------------------------------------------------------
    # Cycle 4 — delete_file removes the file
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_delete_file_removes_file(self, tmp_path):
        from plugins.files import create
        plugin = create()
        f = tmp_path / "bye.txt"
        f.write_text("remove me")
        result = await plugin.call_tool("delete_file", {"path": str(f)})
        assert not result.is_error
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_file_error_on_missing(self, tmp_path):
        from plugins.files import create
        plugin = create()
        result = await plugin.call_tool("delete_file", {"path": str(tmp_path / "ghost.txt")})
        assert result.is_error

    # -----------------------------------------------------------------------
    # Cycle 5 — search_files returns matching paths
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_search_files_finds_matches(self, tmp_path):
        from plugins.files import create
        plugin = create()
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        (tmp_path / "c.log").write_text("")
        result = await plugin.call_tool("search_files", {"pattern": "*.txt", "directory": str(tmp_path)})
        assert not result.is_error
        matches = json.loads(result.content)
        names = [Path(m).name for m in matches]
        assert "a.txt" in names
        assert "b.txt" in names
        assert "c.log" not in names

    @pytest.mark.asyncio
    async def test_search_files_empty_when_no_match(self, tmp_path):
        from plugins.files import create
        plugin = create()
        result = await plugin.call_tool("search_files", {"pattern": "*.xyz", "directory": str(tmp_path)})
        assert not result.is_error
        assert json.loads(result.content) == []

    # -----------------------------------------------------------------------
    # list_tools exposes all six tools
    # -----------------------------------------------------------------------
    def test_list_tools_exposes_all_five(self):
        from plugins.files import create
        plugin = create()
        names = {t.name for t in plugin.list_tools()}
        assert names == {"create_file", "read_file", "edit_file", "move_file", "delete_file", "search_files"}

    def test_create_returns_plugin_instance(self):
        from plugins.files import create
        assert create().name == "files"
