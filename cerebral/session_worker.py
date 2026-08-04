"""
In-session worker for ADR-0016 Phase 2 isolated-session path (S11 #605).

Run as: python -m cerebral.session_worker [ws://localhost:7766]

Connects to Cerebral's existing WS IPC server (ws://localhost:7766) as a
distinct client, sends a worker_hello to register, then handles action
messages from Cerebral and returns result messages.

## Wire protocol (device-agnostic -- no Windows shapes on the wire)

Cerebral -> Worker:
  {"type": "read_ui",   "id": "<id>", "window_title": "<title>"}
  {"type": "capture",   "id": "<id>", "window_title": "<title>"}
  {"type": "click",     "id": "<id>", "bbox": [l, t, r, b]}
  {"type": "type",      "id": "<id>", "text": "<text>"}
  {"type": "press_key", "id": "<id>", "key": "<key>"}
  {"type": "heartbeat"}                              -- S12 dead-man ping

Worker -> Cerebral:
  {"type": "worker_hello", "version": "1"}          -- sent on connect
  {"type": "result", "id": "<id>", "ok": true,  "data": {...}}
  {"type": "result", "id": "<id>", "ok": false, "error": "<msg>"}
  {"type": "heartbeat_ack"}                         -- S12 reply to each ping

"data" shapes:
  read_ui:   {"elements": [{"name": str, "role": str, "bbox": [l,t,r,b]}, ...]}
  capture:   {"frame_b64": "<base64-encoded bytes>" | null}
  click:     {}
  type:      {}
  press_key: {}

S12 dead-man: Cerebral sends a heartbeat every HEARTBEAT_INTERVAL_S seconds.
The worker's _watchdog() task fires if no heartbeat arrives within
HEARTBEAT_INTERVAL_S * HEARTBEAT_MISSED_LIMIT seconds and halts the worker
(covers a hung brain or wedged WS without any Cerebral involvement).

The protocol has no Windows-specific types; a future phone/glasses
actuator over OpenClaw implements the same contract with its own backend.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Callable, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

WORKER_PROTOCOL_VERSION = "1"

# S12 #606: dead-man heartbeat defaults. Worker halts if no heartbeat arrives
# within interval_s * missed_limit seconds. Configurable via __init__ params.
HEARTBEAT_INTERVAL_S: float = 10.0
HEARTBEAT_MISSED_LIMIT: int = 3


@runtime_checkable
class ActuatorBackend(Protocol):
    """Platform seam -- tests inject a fake, production uses _WindowsBackend.

    Optional methods (capture_frame, press_key): the worker handles their
    absence gracefully (returns ok=False with "not-supported").
    """

    def read_ui(self, window_title: str) -> list[dict]: ...
    def click(self, bbox: list[int]) -> None: ...
    def type_text(self, text: str) -> None: ...


def _make_default_actuator() -> ActuatorBackend | None:
    """Return the Windows UIA/pyautogui backend, or None on non-Windows.

    Importing Windows libs here keeps the module importable on any host.
    Non-Windows -> None -> every action message returns ok=False (fail-closed).
    """
    if sys.platform != "win32":
        return None
    try:
        # Reuse the plugin's Windows backend rather than duplicating it.
        from plugins.computer_use import _make_default_backend  # type: ignore
        return _make_default_backend()
    except Exception:
        return None


class SessionWorker:
    """Handles the action message loop for one WS connection.

    Injection seam: ``backend=<fake>`` in tests; production passes
    ``_make_default_actuator()`` which returns None on non-Windows.
    When backend is None every action returns ``ok=False, error="not-windows"``
    (fail-closed per SAFETY 2 / ADR-0016 SAFETY 4).

    S12 #606 dead-man: Cerebral sends ``{"type": "heartbeat"}`` every
    ``heartbeat_interval_s`` seconds. If the worker misses ``missed_limit``
    consecutive heartbeats (i.e. no heartbeat arrives within
    ``heartbeat_interval_s * missed_limit`` seconds), ``_exit_fn`` is called
    to halt the process. ``_exit_fn`` defaults to ``lambda: sys.exit(0)``; tests
    inject a sentinel-raising callable to catch the event without killing pytest.
    """

    def __init__(
        self,
        ws,
        backend: ActuatorBackend | None,
        *,
        heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
        missed_limit: int = HEARTBEAT_MISSED_LIMIT,
        _exit_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        self._ws = ws
        self._backend = backend
        self._heartbeat_interval_s = heartbeat_interval_s
        self._missed_limit = missed_limit
        self._exit_fn: Callable[[], None] = _exit_fn or (lambda: sys.exit(0))
        self._last_heartbeat: float = 0.0

    async def _send_result(self, req_id: str, **kwargs) -> None:
        await self._ws.send(json.dumps({"type": "result", "id": req_id, **kwargs}))

    async def _handle(self, msg: dict) -> None:
        action = msg.get("type")
        req_id = str(msg.get("id", ""))

        if self._backend is None:
            await self._send_result(req_id, ok=False, error="not-windows")
            return

        try:
            if action == "read_ui":
                elements = self._backend.read_ui(msg["window_title"])
                await self._send_result(req_id, ok=True, data={"elements": elements})

            elif action == "capture":
                fn = getattr(self._backend, "capture_frame", None)
                if fn is None:
                    await self._send_result(req_id, ok=False, error="not-supported")
                    return
                frame = fn(msg["window_title"])
                if frame is not None:
                    import base64
                    b64: str | None = base64.b64encode(frame).decode()
                else:
                    b64 = None
                await self._send_result(req_id, ok=True, data={"frame_b64": b64})

            elif action == "click":
                self._backend.click(msg["bbox"])
                await self._send_result(req_id, ok=True, data={})

            elif action == "type":
                self._backend.type_text(msg["text"])
                await self._send_result(req_id, ok=True, data={})

            elif action == "press_key":
                fn = getattr(self._backend, "press_key", None)
                if fn is None:
                    await self._send_result(req_id, ok=False, error="not-supported")
                    return
                fn(msg["key"])
                await self._send_result(req_id, ok=True, data={})

            else:
                await self._send_result(req_id, ok=False, error=f"unknown: {action!r}")

        except Exception as exc:
            logger.warning("[session_worker] %r failed: %s", action, exc, exc_info=True)
            await self._send_result(req_id, ok=False, error=str(exc))

    async def _watchdog(self) -> None:
        """S12 #606: halt if no heartbeat arrives within the deadline."""
        deadline = self._heartbeat_interval_s * self._missed_limit
        while True:
            await asyncio.sleep(self._heartbeat_interval_s)
            elapsed = asyncio.get_event_loop().time() - self._last_heartbeat
            if elapsed >= deadline:
                logger.warning(
                    "[session_worker] no heartbeat for %.0fs (limit %d x %.0fs) -- halting",
                    elapsed, self._missed_limit, self._heartbeat_interval_s,
                )
                self._exit_fn()
                return  # unreachable in production; lets tests proceed past _exit_fn

    async def run(self) -> None:
        """Register with Cerebral then receive actions until the WS closes."""
        await self._ws.send(json.dumps({
            "type": "worker_hello",
            "version": WORKER_PROTOCOL_VERSION,
        }))
        # S12: start watchdog; reset _last_heartbeat so time-since-connect counts.
        self._last_heartbeat = asyncio.get_event_loop().time()
        watchdog = asyncio.create_task(self._watchdog())
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # S12: heartbeat ping -- reset dead-man timer, send ack, no further dispatch.
                if msg.get("type") == "heartbeat":
                    self._last_heartbeat = asyncio.get_event_loop().time()
                    await self._ws.send(json.dumps({"type": "heartbeat_ack"}))
                    continue
                # Ignore non-action messages (Cerebral broadcasts state to all clients).
                if msg.get("type") not in {
                    "read_ui", "capture", "click", "type", "press_key",
                }:
                    continue
                await self._handle(msg)
        finally:
            watchdog.cancel()


async def connect_and_run(url: str, backend: ActuatorBackend | None) -> None:
    """Connect to Cerebral's WS server and run the worker until disconnect."""
    import websockets  # type: ignore
    async with websockets.connect(url) as ws:
        await SessionWorker(ws, backend).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:7766"
    asyncio.run(connect_and_run(url, _make_default_actuator()))
