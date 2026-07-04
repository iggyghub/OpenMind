"""Unit tests for the Windows sandbox (SBX-1 Job Object + SBX-2 AppContainer, ADR-0010).

Windows-only; skipped on other platforms.
- SBX-1 tests use use_appcontainer=False (Job Object only; sys.executable safe).
- SBX-2 tests use use_appcontainer=True with PowerShell/cmd children (System32,
  accessible inside AppContainer via ALL_APPLICATION_PACKAGES).
- AppContainer workdirs live under C:\\Users\\Public (ALL_APPLICATION_PACKAGES traverse
  on the full parent chain, so the container can set CWD without ACL modification).
- No test runs a real 120-second wall clock -- timeout_s is injectable.
- Cleanup: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE terminates all children on job close;
  AppContainer profiles are deleted in the finally block of _run_appcontainer;
  ac_workdir fixture removes its directory after each test.
"""
import os
import re
import shutil
import sys
import textwrap
import uuid

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")

from cerebral.sandbox import SandboxResult, Sandbox, WindowsSandbox  # noqa: E402
from cerebral.sandbox._interface import Sandbox as SandboxABC         # noqa: E402
from cerebral.sandbox._windows import (                                # noqa: E402
    _ac_create_sid, _ac_delete_profile, _ac_sid_to_str,
    _ensure_ac_libs,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sb(**kw):
    """SBX-1 sandbox (Job Object only, no AppContainer)."""
    return WindowsSandbox(
        timeout_s=kw.pop("timeout_s", 10),
        max_procs=kw.pop("max_procs", 32),
        max_commit_bytes=kw.pop("max_commit_bytes", 1 * 1024 ** 3),
        use_appcontainer=False,
        **kw,
    )


def _sb_ac(**kw):
    """SBX-2 sandbox (AppContainer + Job Object)."""
    return WindowsSandbox(
        timeout_s=kw.pop("timeout_s", 15),
        max_procs=kw.pop("max_procs", 32),
        max_commit_bytes=kw.pop("max_commit_bytes", 1 * 1024 ** 3),
        use_appcontainer=True,
        **kw,
    )


@pytest.fixture
def ac_workdir():
    """Workdir under C:\\Users\\Public so AppContainer can traverse via ALL_APPLICATION_PACKAGES.

    C:\\Users\\Public has ALL_APPLICATION_PACKAGES read+execute (traverse), so the full
    path chain to the workdir is accessible without modifying any parent dir ACLs.
    Users can create subdirectories here (Public has Users: Modify).
    """
    path = os.path.join(r"C:\Users\Public\OpenMind-sbx-test", uuid.uuid4().hex[:8])
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _ps(script: str) -> list[str]:
    """Build a PowerShell command list from an inline script."""
    return [
        "powershell.exe",
        "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-Command", script,
    ]

# ---------------------------------------------------------------------------
# interface contract
# ---------------------------------------------------------------------------

def test_sandbox_result_is_dataclass():
    r = SandboxResult(stdout="a", stderr="b", exit_code=0)
    assert r.killed_reason is None


def test_windows_sandbox_implements_abstract():
    assert issubclass(WindowsSandbox, SandboxABC)

# ---------------------------------------------------------------------------
# SBX-1: normal execution (no AppContainer)
# ---------------------------------------------------------------------------

def test_echo_returns_stdout(tmp_path):
    result = _sb().spawn(["cmd", "/c", "echo hi"], str(tmp_path))
    assert result.exit_code == 0
    assert "hi" in result.stdout
    assert result.killed_reason is None


def test_nonzero_exit_code(tmp_path):
    result = _sb().spawn(["cmd", "/c", "exit 42"], str(tmp_path))
    assert result.exit_code == 42
    assert result.killed_reason is None


def test_wall_clock_kill(tmp_path):
    sb = WindowsSandbox(timeout_s=2, max_procs=32,
                        max_commit_bytes=1 * 1024 ** 3, use_appcontainer=False)
    result = sb.spawn(["ping", "-n", "300", "127.0.0.1"], str(tmp_path))
    assert result.killed_reason == "wall_clock"
    assert result.exit_code == -1


def test_process_cap_blocks_excess_spawns(tmp_path):
    script = textwrap.dedent("""\
        import subprocess, sys
        ok = fail = 0
        for _ in range(20):
            try:
                subprocess.Popen(
                    ["ping", "-n", "300", "127.0.0.1"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                ok += 1
            except OSError:
                fail += 1
        print(f"ok={ok} fail={fail}")
    """)
    result = _sb(timeout_s=10, max_procs=3).spawn(
        [sys.executable, "-c", script], str(tmp_path),
    )
    m = re.search(r"fail=(\d+)", result.stdout)
    assert m and int(m.group(1)) > 0, f"expected blocked spawns; got: {result.stdout!r}"


def test_stdout_truncation(tmp_path):
    result = _sb().spawn(
        [sys.executable, "-c", "print('x' * 40_000)"], str(tmp_path),
    )
    assert result.exit_code == 0
    assert result.stdout.endswith("[truncated]")
    assert len(result.stdout) <= 30_000 + len("\n[truncated]")


def test_short_output_not_truncated(tmp_path):
    result = _sb().spawn([sys.executable, "-c", "print('hello')"], str(tmp_path))
    assert "[truncated]" not in result.stdout
    assert "hello" in result.stdout

# ---------------------------------------------------------------------------
# SBX-2: AppContainer basic sanity
# ---------------------------------------------------------------------------

def test_appcontainer_echo_succeeds(ac_workdir):
    """AppContainer launches and a simple cmd runs to completion."""
    result = _sb_ac().spawn(
        ["cmd", "/c", "echo appcontainer-ok"], ac_workdir,
    )
    assert result.exit_code == 0, f"stderr: {result.stderr!r}"
    assert "appcontainer-ok" in result.stdout

# ---------------------------------------------------------------------------
# SBX-2: workdir kernel ACL
# ---------------------------------------------------------------------------

def test_appcontainer_workdir_write_allowed(ac_workdir):
    """Child can write inside the granted workdir."""
    # Use absolute path: AppContainer PowerShell CWD is C:\ not the workdir
    target = ac_workdir.replace("\\", "\\\\") + "\\\\acl_test.txt"
    script = f"[System.IO.File]::WriteAllText('{target}', 'x')"
    result = _sb_ac().spawn(_ps(script), ac_workdir)
    assert result.exit_code == 0, (
        f"Write inside workdir failed.\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert os.path.exists(os.path.join(ac_workdir, "acl_test.txt")), "File not created in workdir"


def test_appcontainer_parent_write_denied(ac_workdir):
    """Child cannot write to the parent directory (no ACE for AppContainer SID there)."""
    # ac_workdir = C:\Users\Public\OpenMind-sbx-test\<uuid>
    # parent    = C:\Users\Public\OpenMind-sbx-test\  (ALL_APPLICATION_PACKAGES read-only)
    script = (
        "try { "
        "  New-Item -Path '..\\forbidden_parent.txt' -Value 'x' -ItemType File "
        "    -Force -ErrorAction Stop | Out-Null; exit 0 "
        "} catch { exit 1 }"
    )
    result = _sb_ac().spawn(_ps(script), ac_workdir)
    assert result.exit_code == 1, (
        f"Expected write to parent dir to be denied, got exit {result.exit_code}.\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    parent = os.path.dirname(ac_workdir)
    assert not os.path.exists(os.path.join(parent, "forbidden_parent.txt"))


def test_appcontainer_dotdot_write_denied(ac_workdir):
    """'..' traversal does not bypass the kernel ACL (not string matching)."""
    script = (
        "try { "
        "  [System.IO.File]::WriteAllText("
        "    [System.IO.Path]::GetFullPath((Join-Path (Get-Location) '..\\dotdot_evil.txt')), 'x'"
        "  ); exit 0 "
        "} catch { exit 1 }"
    )
    result = _sb_ac().spawn(_ps(script), ac_workdir)
    assert result.exit_code == 1, (
        f"Expected '..' write to be denied, got exit {result.exit_code}.\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    parent = os.path.dirname(ac_workdir)
    assert not os.path.exists(os.path.join(parent, "dotdot_evil.txt"))


def test_appcontainer_short_name_write_denied(ac_workdir):
    """8.3 short-name form of the parent dir is also denied (proves no string-match bypass)."""
    import ctypes as ct
    parent = os.path.dirname(ac_workdir)
    buf = ct.create_unicode_buffer(512)
    ct.windll.kernel32.GetShortPathNameW(parent, buf, 512)
    short_parent = buf.value
    if short_parent == parent:
        pytest.skip("8.3 names not enabled on this volume")

    script = (
        f"try {{ "
        f"  [System.IO.File]::WriteAllText('{short_parent}\\\\short83_evil.txt', 'x'); exit 0 "
        f"}} catch {{ exit 1 }}"
    )
    result = _sb_ac().spawn(_ps(script), ac_workdir)
    assert result.exit_code == 1, (
        f"Expected 8.3-path write to be denied, got exit {result.exit_code}.\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )

# ---------------------------------------------------------------------------
# SBX-2: network denial
# ---------------------------------------------------------------------------

def test_appcontainer_network_denied(ac_workdir):
    """
    AppContainer with no network capabilities cannot reach private/LAN addresses.
    We connect to the machine's own LAN IP on port 1 (no listener):
    - Network denied (AppContainer, no privateNetworkClientServer) -> SOCK_ERR 10013 (WSAEACCES)
    - Network allowed (regular process, no listener) -> SOCK_ERR 10061 (WSAECONNREFUSED)
    Loopback (127.x) is exempted from AppContainer network isolation on Windows 10,
    so we use the machine's non-loopback IPv4 address instead.
    """
    import socket as _socket
    import subprocess as _sp

    fw = _sp.run(["sc", "query", "mpssvc"], capture_output=True, text=True)
    if "RUNNING" not in fw.stdout:
        pytest.skip("Windows Defender Firewall (mpssvc) not running — WFP won't enforce AppContainer network isolation")

    # Use the default gateway: it is a private IP that is NOT the machine's own IP.
    # Connecting to the machine's own IP goes through loopback internally (which is
    # exempt from AppContainer isolation), so we must use a different private target.
    try:
        route = _sp.run(["route", "print", "0.0.0.0"], capture_output=True, text=True)
        gw_ips = []
        for line in route.stdout.splitlines():
            parts = line.split()
            # route print output: Network  Netmask  Gateway  Interface  Metric
            if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                gw = parts[2]
                if not gw.startswith("127."):
                    gw_ips.append(gw)
    except OSError:
        gw_ips = []
    if not gw_ips:
        pytest.skip("No default gateway found — cannot resolve a non-self private IP for AppContainer network test")
    target_ip = gw_ips[0]

    script = (
        f"try {{ "
        f"  $t = New-Object System.Net.Sockets.TcpClient; "
        f"  $t.Connect('{target_ip}', 1); Write-Output 'CONNECTED' "
        f"}} catch [System.Net.Sockets.SocketException] {{ "
        f"  Write-Output ('SOCK_ERR ' + [int]$_.Exception.SocketErrorCode) "
        f"}} catch {{ "
        f"  Write-Output ('OTHER_ERR ' + $_.Exception.Message) "
        f"}}"
    )
    result = _sb_ac(timeout_s=20).spawn(_ps(script), ac_workdir)
    output = result.stdout.strip()
    # WSAEACCES (10013) = OS denied due to AppContainer network policy
    # WSAECONNREFUSED (10061) = OS accepted socket but port not listening (network NOT blocked)
    assert "SOCK_ERR 10013" in output, (
        f"Expected WSAEACCES (10013) connecting to {target_ip}:1 inside AppContainer.\n"
        f"Got: {output!r}\nstderr: {result.stderr!r}"
    )

# ---------------------------------------------------------------------------
# SBX-2: Job Object caps still compose with AppContainer
# ---------------------------------------------------------------------------

def test_appcontainer_wall_clock_kill(ac_workdir):
    """Wall-clock timeout fires inside AppContainer (SBX-1 cap composes with SBX-2)."""
    sb = WindowsSandbox(
        timeout_s=2, max_procs=32,
        max_commit_bytes=1 * 1024 ** 3,
        use_appcontainer=True,
    )
    # Start-Sleep doesn't need network or file access -- safe for AppContainer
    result = sb.spawn(_ps("Start-Sleep -Seconds 300"), ac_workdir)
    assert result.killed_reason == "wall_clock"
    assert result.exit_code == -1

# ---------------------------------------------------------------------------
# SBX-2: AppContainer profile cleanup
# ---------------------------------------------------------------------------

def test_appcontainer_profile_deleted_after_spawn(ac_workdir):
    """After spawn() completes, CreateAppContainerProfile with the same name returns S_OK
    (not ALREADY_EXISTS=0x800700B7), proving _run_appcontainer deleted the profile."""
    import uuid as _uuid
    import ctypes as ct

    fixed_hex   = "deadbeef12345678"
    fixed_name  = f"openmind-sbx-{fixed_hex[:8]}"  # "openmind-sbx-deadbeef"

    # Ensure no leftover from a prior failed run
    _ensure_ac_libs()
    from cerebral.sandbox._windows import _userenv_dll, _advapi32_dll, _S_OK, _HRESULT_ALREADY_EXISTS

    _userenv_dll.DeleteAppContainerProfile(fixed_name)

    class _FakeUUID:
        hex = fixed_hex

    _uuid4_orig = _uuid.uuid4
    _uuid.uuid4 = lambda: _FakeUUID()  # type: ignore[assignment]
    try:
        _sb_ac().spawn(["cmd", "/c", "echo done"], ac_workdir)
    finally:
        _uuid.uuid4 = _uuid4_orig

    # Re-create the profile: S_OK means the previous profile WAS deleted; ALREADY_EXISTS means it wasn't.
    sid2 = ct.c_void_p()
    hr2  = _userenv_dll.CreateAppContainerProfile(
        fixed_name, fixed_name, fixed_name, None, 0, ct.byref(sid2),
    )
    # Always clean up whatever we just created (or leave-behind)
    _userenv_dll.DeleteAppContainerProfile(fixed_name)
    if sid2.value:
        _advapi32_dll.FreeSid(sid2)

    assert hr2 == _S_OK, (
        f"Profile '{fixed_name}' still existed after spawn (cleanup failed); "
        f"CreateAppContainerProfile returned HRESULT={hr2 & 0xFFFFFFFF:#010x} "
        f"(expected S_OK=0 for a freshly-deleted slot)"
    )
