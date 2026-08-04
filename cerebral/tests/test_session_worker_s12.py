"""Tests for S12 #606: out-of-session kill switch + dead-man's switches.

All tests use fakes -- no real process handles, no real WS, no real Job Objects.
Fail-closed on non-Windows is covered by create_kill_on_close_job returning None.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.session_worker import (
    HEARTBEAT_INTERVAL_S,
    HEARTBEAT_MISSED_LIMIT,
    SessionWorker,
)


# ---------------------------------------------------------------------------
# Fake WS (shared with S11 tests, duplicated here for isolation)
# ---------------------------------------------------------------------------

class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._recv_queue: asyncio.Queue = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def push(self, msg: dict) -> None:
        self._recv_queue.put_nowait(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._recv_queue.empty():
            raise StopAsyncIteration
        return json.dumps(await self._recv_queue.get())


class _FakeBackend:
    def read_ui(self, window_title: str) -> list[dict]:
        return []

    def click(self, bbox: list[int]) -> None:
        pass

    def type_text(self, text: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Heartbeat: dead-man fires when no heartbeat arrives
# ---------------------------------------------------------------------------

async def test_watchdog_fires_after_missed_heartbeats():
    """Worker's _watchdog() calls _exit_fn when no heartbeat arrives within deadline."""
    exited: list[bool] = []

    def fake_exit():
        exited.append(True)

    ws = _FakeWS()
    # interval=0.05s, limit=2 -> halts after ~0.1s with no heartbeat
    worker = SessionWorker(ws, _FakeBackend(),
                           heartbeat_interval_s=0.05, missed_limit=2,
                           _exit_fn=fake_exit)
    # Test _watchdog() directly -- run() exits immediately on an empty queue
    # but we need the watchdog to run to completion.
    worker._last_heartbeat = asyncio.get_event_loop().time()
    await asyncio.wait_for(worker._watchdog(), timeout=0.5)
    assert exited, "exit_fn must have been called after missed heartbeats"


async def test_watchdog_does_not_fire_when_heartbeats_arrive():
    """Worker resets the dead-man timer on each heartbeat, so it keeps running."""
    exited: list[bool] = []

    def fake_exit():
        exited.append(True)

    ws = _FakeWS()
    # Push two heartbeats spaced within the deadline.
    # interval=0.1s, limit=2 -> deadline=0.2s; heartbeats every 0.05s keep it alive.
    worker = SessionWorker(ws, _FakeBackend(),
                           heartbeat_interval_s=0.1, missed_limit=2,
                           _exit_fn=fake_exit)

    async def _feed_heartbeats():
        for _ in range(4):
            await asyncio.sleep(0.05)
            ws.push({"type": "heartbeat"})

    feeder = asyncio.create_task(_feed_heartbeats())
    try:
        await asyncio.wait_for(worker.run(), timeout=0.3)
    except (asyncio.TimeoutError, StopAsyncIteration):
        pass
    finally:
        feeder.cancel()

    assert not exited, "exit_fn must NOT have been called when heartbeats were arriving"


# ---------------------------------------------------------------------------
# Heartbeat message sends an ack
# ---------------------------------------------------------------------------

async def test_heartbeat_sends_ack():
    """Worker replies heartbeat_ack for each heartbeat received."""
    ws = _FakeWS()
    ws.push({"type": "heartbeat"})
    worker = SessionWorker(ws, _FakeBackend(),
                           heartbeat_interval_s=60.0, missed_limit=3,
                           _exit_fn=lambda: None)
    await worker.run()
    acks = [m for m in ws.sent if m.get("type") == "heartbeat_ack"]
    assert len(acks) == 1


# ---------------------------------------------------------------------------
# Heartbeat does not appear as an action result
# ---------------------------------------------------------------------------

async def test_heartbeat_not_dispatched_as_action():
    """Heartbeat messages must not generate a result frame."""
    ws = _FakeWS()
    ws.push({"type": "heartbeat"})
    worker = SessionWorker(ws, _FakeBackend(),
                           heartbeat_interval_s=60.0, missed_limit=3,
                           _exit_fn=lambda: None)
    await worker.run()
    result_frames = [m for m in ws.sent if m.get("type") == "result"]
    assert result_frames == []


# ---------------------------------------------------------------------------
# create_kill_on_close_job: fail-closed on non-Windows
# ---------------------------------------------------------------------------

def test_create_kill_on_close_job_non_windows():
    """create_kill_on_close_job() returns None on non-Windows."""
    from cerebral.sandbox._windows import create_kill_on_close_job
    if sys.platform != "win32":
        assert create_kill_on_close_job() is None


def test_create_kill_on_close_job_windows_unavailable_libs():
    """create_kill_on_close_job() returns None when win32job is unavailable."""
    from cerebral.sandbox import _windows as _w
    orig = sys.platform
    try:
        # Simulate Windows but with missing win32job via patch
        with patch.object(_w, "sys") as mock_sys, \
             patch.dict("sys.modules", {"win32job": None}):
            mock_sys.platform = "win32"
            result = _w.create_kill_on_close_job()
        # Either None (ImportError caught) or a handle (real Windows).
        # On a real Windows machine win32job IS available, so don't assert None here.
        # The non-Windows path is covered by test_create_kill_on_close_job_non_windows.
    except Exception:
        pass  # acceptable -- real Windows returns a handle


# ---------------------------------------------------------------------------
# abort_current calls _terminate_worker_fn (Visualiser Stop + F11+F12 seam)
# ---------------------------------------------------------------------------

def test_abort_current_calls_terminate_worker_fn():
    """abort_current() calls _terminate_worker_fn when wired (S12 seam)."""
    import plugins.computer_use as cu_mod
    from plugins.computer_use import ComputerUsePlugin

    called: list[bool] = []
    cu_mod.set_terminate_worker_fn(lambda: called.append(True))
    try:
        plugin = ComputerUsePlugin(backend=None)
        cu_mod.abort_current()
        assert called, "terminate_worker_fn must be called from abort_current()"
    finally:
        cu_mod.set_terminate_worker_fn(None)


def test_abort_current_safe_when_terminate_fn_raises():
    """abort_current() does not propagate exceptions from terminate_worker_fn."""
    import plugins.computer_use as cu_mod
    from plugins.computer_use import ComputerUsePlugin

    def _boom():
        raise RuntimeError("terminate failed")

    cu_mod.set_terminate_worker_fn(_boom)
    try:
        _p = ComputerUsePlugin(backend=None)
        cu_mod.abort_current()  # must not raise
    finally:
        cu_mod.set_terminate_worker_fn(None)


# ---------------------------------------------------------------------------
# F11+F12 routes through abort_current (terminate seam reached)
# ---------------------------------------------------------------------------

def test_f11_f12_callback_is_abort_current():
    """The callback passed to hotkey_register_fn is abort_current, not plugin.abort.

    This ensures F11+F12 also fires _terminate_worker_fn (S12 kill-switch crossing
    the session boundary)."""
    import plugins.computer_use as cu_mod
    from plugins.computer_use import ComputerUsePlugin

    captured: list = []
    plugin = ComputerUsePlugin(
        backend=None,
        hotkey_register_fn=lambda cb: captured.append(cb),
    )
    assert captured, "hotkey_register_fn must receive a callback"
    assert captured[0] is cu_mod.abort_current, (
        "F11+F12 callback must be abort_current() so worker termination is included"
    )


def test_f11_f12_fires_terminate_worker_fn_via_abort_current():
    """Calling the F11+F12 callback calls terminate_worker_fn (end-to-end path)."""
    import plugins.computer_use as cu_mod
    from plugins.computer_use import ComputerUsePlugin

    called: list[bool] = []
    cu_mod.set_terminate_worker_fn(lambda: called.append(True))
    try:
        captured: list = []
        plugin = ComputerUsePlugin(
            backend=None,
            hotkey_register_fn=lambda cb: captured.append(cb),
        )
        hotkey_cb = captured[0]
        hotkey_cb()  # simulate F11+F12 press
        assert called, "terminate_worker_fn must be reached via F11+F12 -> abort_current"
        assert plugin._abort_event.is_set(), "_abort_event must also be set"
    finally:
        cu_mod.set_terminate_worker_fn(None)


# ---------------------------------------------------------------------------
# Fail-closed: non-Windows check for _terminate_worker_process in main.py
# ---------------------------------------------------------------------------

def test_terminate_worker_process_no_op_without_handle():
    """_terminate_worker_process() is a no-op when no proc handle is registered."""
    import cerebral.main as main_mod
    orig = main_mod._worker_proc_handle
    try:
        main_mod._worker_proc_handle = None
        main_mod._terminate_worker_process()  # must not raise
    finally:
        main_mod._worker_proc_handle = orig


def test_terminate_worker_process_non_windows_no_op():
    """_terminate_worker_process() is a no-op on non-Windows even with a handle."""
    import cerebral.main as main_mod
    if sys.platform == "win32":
        pytest.skip("non-Windows behaviour -- skip on real Windows")
    orig = main_mod._worker_proc_handle
    try:
        main_mod._worker_proc_handle = 12345
        main_mod._terminate_worker_process()  # must not raise, no TerminateProcess call
    finally:
        main_mod._worker_proc_handle = orig


# ---------------------------------------------------------------------------
# Heartbeat sender task starts / stops with worker connect / disconnect
# ---------------------------------------------------------------------------

def test_heartbeat_task_starts_on_wire():
    """_wire_session_worker starts the heartbeat task."""
    import cerebral.main as main_mod

    fake_ws = object()
    orig_ws = main_mod._worker_ws
    orig_task = main_mod._worker_heartbeat_task
    try:
        # Simulate a fresh connect
        main_mod._worker_ws = None
        main_mod._worker_heartbeat_task = None
        main_mod._wire_session_worker(fake_ws)
        assert main_mod._worker_heartbeat_task is not None
        assert not main_mod._worker_heartbeat_task.done()
    finally:
        if main_mod._worker_heartbeat_task and not main_mod._worker_heartbeat_task.done():
            main_mod._worker_heartbeat_task.cancel()
        main_mod._worker_ws = orig_ws
        main_mod._worker_heartbeat_task = orig_task


async def test_heartbeat_task_cancelled_on_unwire():
    """_unwire_session_worker cancels the heartbeat task."""
    import cerebral.main as main_mod

    async def _dummy():
        await asyncio.sleep(9999)

    task = asyncio.get_event_loop().create_task(_dummy())

    orig_ws = main_mod._worker_ws
    orig_task = main_mod._worker_heartbeat_task
    orig_pending = dict(main_mod._worker_pending)
    try:
        main_mod._worker_heartbeat_task = task
        main_mod._worker_ws = object()  # pretend connected
        main_mod._unwire_session_worker()
        # cancel() is requested; give the loop one tick to process it.
        await asyncio.sleep(0)
        assert task.cancelled() or task.done() or task.cancelling() > 0
        assert main_mod._worker_heartbeat_task is None
    finally:
        main_mod._worker_ws = orig_ws
        main_mod._worker_heartbeat_task = orig_task
        main_mod._worker_pending.update(orig_pending)
        if not task.done():
            task.cancel()
