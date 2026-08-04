"""Tests for the S11 #605 in-session worker (cerebral/session_worker.py).

All tests use a fake WS and fake ActuatorBackend -- no real UIA, no real
mouse/keyboard, no real WebSocket connection. Fail-closed on non-Windows
is covered by _make_default_actuator() returning None when backend is None.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.session_worker import (
    WORKER_PROTOCOL_VERSION,
    SessionWorker,
    _make_default_actuator,
)


# ---------------------------------------------------------------------------
# Fake WS: captures sent frames
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


# ---------------------------------------------------------------------------
# Fake backend
# ---------------------------------------------------------------------------

class _FakeBackend:
    def __init__(self, read_returns=None, capture_returns=None):
        self._read_returns = read_returns or [[{"name": "OK", "role": "Button", "bbox": [0,0,1,1]}]]
        self._capture_returns = capture_returns
        self.read_calls: list[str] = []
        self.click_calls: list[list] = []
        self.type_calls: list[str] = []
        self.press_calls: list[str] = []
        self._read_idx = 0

    def read_ui(self, window_title: str) -> list[dict]:
        self.read_calls.append(window_title)
        idx = min(self._read_idx, len(self._read_returns) - 1)
        self._read_idx += 1
        return list(self._read_returns[idx])

    def click(self, bbox: list[int]) -> None:
        self.click_calls.append(list(bbox))

    def type_text(self, text: str) -> None:
        self.type_calls.append(text)

    def capture_frame(self, window_title: str) -> bytes | None:
        return self._capture_returns

    def press_key(self, key: str) -> None:
        self.press_calls.append(key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(backend=None, *, use_fake=True):
    ws = _FakeWS()
    b = _FakeBackend() if use_fake and backend is None else backend
    return ws, SessionWorker(ws, b)


async def _handle_one(ws: _FakeWS, worker: SessionWorker, msg: dict) -> dict:
    """Send one action message and return the result the worker sent."""
    await worker._handle(msg)
    return ws.sent[-1]


# ---------------------------------------------------------------------------
# Fail-closed on non-Windows
# ---------------------------------------------------------------------------

def test_make_default_actuator_non_windows():
    """On non-Windows the default actuator is None (fail-closed).
    On Windows it may be non-None, but the import must not crash."""
    if sys.platform != "win32":
        assert _make_default_actuator() is None


async def test_worker_non_windows_backend_returns_not_windows():
    """When backend is None every action returns ok=False error='not-windows'."""
    ws = _FakeWS()
    worker = SessionWorker(ws, None)
    for action in ("read_ui", "click", "type", "capture", "press_key"):
        msg = {"type": action, "id": f"t-{action}",
               "window_title": "X", "bbox": [0,0,1,1], "text": "hi", "key": "enter"}
        await worker._handle(msg)
    for sent in ws.sent:
        assert sent["type"] == "result"
        assert sent["ok"] is False
        assert sent["error"] == "not-windows"


# ---------------------------------------------------------------------------
# worker_hello registration
# ---------------------------------------------------------------------------

async def test_worker_hello_sent_on_run_start():
    """run() sends worker_hello before the receive loop."""
    ws = _FakeWS()
    worker = SessionWorker(ws, _FakeBackend())
    # Empty queue -> __anext__ raises StopAsyncIteration immediately
    await worker.run()
    assert ws.sent[0] == {"type": "worker_hello", "version": WORKER_PROTOCOL_VERSION}


# ---------------------------------------------------------------------------
# read_ui round-trip
# ---------------------------------------------------------------------------

async def test_read_ui_returns_elements():
    ws, worker = _make()
    result = await _handle_one(ws, worker, {"type": "read_ui", "id": "r1", "window_title": "Notepad"})
    assert result == {
        "type": "result", "id": "r1", "ok": True,
        "data": {"elements": [{"name": "OK", "role": "Button", "bbox": [0, 0, 1, 1]}]},
    }
    assert worker._backend.read_calls == ["Notepad"]  # type: ignore[union-attr]


async def test_read_ui_backend_error_returns_not_ok():
    class _Bad:
        def read_ui(self, t): raise RuntimeError("UIA dead")
        def click(self, b): pass
        def type_text(self, t): pass
    ws = _FakeWS()
    worker = SessionWorker(ws, _Bad())
    r = await _handle_one(ws, worker, {"type": "read_ui", "id": "x", "window_title": "X"})
    assert r["ok"] is False
    assert "UIA dead" in r["error"]


# ---------------------------------------------------------------------------
# click round-trip
# ---------------------------------------------------------------------------

async def test_click_records_bbox():
    ws, worker = _make()
    r = await _handle_one(ws, worker, {"type": "click", "id": "c1", "bbox": [10, 20, 30, 40]})
    assert r == {"type": "result", "id": "c1", "ok": True, "data": {}}
    assert worker._backend.click_calls == [[10, 20, 30, 40]]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# type round-trip
# ---------------------------------------------------------------------------

async def test_type_records_text():
    ws, worker = _make()
    r = await _handle_one(ws, worker, {"type": "type", "id": "t1", "text": "hello"})
    assert r == {"type": "result", "id": "t1", "ok": True, "data": {}}
    assert worker._backend.type_calls == ["hello"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# capture round-trip
# ---------------------------------------------------------------------------

async def test_capture_returns_none_when_no_frame():
    ws, worker = _make()
    r = await _handle_one(ws, worker, {"type": "capture", "id": "cap1", "window_title": "X"})
    assert r["ok"] is True
    assert r["data"]["frame_b64"] is None


async def test_capture_returns_b64_when_frame_present():
    import base64
    frame = b"\x00\x01\x02"
    backend = _FakeBackend(capture_returns=frame)
    ws = _FakeWS()
    worker = SessionWorker(ws, backend)
    r = await _handle_one(ws, worker, {"type": "capture", "id": "cap2", "window_title": "X"})
    assert r["ok"] is True
    assert base64.b64decode(r["data"]["frame_b64"]) == frame


# ---------------------------------------------------------------------------
# press_key round-trip
# ---------------------------------------------------------------------------

async def test_press_key_records_key():
    ws, worker = _make()
    r = await _handle_one(ws, worker, {"type": "press_key", "id": "pk1", "key": "enter"})
    assert r == {"type": "result", "id": "pk1", "ok": True, "data": {}}
    assert worker._backend.press_calls == ["enter"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------

async def test_unknown_action_returns_error():
    ws, worker = _make()
    r = await _handle_one(ws, worker, {"type": "nuke", "id": "u1"})
    assert r["ok"] is False
    assert "unknown" in r["error"]


# ---------------------------------------------------------------------------
# run() filters non-action messages (Cerebral broadcasts)
# ---------------------------------------------------------------------------

async def test_run_ignores_non_action_messages():
    """Messages whose type is not a known action are silently skipped."""
    ws, worker = _make()
    ws.push({"type": "profiles_list", "data": {}})  # typical Cerebral broadcast
    ws.push({"type": "read_ui", "id": "r2", "window_title": "Calc"})
    await worker.run()
    # Only one result sent (the read_ui), not an error for profiles_list.
    results = [m for m in ws.sent if m.get("type") == "result"]
    assert len(results) == 1
    assert results[0]["id"] == "r2"


# ---------------------------------------------------------------------------
# Plugin seam: session_dispatch_fn routes primitives through the dispatch fn
# ---------------------------------------------------------------------------

async def test_plugin_routes_read_ui_through_dispatch_fn():
    """When session_dispatch_fn is set, _prim_read_ui calls it instead of backend."""
    from plugins.computer_use import ComputerUsePlugin

    dispatch_calls: list[tuple] = []
    fake_elements = [{"name": "Btn", "role": "Button", "bbox": [0, 0, 10, 10]}]

    async def fake_dispatch(action: str, params: dict) -> dict:
        dispatch_calls.append((action, params))
        return {"elements": fake_elements}

    plugin = ComputerUsePlugin(backend=None, session_dispatch_fn=fake_dispatch)
    # Directly test the primitive helper
    result = await plugin._prim_read_ui("Notepad")
    assert result == fake_elements
    assert dispatch_calls == [("read_ui", {"window_title": "Notepad"})]


async def test_plugin_routes_click_through_dispatch_fn():
    from plugins.computer_use import ComputerUsePlugin

    dispatch_calls: list[tuple] = []

    async def fake_dispatch(action: str, params: dict) -> dict:
        dispatch_calls.append((action, params))
        return {}

    plugin = ComputerUsePlugin(backend=None, session_dispatch_fn=fake_dispatch)
    await plugin._prim_click([1, 2, 3, 4])
    assert dispatch_calls == [("click", {"bbox": [1, 2, 3, 4]})]


async def test_plugin_routes_type_through_dispatch_fn():
    from plugins.computer_use import ComputerUsePlugin

    dispatch_calls: list[tuple] = []

    async def fake_dispatch(action: str, params: dict) -> dict:
        dispatch_calls.append((action, params))
        return {}

    plugin = ComputerUsePlugin(backend=None, session_dispatch_fn=fake_dispatch)
    await plugin._prim_type("hello world")
    assert dispatch_calls == [("type", {"text": "hello world"})]


async def test_plugin_falls_back_to_local_backend_when_no_dispatch():
    """When session_dispatch_fn is None, _prim_read_ui calls the local backend."""
    from plugins.computer_use import ComputerUsePlugin

    backend = _FakeBackend()
    plugin = ComputerUsePlugin(backend=backend, session_dispatch_fn=None)
    result = await plugin._prim_read_ui("Calc")
    assert result == [{"name": "OK", "role": "Button", "bbox": [0, 0, 1, 1]}]
    assert backend.read_calls == ["Calc"]


async def test_read_ui_tool_routes_through_dispatch_fn():
    """call_tool('read_ui') uses the dispatch fn when wired."""
    from plugins.computer_use import ComputerUsePlugin

    fake_elements = [{"name": "Two", "role": "Button", "bbox": [0, 0, 5, 5]}]

    async def fake_dispatch(action: str, params: dict) -> dict:
        return {"elements": fake_elements}

    # backend=None normally triggers fail-closed, but dispatch fn overrides.
    plugin = ComputerUsePlugin(backend=None, session_dispatch_fn=fake_dispatch)
    # call_tool checks backend is None first -- so to test read_ui routing we
    # need a non-None backend as the local fallback (dispatch wins regardless).
    plugin2 = ComputerUsePlugin(backend=_FakeBackend(), session_dispatch_fn=fake_dispatch)
    r = await plugin2.call_tool("read_ui", {"window_title": "Notepad"})
    import json as _json
    data = _json.loads(r.content)
    assert data["elements"] == fake_elements
    assert data["ok"] is True
