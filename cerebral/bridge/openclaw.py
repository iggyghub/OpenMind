"""
OpenClaw channel bridge — Issue #22.

Cerebral is the local brain; OpenClaw is the external Node.js gateway that
already speaks WhatsApp, Telegram, Discord, Slack, etc. The bridge is the
seam: it connects to OpenClaw's inbound message stream, runs each message
through the same LLM + MCP pipeline used for voice wakes, then posts the
reply back through OpenClaw so it lands in the originating channel.

Public interface:

    bridge = ChannelBridge(
        process_fn,                # async (transcript, history) -> str
        fetch_fn=...,              # injectable HTTP for tests
        ws_connect_fn=...,         # injectable WS for tests
        ws_url="ws://localhost:3000/agent/stream",
        outbound_url="http://localhost:3000/agent/reply",
        api_key="",
    )
    asyncio.create_task(bridge.start())   # runs forever, graceful on failure
    ...
    await bridge.stop()                   # closes WS, exits the loop

Each (channel, sender_id) maintains a short conversation buffer in RAM.
Nothing is persisted — channel history dies with the process.

All side-effects (HTTP, WS) are injectable so the unit suite runs hermetic.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Reasonable defaults for a local OpenClaw install. Override per environment.
DEFAULT_WS_URL = "ws://localhost:3000/agent/stream"
DEFAULT_OUTBOUND_URL = "http://localhost:3000/agent/reply"
DEFAULT_HISTORY_LIMIT = 16
ERROR_REPLY = "Sorry, something went wrong handling that message."


ProcessFn = Callable[[str, list[dict]], Awaitable[str]]
FetchFn = Callable[..., Awaitable[Any]]
WsConnectFn = Callable[..., Awaitable[Any]]


async def _default_fetch(
    *,
    method: str,
    url: str,
    headers: dict | None = None,
    json: dict | None = None,  # noqa: A002 — matches httpx/aiohttp kwarg
) -> Any:
    """HTTP transport for production use. Tries aiohttp, falls back to httpx."""
    try:
        import aiohttp  # type: ignore

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, json=json) as resp:
                if resp.content_type == "application/json":
                    return await resp.json()
                return await resp.text()
    except ImportError:
        pass

    import httpx  # type: ignore

    async with httpx.AsyncClient() as client:
        resp = await client.request(method, url, headers=headers, json=json)
        try:
            return resp.json()
        except ValueError:
            return resp.text


async def _default_ws_connect(url: str):
    """WebSocket transport for production use. Uses the `websockets` library."""
    import websockets  # type: ignore

    return await websockets.connect(url)


class ChannelBridge:
    def __init__(
        self,
        process_fn: ProcessFn,
        *,
        fetch_fn: FetchFn | None = None,
        ws_connect_fn: WsConnectFn | None = None,
        ws_url: str = DEFAULT_WS_URL,
        outbound_url: str = DEFAULT_OUTBOUND_URL,
        api_key: str = "",
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        self._process_fn = process_fn
        self._fetch = fetch_fn or _default_fetch
        self._ws_connect = ws_connect_fn or _default_ws_connect
        self._ws_url = ws_url
        self._outbound_url = outbound_url
        self._api_key = api_key
        self._history_limit = max(2, history_limit)
        self._sessions: dict[str, list[dict]] = {}
        self._ws: Any = None
        self._running = False
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Connect to OpenClaw's inbound stream and forward messages until
        ``stop()`` is called. Logs and returns gracefully if OpenClaw is
        unreachable — same pattern as audio/TTS startup."""
        self._stop_event.clear()
        try:
            self._ws = await self._ws_connect(self._ws_url)
        except (ConnectionError, ConnectionRefusedError, OSError) as exc:
            logger.warning(
                "[bridge] OpenClaw unreachable at %s — channel bridge disabled (%s)",
                self._ws_url, exc,
            )
            return

        self._running = True
        logger.info("[bridge] Connected to OpenClaw at %s", self._ws_url)
        try:
            async for raw in self._ws:
                if self._stop_event.is_set():
                    break
                await self._on_ws_message(raw)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("[bridge] WebSocket loop ended: %s", exc)
        finally:
            self._running = False
            logger.info("[bridge] Channel bridge stopped")

    async def stop(self) -> None:
        self._stop_event.set()
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # pragma: no cover — best effort
                pass

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    async def _on_ws_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("[bridge] dropped non-UTF8 ws frame")
                return
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[bridge] dropped malformed ws frame: %r", raw[:80])
            return
        if not isinstance(payload, dict):
            logger.warning("[bridge] dropped non-object ws payload: %r", payload)
            return
        await self.handle_inbound(payload)

    async def handle_inbound(self, message: dict) -> None:
        """Process one inbound channel message and post a reply.

        Expected payload shape (loose — extra keys are ignored):
            {"channel": "telegram", "sender_id": "abc", "text": "hi", ...}
        """
        channel = message.get("channel") or "unknown"
        sender_id = message.get("sender_id")
        text = (message.get("text") or "").strip()

        if not sender_id or not text:
            return

        session_key = f"{channel}:{sender_id}"
        history = list(self._sessions.get(session_key, []))

        try:
            reply = await self._process_fn(text, history)
        except Exception as exc:
            logger.exception("[bridge] process_fn raised for %s: %s", session_key, exc)
            reply = ERROR_REPLY
        else:
            self._append_history(session_key, "user", text)
            self._append_history(session_key, "assistant", reply)

        await self._send_reply(channel, sender_id, reply, message)

    # ------------------------------------------------------------------
    # Session buffer
    # ------------------------------------------------------------------

    def _append_history(self, session_key: str, role: str, text: str) -> None:
        buf = self._sessions.setdefault(session_key, [])
        buf.append({"role": role, "text": text})
        # Keep only the most recent `history_limit` entries
        if len(buf) > self._history_limit:
            del buf[: len(buf) - self._history_limit]

    def get_history(self, session_key: str) -> list[dict]:
        """Return a copy of the conversation buffer for a session."""
        return list(self._sessions.get(session_key, []))

    def reset_session(self, session_key: str) -> None:
        self._sessions.pop(session_key, None)

    # ------------------------------------------------------------------
    # Outbound reply
    # ------------------------------------------------------------------

    async def _send_reply(
        self,
        channel: str,
        sender_id: str,
        text: str,
        original: dict,
    ) -> None:
        body = {"channel": channel, "sender_id": sender_id, "text": text}
        # Pass message_id through so OpenClaw can thread the reply if needed
        if "message_id" in original:
            body["message_id"] = original["message_id"]

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            await self._fetch(
                method="POST",
                url=self._outbound_url,
                headers=headers,
                json=body,
            )
        except Exception as exc:
            logger.warning(
                "[bridge] outbound POST to %s failed: %s",
                self._outbound_url, exc,
            )
