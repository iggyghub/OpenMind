"""Spike probe -- runs INSIDE Felix's second session (as the RDP alternate shell).

Proves a process in session 2 (a) is a genuinely different Windows session from
the user's session 1, and (b) reads a real UIA tree there via the landed
session_worker actuator. Writes a JSON result the orchestrator polls, then holds
the window open so the run is visible in the RDP view.

Not run directly -- scripts/spike-second-session.ps1 wires it as the RDP session's
alternate shell. See #603 (ADR-0016 isolated interactive session).
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, r"C:\OpenMind")


def _session_id() -> int:
    k32 = ctypes.windll.kernel32
    sid = ctypes.c_ulong()
    k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid))
    return sid.value


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else r"C:\OpenMind\.claude\tmp\spike\session2_result.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)

    result: dict = {
        "session_id": _session_id(),
        "user": os.environ.get("USERNAME"),
        "ok": False,
    }
    notepad = None
    try:
        from cerebral.session_worker import _make_default_actuator
        backend = _make_default_actuator()
        result["actuator"] = type(backend).__name__ if backend else None
        if backend is None:
            raise RuntimeError("no actuator (non-Windows or deps missing)")

        notepad = subprocess.Popen(["notepad.exe"])
        time.sleep(2.0)
        els = backend.read_ui("Notepad")
        result["notepad_elements"] = len(els)
        result["sample_roles"] = sorted({e.get("role") for e in els})
        result["ok"] = len(els) > 0
    except Exception as exc:
        result["error"] = repr(exc)
    finally:
        if notepad is not None:
            subprocess.run(["taskkill", "/PID", str(notepad.pid), "/F"], capture_output=True)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print("\n=== SPIKE PROBE (session 2) ===")
    print(json.dumps(result, indent=2))
    print(f"\nresult written to: {out}")
    print("\nThis window is Felix's SECOND session. Session 1 (your desktop) was not touched.")
    print("Close this RDP window to log the second session off.")
    try:
        input("Press Enter to log off session 2...")
    except Exception:
        time.sleep(8)


if __name__ == "__main__":
    main()
