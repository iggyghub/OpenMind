"""Windows Job Object sandbox backend (ADR-0010).

Caps: 1 GB commit / 32 active procs / 120 s wall-clock (all injectable for tests).
Child spawned CREATE_SUSPENDED, assigned to Job Object, then resumed — so it
is inside the job before it executes a single instruction.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import threading
from typing import Optional

from cerebral.sandbox._interface import Sandbox, SandboxResult

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
JOB_OBJECT_LIMIT_ACTIVE_PROCESS             = 0x00000008
JOB_OBJECT_LIMIT_JOB_MEMORY                 = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE          = 0x00002000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400

JobObjectExtendedLimitInformation = 9

CREATE_SUSPENDED  = 0x00000004   # not exposed by Python's subprocess module
CREATE_NO_WINDOW  = 0x08000000

_TRUNCATE_AT     = 30_000
_TRUNCATE_MARKER = "\n[truncated]"

_DEFAULT_TIMEOUT_S   = 120.0
_DEFAULT_MAX_PROCS   = 32
_DEFAULT_MAX_COMMIT  = 1 * 1024 * 1024 * 1024  # 1 GB

# ---------------------------------------------------------------------------
# ctypes structs for SetInformationJobObject
# ---------------------------------------------------------------------------
class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount",  ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount",   ctypes.c_uint64),
        ("WriteTransferCount",  ctypes.c_uint64),
        ("OtherTransferCount",  ctypes.c_uint64),
    ]

class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit",     ctypes.c_int64),
        ("LimitFlags",              wt.DWORD),
        ("MinimumWorkingSetSize",   ctypes.c_size_t),
        ("MaximumWorkingSetSize",   ctypes.c_size_t),
        ("ActiveProcessLimit",      wt.DWORD),
        ("Affinity",                ctypes.c_size_t),
        ("PriorityClass",           wt.DWORD),
        ("SchedulingClass",         wt.DWORD),
    ]

class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo",                _IO_COUNTERS),
        ("ProcessMemoryLimit",    ctypes.c_size_t),
        ("JobMemoryLimit",        ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed",     ctypes.c_size_t),
    ]


def _apply_limits(job_handle, max_procs: int, max_commit_bytes: int) -> None:
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = (
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | JOB_OBJECT_LIMIT_JOB_MEMORY
        | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    info.BasicLimitInformation.LimitFlags      = flags
    info.BasicLimitInformation.ActiveProcessLimit = max_procs
    info.JobMemoryLimit                         = max_commit_bytes

    ok = ctypes.windll.kernel32.SetInformationJobObject(
        int(job_handle),
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(f"SetInformationJobObject failed: {ctypes.GetLastError()}")


def _resume_process(pid: int) -> None:
    """Resume all threads in the process — used after CREATE_SUSPENDED spawn.

    Python's subprocess closes the thread handle before returning, so we use
    NtResumeProcess (ntdll, available Vista+) which takes the process handle.
    """
    import win32api
    import win32con
    h = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, pid)
    try:
        ntdll = ctypes.windll.ntdll
        ntdll.NtResumeProcess.restype  = ctypes.c_long   # NTSTATUS
        ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
        status = ntdll.NtResumeProcess(int(h))
        if status != 0:
            raise OSError(f"NtResumeProcess NTSTATUS={status:#010x}")
    finally:
        win32api.CloseHandle(h)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------
class WindowsSandbox(Sandbox):
    """Job Object sandbox.  AppContainer (SBX-2) and env scrub (SBX-3) slot in
    here; spawn() signature is unchanged by those slices."""

    def __init__(
        self,
        *,
        timeout_s:        float = _DEFAULT_TIMEOUT_S,
        max_procs:        int   = _DEFAULT_MAX_PROCS,
        max_commit_bytes: int   = _DEFAULT_MAX_COMMIT,
    ) -> None:
        self._timeout_s        = timeout_s
        self._max_procs        = max_procs
        self._max_commit_bytes = max_commit_bytes

    def spawn(
        self,
        cmd: list[str],
        workdir: str,
        *,
        timeout_s: Optional[float] = None,
    ) -> SandboxResult:
        import win32api
        import win32job

        effective_timeout = timeout_s if timeout_s is not None else self._timeout_s

        job = win32job.CreateJobObject(None, "")
        try:
            _apply_limits(job, self._max_procs, self._max_commit_bytes)
            return self._run(cmd, workdir, job, effective_timeout)
        finally:
            win32api.CloseHandle(job)

    def _run(
        self,
        cmd: list[str],
        workdir: str,
        job,
        timeout_s: float,
    ) -> SandboxResult:
        import win32api
        import win32job
        import win32con

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW,
        )

        # Assign to the Job Object before resuming — no race window
        try:
            h = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, proc.pid)
            try:
                win32job.AssignProcessToJobObject(job, h)
            finally:
                win32api.CloseHandle(h)
        except Exception as exc:
            proc.kill()
            proc.wait()
            raise RuntimeError(f"AssignProcessToJobObject failed: {exc}") from exc

        _resume_process(proc.pid)

        # Use threading.Timer instead of proc.wait() in a watcher thread, so
        # proc.communicate() drains the pipes (avoiding deadlock on large output)
        killed: list[bool] = [False]

        def _kill_on_timeout():
            killed[0] = True
            try:
                proc.kill()
            except OSError:
                pass  # already dead — race between natural exit and timer

        timer = threading.Timer(timeout_s, _kill_on_timeout)
        timer.start()
        try:
            stdout_bytes, stderr_bytes = proc.communicate()
        finally:
            timer.cancel()

        stdout = _maybe_truncate(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = _maybe_truncate(stderr_bytes.decode("utf-8", errors="replace"))

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode if not killed[0] else -1,
            killed_reason="wall_clock" if killed[0] else None,
        )


def _maybe_truncate(text: str) -> str:
    if len(text) <= _TRUNCATE_AT:
        return text
    return text[:_TRUNCATE_AT] + _TRUNCATE_MARKER
