"""Unit tests for shell plugin SBX-3 -- sandbox dispatch (ADR-0010).

Dispatch tests (mock sandbox) run on all platforms.
Env-scrub tests (real WindowsSandbox) are Windows-only.
"""
import json
import os
import shutil
import sys
import uuid
from unittest.mock import patch

import pytest

import plugins.shell as _shell_mod
from plugins.shell import ShellPlugin
from cerebral.sandbox._interface import Sandbox, SandboxResult


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _MockSandbox(Sandbox):
    def __init__(self, result: SandboxResult):
        self._result = result
        self.calls: list[tuple] = []

    def spawn(self, cmd, workdir, *, timeout_s=None) -> SandboxResult:
        self.calls.append((cmd, workdir, timeout_s))
        return self._result


def _result(**kw) -> SandboxResult:
    return SandboxResult(
        stdout=kw.get("stdout", ""),
        stderr=kw.get("stderr", ""),
        exit_code=kw.get("exit_code", 0),
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_workdir_fn():
    orig = _shell_mod._workdir_fn
    yield
    _shell_mod._workdir_fn = orig


@pytest.fixture
def ac_workdir():
    """Workdir under C:\\Users\\Public -- AppContainer-traversable without parent ACL changes."""
    path = os.path.join(r"C:\Users\Public\OpenMind-sbx-test", uuid.uuid4().hex[:8])
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# dispatch tests -- platform-agnostic (mock sandbox)
# ---------------------------------------------------------------------------

async def test_run_command_routes_through_sandbox(tmp_path):
    """run_command calls sandbox.spawn with cmd/c wrapper; no direct subprocess."""
    mock = _MockSandbox(_result(stdout="hello\n"))
    _shell_mod.set_workdir_fn(lambda: str(tmp_path))
    plugin = ShellPlugin(sandbox=mock)

    # Assert no direct subprocess.run is called
    with patch("subprocess.run", side_effect=AssertionError("subprocess.run called directly")):
        tr = await plugin.call_tool("run_command", {"command": "echo hello"})

    assert len(mock.calls) == 1
    cmd, workdir, _ = mock.calls[0]
    assert cmd == ["cmd", "/c", "echo hello"]
    assert workdir == str(tmp_path)
    assert not tr.is_error
    payload = json.loads(tr.content)
    assert payload["exit_code"] == 0
    assert "hello" in payload["stdout"]


async def test_run_command_passes_timeout(tmp_path):
    mock = _MockSandbox(_result())
    _shell_mod.set_workdir_fn(lambda: str(tmp_path))
    plugin = ShellPlugin(sandbox=mock)
    await plugin.call_tool("run_command", {"command": "echo hi", "timeout": 42})
    _, _, timeout_s = mock.calls[0]
    assert timeout_s == 42.0


async def test_run_command_output_shape(tmp_path):
    """ToolResult content is JSON with stdout/stderr/exit_code."""
    mock = _MockSandbox(_result(stdout="out", stderr="err", exit_code=1))
    _shell_mod.set_workdir_fn(lambda: str(tmp_path))
    plugin = ShellPlugin(sandbox=mock)
    tr = await plugin.call_tool("run_command", {"command": "false"})
    payload = json.loads(tr.content)
    assert payload == {"stdout": "out", "stderr": "err", "exit_code": 1}


async def test_run_command_no_sandbox_returns_error():
    """If sandbox is None (non-Windows without sandbox), run_command returns error."""
    plugin = ShellPlugin(sandbox=None)
    tr = await plugin.call_tool("run_command", {"command": "echo hi"})
    assert tr.is_error


async def test_unknown_tool_returns_error(tmp_path):
    mock = _MockSandbox(_result())
    _shell_mod.set_workdir_fn(lambda: str(tmp_path))
    plugin = ShellPlugin(sandbox=mock)
    tr = await plugin.call_tool("nonexistent", {})
    assert tr.is_error
    assert len(mock.calls) == 0


# ---------------------------------------------------------------------------
# input_files / output_files -- crossing the sandbox boundary
# ---------------------------------------------------------------------------

@pytest.fixture
def io_dirs(tmp_path, monkeypatch):
    att = tmp_path / "attachments"
    att.mkdir()
    exp = tmp_path / "Downloads" / "Felix"
    wd = tmp_path / "wd"
    wd.mkdir()
    monkeypatch.setattr(_shell_mod, "_ATTACHMENTS_ROOT", att)
    monkeypatch.setattr(_shell_mod, "_EXPORT_DIR", exp)
    _shell_mod.set_workdir_fn(lambda: str(wd))
    return att, exp, wd


async def test_input_files_stages_attachment_into_in(io_dirs):
    att, _, wd = io_dirs
    src = att / "1" / "abc" / "clip.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"video")
    plugin = ShellPlugin(sandbox=_MockSandbox(_result()))
    tr = await plugin.call_tool("run_command", {"command": "x", "input_files": [str(src)]})
    assert not tr.is_error
    assert (wd / "in" / "clip.mp4").read_bytes() == b"video"


async def test_input_files_refuses_paths_outside_attachments(io_dirs, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    mock = _MockSandbox(_result())
    plugin = ShellPlugin(sandbox=mock)
    tr = await plugin.call_tool("run_command", {"command": "x", "input_files": [str(secret)]})
    assert tr.is_error
    assert mock.calls == []  # refused before anything ran


async def test_output_files_exports_without_overwriting(io_dirs):
    _, exp, wd = io_dirs
    (wd / "out.mp4").write_bytes(b"edited")
    plugin = ShellPlugin(sandbox=_MockSandbox(_result()))
    args = {"command": "x", "output_files": ["out.mp4"]}
    first = json.loads((await plugin.call_tool("run_command", args)).content)
    second = json.loads((await plugin.call_tool("run_command", args)).content)
    assert first["exported"] == [str(exp / "out.mp4")]
    assert second["exported"] == [str(exp / "out (1).mp4")]
    assert (exp / "out.mp4").read_bytes() == b"edited"


async def test_output_files_refuses_escape_from_workdir(io_dirs, tmp_path):
    (tmp_path / "outside.txt").write_text("x")
    plugin = ShellPlugin(sandbox=_MockSandbox(_result()))
    tr = await plugin.call_tool(
        "run_command", {"command": "x", "output_files": ["../outside.txt", "missing.txt"]},
    )
    payload = json.loads(tr.content)
    assert payload["exported"] == []
    assert len(payload["export_errors"]) == 2


# ---------------------------------------------------------------------------
# env scrub tests -- Windows-only, real WindowsSandbox
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_env_scrub_secrets_absent_no_ac(tmp_path):
    """SBX-3: Job-Object-only child env has no parent secrets; PATH+TEMP present."""
    import os
    from cerebral.sandbox import WindowsSandbox

    os.environ["OPENAI_API_KEY"] = "test_secret_123"
    os.environ["DISCORD_USER_TOKEN"] = "test_token_456"
    try:
        sb = WindowsSandbox(timeout_s=15, use_appcontainer=False)
        result = sb.spawn(
            [sys.executable, "-c",
             "import os, json; print(json.dumps(dict(os.environ)))"],
            str(tmp_path),
        )
        assert result.exit_code == 0, f"stderr: {result.stderr!r}"
        child_env = json.loads(result.stdout.strip())
        assert "OPENAI_API_KEY" not in child_env
        assert "DISCORD_USER_TOKEN" not in child_env
        assert "PATH" in child_env
        assert "TEMP" in child_env or "TMP" in child_env
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("DISCORD_USER_TOKEN", None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_env_scrub_secrets_absent_appcontainer(ac_workdir):
    """SBX-3: AppContainer child env scrubbed -- no secrets, PATH present."""
    import os
    from cerebral.sandbox import WindowsSandbox

    os.environ["OPENAI_API_KEY"] = "test_secret_123"
    try:
        sb = WindowsSandbox(timeout_s=20, use_appcontainer=True)
        # PowerShell 5.1: emit ABSENT/PRESENT markers, no null-coalescing operator
        script = (
            "if ($null -eq $env:OPENAI_API_KEY -or $env:OPENAI_API_KEY -eq '') "
            "{ Write-Output 'OPENAI_KEY=ABSENT' } else { Write-Output 'OPENAI_KEY=PRESENT' }; "
            "if ($null -ne $env:PATH -and $env:PATH -ne '') "
            "{ Write-Output 'PATH_PRESENT=True' } else { Write-Output 'PATH_PRESENT=False' }"
        )
        result = sb.spawn(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            ac_workdir,
        )
        assert result.exit_code == 0, f"stderr: {result.stderr!r}"
        assert "OPENAI_KEY=ABSENT" in result.stdout
        assert "PATH_PRESENT=True" in result.stdout
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
