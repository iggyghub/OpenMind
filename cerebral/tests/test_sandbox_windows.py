"""Unit tests for the Windows Job Object sandbox (SBX-1, ADR-0010).

Windows-only; skipped on other platforms.  Uses injectable caps so no test
runs a real 120-second wall clock.  Cleanup is automatic: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
terminates every child in the job tree when spawn() returns.
"""
import re
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")

from cerebral.sandbox import SandboxResult, Sandbox, WindowsSandbox  # noqa: E402
from cerebral.sandbox._interface import Sandbox as SandboxABC  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sb(timeout_s=10, max_procs=32, max_commit_bytes=1 * 1024 ** 3):
    return WindowsSandbox(timeout_s=timeout_s, max_procs=max_procs,
                          max_commit_bytes=max_commit_bytes)


# ---------------------------------------------------------------------------
# interface contract
# ---------------------------------------------------------------------------

def test_sandbox_result_is_dataclass():
    r = SandboxResult(stdout="a", stderr="b", exit_code=0)
    assert r.killed_reason is None


def test_windows_sandbox_implements_abstract():
    assert issubclass(WindowsSandbox, SandboxABC)


# ---------------------------------------------------------------------------
# normal execution
# ---------------------------------------------------------------------------

def test_echo_returns_stdout(tmp_path):
    result = _sb().spawn(["cmd", "/c", "echo hi"], str(tmp_path))
    assert result.exit_code == 0
    assert "hi" in result.stdout
    assert result.killed_reason is None
    assert result.stderr == ""


def test_nonzero_exit_code(tmp_path):
    result = _sb().spawn(["cmd", "/c", "exit 42"], str(tmp_path))
    assert result.exit_code == 42
    assert result.killed_reason is None


# ---------------------------------------------------------------------------
# wall-clock timeout (injected 2 s so the test is fast)
# ---------------------------------------------------------------------------

def test_wall_clock_kill(tmp_path):
    sb = WindowsSandbox(timeout_s=2, max_procs=32, max_commit_bytes=1 * 1024 ** 3)
    # ping -n 300 runs for ~300 s; 2 s timeout must fire first
    result = sb.spawn(["ping", "-n", "300", "127.0.0.1"], str(tmp_path))
    assert result.killed_reason == "wall_clock"
    assert result.exit_code == -1


# ---------------------------------------------------------------------------
# process cap (injected max_procs=3 so only python + 2 children fit)
# ---------------------------------------------------------------------------

def test_process_cap_blocks_excess_spawns(tmp_path):
    # max_procs=3: python.exe counts as 1; only 2 ping.exe children fit.
    sb = WindowsSandbox(timeout_s=10, max_procs=3, max_commit_bytes=1 * 1024 ** 3)
    script = textwrap.dedent("""\
        import subprocess, sys
        ok = 0
        fail = 0
        for _ in range(20):
            try:
                subprocess.Popen(
                    ["ping", "-n", "300", "127.0.0.1"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                ok += 1
            except OSError:
                fail += 1
        print(f"ok={ok} fail={fail}")
    """)
    result = _sb(timeout_s=10, max_procs=3).spawn(
        [sys.executable, "-c", script], str(tmp_path)
    )
    # some spawn attempts must have been blocked
    m = re.search(r"fail=(\d+)", result.stdout)
    assert m and int(m.group(1)) > 0, f"expected blocked spawns; got: {result.stdout!r}"


# ---------------------------------------------------------------------------
# output truncation at ~30 k chars
# ---------------------------------------------------------------------------

def test_stdout_truncation(tmp_path):
    # generate 40 k 'x' chars — must be truncated with the marker
    result = _sb().spawn(
        [sys.executable, "-c", "print('x' * 40_000)"],
        str(tmp_path),
    )
    assert result.exit_code == 0
    assert result.stdout.endswith("[truncated]")
    assert len(result.stdout) <= 30_000 + len("\n[truncated]")


def test_short_output_not_truncated(tmp_path):
    result = _sb().spawn(
        [sys.executable, "-c", "print('hello')"],
        str(tmp_path),
    )
    assert "[truncated]" not in result.stdout
    assert "hello" in result.stdout
