"""Windows Job Object + AppContainer sandbox backend (ADR-0010).

SBX-1: Job Object caps (1 GB commit / 32 procs / 120 s wall-clock).
SBX-2: AppContainer network-deny + per-profile workdir kernel ACL.
SBX-3: env scrub + shell_exec wiring (default use_appcontainer=True).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import threading
import uuid
from typing import Optional

from cerebral.sandbox._interface import Sandbox, SandboxResult

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
JOB_OBJECT_LIMIT_ACTIVE_PROCESS             = 0x00000008
JOB_OBJECT_LIMIT_JOB_MEMORY                 = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE          = 0x00002000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JobObjectExtendedLimitInformation           = 9

CREATE_SUSPENDED            = 0x00000004
CREATE_NO_WINDOW            = 0x08000000
CREATE_UNICODE_ENVIRONMENT  = 0x00000400

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES         = 0x00000100
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009

# ---------------------------------------------------------------------------
# Env scrub (SBX-3) — child receives only these vars, no secrets
# ---------------------------------------------------------------------------
# LOCALAPPDATA is required: AppContainer setup resolves the container profile path via this var.
_SCRUBBED_ENV_KEYS = frozenset({"PATH", "TEMP", "TMP", "SystemRoot", "WINDIR", "LOCALAPPDATA"})


def _build_scrubbed_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _SCRUBBED_ENV_KEYS if k in os.environ}


def _env_dict_to_block(env: dict[str, str]) -> ctypes.Array:
    """Build double-null-terminated WCHAR env block for CreateProcessW lpEnvironment."""
    s = "".join(f"{k}={v}\0" for k, v in env.items()) + "\0"
    return (ctypes.c_wchar * len(s))(*s)

# HRESULT (signed int32)
_S_OK                   = 0
_HRESULT_ALREADY_EXISTS = -2146434889   # 0x800700B7 = HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)

_TRUNCATE_AT     = 30_000
_TRUNCATE_MARKER = "\n[truncated]"

_DEFAULT_TIMEOUT_S   = 120.0
_DEFAULT_MAX_PROCS   = 32
_DEFAULT_MAX_COMMIT  = 1 * 1024 * 1024 * 1024  # 1 GB

# ---------------------------------------------------------------------------
# ctypes structs -- Job Object
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

# ---------------------------------------------------------------------------
# ctypes structs -- AppContainer / STARTUPINFOEX
# ---------------------------------------------------------------------------
class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities",    ctypes.c_void_p),
        ("CapabilityCount", wt.DWORD),
        ("Reserved",        wt.DWORD),
    ]

class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb",             wt.DWORD),
        ("lpReserved",     wt.LPWSTR),
        ("lpDesktop",      wt.LPWSTR),
        ("lpTitle",        wt.LPWSTR),
        ("dwX",            wt.DWORD), ("dwY",            wt.DWORD),
        ("dwXSize",        wt.DWORD), ("dwYSize",        wt.DWORD),
        ("dwXCountChars",  wt.DWORD), ("dwYCountChars",  wt.DWORD),
        ("dwFillAttribute",wt.DWORD),
        ("dwFlags",        wt.DWORD),
        ("wShowWindow",    wt.WORD),
        ("cbReserved2",    wt.WORD),
        ("lpReserved2",    ctypes.c_char_p),
        ("hStdInput",      wt.HANDLE),
        ("hStdOutput",     wt.HANDLE),
        ("hStdError",      wt.HANDLE),
    ]

class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo",     _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]

class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess",    wt.HANDLE),
        ("hThread",     wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId",  wt.DWORD),
    ]

class _SECURITY_ATTRIBUTES_SA(ctypes.Structure):
    _fields_ = [
        ("nLength",              wt.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle",       wt.BOOL),
    ]

# ---------------------------------------------------------------------------
# Job Object helpers
# ---------------------------------------------------------------------------
def _apply_limits(job_handle, max_procs: int, max_commit_bytes: int) -> None:
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = (
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | JOB_OBJECT_LIMIT_JOB_MEMORY
        | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    info.BasicLimitInformation.LimitFlags        = flags
    info.BasicLimitInformation.ActiveProcessLimit = max_procs
    info.JobMemoryLimit                           = max_commit_bytes
    ok = ctypes.windll.kernel32.SetInformationJobObject(
        int(job_handle), JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(f"SetInformationJobObject failed: {ctypes.GetLastError()}")


def _resume_process(pid: int) -> None:
    """Resume all threads via NtResumeProcess (used for the non-AppContainer path)."""
    import win32api, win32con
    h = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, pid)
    try:
        ntdll = ctypes.windll.ntdll
        ntdll.NtResumeProcess.restype  = ctypes.c_long
        ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
        status = ntdll.NtResumeProcess(int(h))
        if status != 0:
            raise OSError(f"NtResumeProcess NTSTATUS={status:#010x}")
    finally:
        win32api.CloseHandle(h)

# ---------------------------------------------------------------------------
# AppContainer helpers (SBX-2)
# ---------------------------------------------------------------------------
_userenv_dll  = None
_advapi32_dll = None


def _ensure_ac_libs() -> None:
    global _userenv_dll, _advapi32_dll
    if _userenv_dll is not None:
        return
    u = ctypes.windll.userenv
    a = ctypes.windll.advapi32
    k = ctypes.windll.kernel32

    u.CreateAppContainerProfile.restype  = ctypes.c_long
    u.CreateAppContainerProfile.argtypes = [
        wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR,
        ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    u.DeriveAppContainerSidFromAppContainerName.restype  = ctypes.c_long
    u.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
    ]
    u.DeleteAppContainerProfile.restype  = ctypes.c_long
    u.DeleteAppContainerProfile.argtypes = [wt.LPCWSTR]

    a.ConvertSidToStringSidW.restype  = wt.BOOL
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    a.FreeSid.restype  = ctypes.c_void_p
    a.FreeSid.argtypes = [ctypes.c_void_p]

    k.CreateProcessW.restype = wt.BOOL
    k.CreateProcessW.argtypes = [
        wt.LPCWSTR, wt.LPWSTR,
        ctypes.c_void_p, ctypes.c_void_p,
        wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    k.CreatePipe.argtypes = [
        ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
        ctypes.c_void_p, wt.DWORD,
    ]
    k.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
    k.CloseHandle.argtypes    = [wt.HANDLE]
    k.ResumeThread.restype    = wt.DWORD
    k.ResumeThread.argtypes   = [wt.HANDLE]
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    k.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    k.ReadFile.argtypes = [
        wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
    ]

    _userenv_dll  = u
    _advapi32_dll = a


def _ac_create_sid(name: str) -> ctypes.c_void_p:
    """Create (or derive) AppContainer profile; return SID. Free with _ac_free_sid."""
    _ensure_ac_libs()
    sid = ctypes.c_void_p()
    hr = _userenv_dll.CreateAppContainerProfile(
        name, name, name, None, 0, ctypes.byref(sid),
    )
    if hr == _HRESULT_ALREADY_EXISTS:
        hr = _userenv_dll.DeriveAppContainerSidFromAppContainerName(
            name, ctypes.byref(sid),
        )
    if hr != _S_OK:
        raise OSError(f"AppContainer create failed: HRESULT={hr & 0xFFFFFFFF:#010x}")
    return sid


def _ac_delete_profile(name: str) -> None:
    _ensure_ac_libs()
    _userenv_dll.DeleteAppContainerProfile(name)


def _ac_free_sid(sid: ctypes.c_void_p) -> None:
    if sid and sid.value:
        _advapi32_dll.FreeSid(sid)


def _ac_sid_to_str(sid: ctypes.c_void_p) -> str:
    _ensure_ac_libs()
    str_addr = ctypes.c_void_p()
    if not _advapi32_dll.ConvertSidToStringSidW(sid, ctypes.byref(str_addr)):
        raise OSError(f"ConvertSidToStringSidW: {ctypes.GetLastError()}")
    result = ctypes.cast(str_addr, ctypes.c_wchar_p).value
    ctypes.windll.kernel32.LocalFree(str_addr)
    return result


def _ac_grant_workdir(workdir: str, sid: ctypes.c_void_p) -> None:
    """Add read/write/execute ACE (inheritable) for the AppContainer SID on workdir."""
    import win32security
    import ntsecuritycon as ntc

    pysid = win32security.ConvertStringSidToSid(_ac_sid_to_str(sid))
    sd = win32security.GetFileSecurity(workdir, win32security.DACL_SECURITY_INFORMATION)
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        dacl = win32security.ACL()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION,
        ntc.CONTAINER_INHERIT_ACE | ntc.OBJECT_INHERIT_ACE,
        ntc.FILE_GENERIC_READ | ntc.FILE_GENERIC_WRITE | ntc.FILE_GENERIC_EXECUTE,
        pysid,
    )
    sd.SetSecurityDescriptorDacl(True, dacl, False)
    win32security.SetFileSecurity(workdir, win32security.DACL_SECURITY_INFORMATION, sd)


def _ac_build_attr_list(sec_caps: _SECURITY_CAPABILITIES):
    """Return (attr_ptr, backing_buf). Keep backing_buf alive while attr_ptr is in use."""
    k = ctypes.windll.kernel32
    size = ctypes.c_size_t(0)
    # First call determines required buffer size (returns FALSE -- expected)
    k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    if size.value == 0:
        raise OSError(f"InitializeProcThreadAttributeList size query failed: {ctypes.GetLastError()}")
    buf = (ctypes.c_byte * size.value)()
    attr_ptr = ctypes.cast(buf, ctypes.c_void_p)
    if not k.InitializeProcThreadAttributeList(attr_ptr, 1, 0, ctypes.byref(size)):
        raise OSError(f"InitializeProcThreadAttributeList: {ctypes.GetLastError()}")
    if not k.UpdateProcThreadAttribute(
        attr_ptr, 0,
        PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
        ctypes.byref(sec_caps), ctypes.sizeof(sec_caps),
        None, None,
    ):
        k.DeleteProcThreadAttributeList(attr_ptr)
        raise OSError(f"UpdateProcThreadAttribute: {ctypes.GetLastError()}")
    return attr_ptr, buf


def _ac_make_pipe():
    """Return (read_int, write_int); write end is inheritable, read end is not."""
    k = ctypes.windll.kernel32
    sa = _SECURITY_ATTRIBUTES_SA()
    sa.nLength        = ctypes.sizeof(sa)
    sa.bInheritHandle = True
    r, w = wt.HANDLE(), wt.HANDLE()
    if not k.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
        raise OSError(f"CreatePipe: {ctypes.GetLastError()}")
    k.SetHandleInformation(r, 1, 0)   # clear HANDLE_FLAG_INHERIT on read end
    return int(r.value), int(w.value)


def _ac_read_pipe(handle_int: int) -> bytes:
    """Drain a pipe read handle to EOF (call from a dedicated thread)."""
    k   = ctypes.windll.kernel32
    out: list[bytes] = []
    buf = ctypes.create_string_buffer(8192)
    n   = wt.DWORD()
    while True:
        ok = k.ReadFile(handle_int, buf, ctypes.sizeof(buf) - 1, ctypes.byref(n), None)
        if ok and n.value > 0:
            out.append(bytes(buf.raw[: n.value]))
        else:
            if ctypes.GetLastError() == 109:  # ERROR_BROKEN_PIPE -- write end gone
                break
            if not ok:
                break
    return b"".join(out)


def _ac_spawn(
    cmd: list[str],
    workdir: str,
    sec_caps: _SECURITY_CAPABILITIES,
    extra_flags: int,
    env_block=None,
) -> tuple:
    """
    Launch cmd inside an AppContainer (CREATE_SUSPENDED + EXTENDED_STARTUPINFO_PRESENT).
    Returns (proc_handle, thread_handle, pid, stdout_read, stderr_read) -- all ints.
    Caller is responsible for closing all five handles.
    sec_caps must stay alive until this function returns.
    """
    k = ctypes.windll.kernel32

    stdout_r, stdout_w = _ac_make_pipe()
    stderr_r, stderr_w = _ac_make_pipe()

    attr_ptr, attr_buf = _ac_build_attr_list(sec_caps)

    try:
        si_ex = _STARTUPINFOEXW()
        si_ex.StartupInfo.cb         = ctypes.sizeof(_STARTUPINFOEXW)
        si_ex.StartupInfo.dwFlags    = STARTF_USESTDHANDLES
        si_ex.StartupInfo.hStdInput  = 0
        si_ex.StartupInfo.hStdOutput = stdout_w
        si_ex.StartupInfo.hStdError  = stderr_w
        si_ex.lpAttributeList        = attr_ptr.value

        pi      = _PROCESS_INFORMATION()
        cmd_buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd))
        flags   = extra_flags | EXTENDED_STARTUPINFO_PRESENT
        if env_block is not None:
            flags |= CREATE_UNICODE_ENVIRONMENT

        ok = k.CreateProcessW(
            None, cmd_buf,
            None, None,
            True,   # bInheritHandles
            flags,
            env_block,
            workdir,
            ctypes.byref(si_ex),
            ctypes.byref(pi),
        )
        if not ok:
            raise OSError(f"CreateProcessW (AppContainer): error={ctypes.GetLastError()}")

        return (
            int(pi.hProcess), int(pi.hThread), int(pi.dwProcessId),
            stdout_r, stderr_r,
        )
    finally:
        k.DeleteProcThreadAttributeList(attr_ptr)
        k.CloseHandle(stdout_w)
        k.CloseHandle(stderr_w)

# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------
class WindowsSandbox(Sandbox):
    """Job Object + AppContainer sandbox (ADR-0010).

    use_appcontainer=True (default): Job Object + AppContainer network-deny + workdir ACL + env scrub.
    use_appcontainer=False: Job Object only + env scrub (no network isolation; tests only).
    """

    def __init__(
        self,
        *,
        timeout_s:        float = _DEFAULT_TIMEOUT_S,
        max_procs:        int   = _DEFAULT_MAX_PROCS,
        max_commit_bytes: int   = _DEFAULT_MAX_COMMIT,
        use_appcontainer: bool  = True,
    ) -> None:
        self._timeout_s        = timeout_s
        self._max_procs        = max_procs
        self._max_commit_bytes = max_commit_bytes
        self._use_appcontainer = use_appcontainer

    def spawn(
        self,
        cmd: list[str],
        workdir: str,
        *,
        timeout_s: Optional[float] = None,
    ) -> SandboxResult:
        import win32api, win32job

        effective_timeout = timeout_s if timeout_s is not None else self._timeout_s
        scrubbed = _build_scrubbed_env()
        job = win32job.CreateJobObject(None, "")
        try:
            _apply_limits(job, self._max_procs, self._max_commit_bytes)
            if self._use_appcontainer:
                return self._run_appcontainer(cmd, workdir, job, effective_timeout, scrubbed)
            return self._run(cmd, workdir, job, effective_timeout, scrubbed)
        finally:
            win32api.CloseHandle(job)

    # ------------------------------------------------------------------ SBX-1
    def _run(self, cmd, workdir, job, timeout_s: float, env: dict) -> SandboxResult:
        import win32api, win32job, win32con

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=workdir,
            env=env,
            creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW,
        )

        try:
            h = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, proc.pid)
            try:
                win32job.AssignProcessToJobObject(job, h)
            finally:
                win32api.CloseHandle(h)
        except Exception as exc:
            proc.kill(); proc.wait()
            raise RuntimeError(f"AssignProcessToJobObject failed: {exc}") from exc

        _resume_process(proc.pid)

        killed: list[bool] = [False]

        def _kill():
            killed[0] = True
            try:
                proc.kill()
            except OSError:
                pass

        timer = threading.Timer(timeout_s, _kill)
        timer.start()
        try:
            out, err = proc.communicate()
        finally:
            timer.cancel()

        return SandboxResult(
            stdout=_maybe_truncate(out.decode("utf-8", errors="replace")),
            stderr=_maybe_truncate(err.decode("utf-8", errors="replace")),
            exit_code=proc.returncode if not killed[0] else -1,
            killed_reason="wall_clock" if killed[0] else None,
        )

    # ------------------------------------------------------------------ SBX-2/3
    def _run_appcontainer(self, cmd, workdir, job, timeout_s: float, env: dict) -> SandboxResult:
        import win32api, win32job, win32con

        # Unique profile name: <=64 chars, alphanumeric + hyphens
        profile_name = f"openmind-sbx-{uuid.uuid4().hex[:8]}"

        _ensure_ac_libs()
        sid = _ac_create_sid(profile_name)
        try:
            # ponytail: caller must ensure workdir parent chain is AppContainer-traversable
            # (e.g. under C:\ProgramData or C:\Users\Public — not in %AppData%)
            _ac_grant_workdir(workdir, sid)

            sec_caps = _SECURITY_CAPABILITIES()
            sec_caps.AppContainerSid = sid.value   # no network caps == deny by OS
            sec_caps.Capabilities    = None
            sec_caps.CapabilityCount = 0
            sec_caps.Reserved        = 0

            env_block = _env_dict_to_block(env)
            hproc, hthread, pid, stdout_r, stderr_r = _ac_spawn(
                cmd, workdir, sec_caps,
                CREATE_SUSPENDED | CREATE_NO_WINDOW,
                env_block,
            )

            k = ctypes.windll.kernel32

            try:
                h = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, pid)
                try:
                    win32job.AssignProcessToJobObject(job, h)
                finally:
                    win32api.CloseHandle(h)
            except Exception as exc:
                k.TerminateProcess(hproc, 1)
                for hh in (hproc, hthread, stdout_r, stderr_r):
                    k.CloseHandle(hh)
                raise RuntimeError(f"AssignProcessToJobObject failed: {exc}") from exc

            k.ResumeThread(hthread)
            k.CloseHandle(hthread)

            killed: list[bool] = [False]
            out_buf: list[bytes] = [b""]
            err_buf: list[bytes] = [b""]

            def _read_out():
                out_buf[0] = _ac_read_pipe(stdout_r)

            def _read_err():
                err_buf[0] = _ac_read_pipe(stderr_r)

            def _kill_timeout():
                killed[0] = True
                k.TerminateProcess(hproc, 1)

            t_out  = threading.Thread(target=_read_out, daemon=True)
            t_err  = threading.Thread(target=_read_err, daemon=True)
            timer  = threading.Timer(timeout_s, _kill_timeout)
            t_out.start(); t_err.start(); timer.start()
            try:
                t_out.join(); t_err.join()
            finally:
                timer.cancel()

            ec_dw = wt.DWORD(259)
            k.GetExitCodeProcess(hproc, ctypes.byref(ec_dw))
            k.CloseHandle(hproc)
            k.CloseHandle(stdout_r)
            k.CloseHandle(stderr_r)

            return SandboxResult(
                stdout=_maybe_truncate(out_buf[0].decode("utf-8", errors="replace")),
                stderr=_maybe_truncate(err_buf[0].decode("utf-8", errors="replace")),
                exit_code=-1 if killed[0] else ec_dw.value,
                killed_reason="wall_clock" if killed[0] else None,
            )
        finally:
            _ac_free_sid(sid)
            _ac_delete_profile(profile_name)


def _maybe_truncate(text: str) -> str:
    if len(text) <= _TRUNCATE_AT:
        return text
    return text[:_TRUNCATE_AT] + _TRUNCATE_MARKER
