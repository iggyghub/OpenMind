"""Tests for plugins/discord_user.py -- Issue #175 (slice 1), ADR-0006.

The plugin reads the user's Discord DMs via a self-bot gateway and
exposes a four-tool surface for the LLM. NONE of the side-effect
surfaces -- HTTP transport, ``discord.py-self`` client, real network
-- are exercised here. Tests fake the entire transport via constructor
injection (``fetch_fn`` + ``client_factory``).

The plugin lives at ``plugins/discord_user.py``; tests load it via
spec_from_file_location so the import path doesn't depend on
``plugins`` being on ``sys.path`` (matches the orchestrator's own
discovery posture -- see Issue #153).
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

import pytest


# ---------------------------------------------------------------------------
# Plugin loader -- mirror orchestrator-style importlib loading
# ---------------------------------------------------------------------------

def _load_plugin_module():
    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "discord_user.py"
    spec = importlib.util.spec_from_file_location(
        "openmind_plugin_discord_user", plugin_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


PLUGIN_MOD = _load_plugin_module()


async def _unused_draft(event: dict) -> None:
    return None


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Clear the module-level setters between tests so order-dependent
    tests don't leak state into one another."""
    PLUGIN_MOD.set_token_provider(lambda: None)
    PLUGIN_MOD.set_draft_callback(_unused_draft)  # type: ignore[arg-type]
    yield
    PLUGIN_MOD.set_token_provider(lambda: None)
    PLUGIN_MOD.set_draft_callback(_unused_draft)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Token provider
# ---------------------------------------------------------------------------

class _StaticTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def current(self) -> Optional[str]:
        return self._token or None


# ---------------------------------------------------------------------------
# fetch_fn fake
# ---------------------------------------------------------------------------

class FakeFetch:
    """Replaceable ``fetch_fn`` that returns canned payloads keyed by
    ``(method, endpoint-suffix)``.

    The plugin calls ``fetch_fn(method, url, headers=..., params=...,
    json=...)``; URL suffix matching keeps tests robust to base-URL
    drift.
    """

    def __init__(
        self,
        responses: dict[tuple[str, str], Any] | None = None,
        raises: dict[tuple[str, str], Exception] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.raises = raises or {}
        self.calls: list[dict] = []

    async def __call__(
        self, method: str, url: str, *,
        headers: dict | None = None,
        params: dict | None = None,
        json: dict | None = None,
    ) -> Any:
        self.calls.append({
            "method": method,
            "url": url,
            "headers": dict(headers or {}),
            "params": dict(params or {}),
            "json": dict(json or {}) if json is not None else None,
        })
        for (m, suffix), exc in self.raises.items():
            if m == method and url.endswith(suffix):
                raise exc
        for (m, suffix), payload in self.responses.items():
            if m == method and url.endswith(suffix):
                return payload
        return None


# ---------------------------------------------------------------------------
# DiscordClient fake -- only what the subscriber loop touches
# ---------------------------------------------------------------------------

class FakeDiscordClient:
    """Hand-rolled DiscordClient Protocol implementation for tests.

    ``feed_message`` lets a test push a message-like object through
    the registered handler synchronously; ``presence_changes`` records
    every ``change_presence`` call.
    """

    def __init__(self, raise_on_start: Optional[Exception] = None) -> None:
        self._handler: Optional[Callable[[Any], Awaitable[None]]] = None
        self.start_calls: list[str] = []
        self.close_calls = 0
        self.presence_changes: list[str] = []
        self._raise_on_start = raise_on_start
        self._stop = asyncio.Event()

    def set_message_handler(
        self, cb: Callable[[Any], Awaitable[None]],
    ) -> None:
        self._handler = cb

    async def start(self, token: str) -> None:
        self.start_calls.append(token)
        if self._raise_on_start is not None:
            raise self._raise_on_start
        await self._stop.wait()

    async def close(self) -> None:
        self.close_calls += 1
        self._stop.set()

    async def change_presence(self, *, status: str) -> None:
        self.presence_changes.append(status)

    async def feed_message(self, message: Any) -> None:
        if self._handler is not None:
            await self._handler(message)


def _fake_user(name: str = "felix", uid: str = "1") -> Any:
    return SimpleNamespace(id=uid, username=name)


def _dm_message(
    *,
    author_name: str = "Alice",
    author_id: str = "2",
    text: str = "hello",
    channel_id: str = "100",
    message_id: str = "m1",
    channel_type: str = "private",
    is_self: bool = False,
    is_bot: bool = False,
) -> Any:
    author = SimpleNamespace(
        id=author_id, name=author_name, display_name=author_name,
        is_self=is_self, bot=is_bot,
    )
    channel = SimpleNamespace(
        id=channel_id,
        type=SimpleNamespace(name=channel_type),
    )
    return SimpleNamespace(
        id=message_id, content=text, author=author, channel=channel,
    )


# ---------------------------------------------------------------------------
# Plugin builder
# ---------------------------------------------------------------------------

def _make_plugin(
    *,
    token: str = "REDACTED-TOKEN",
    fetch: Optional[FakeFetch] = None,
    client: Optional[FakeDiscordClient] = None,
    draft_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
):
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    client_factory = (lambda: client) if client is not None else None
    return plugin_cls(
        token_provider=_StaticTokenProvider(token),
        fetch_fn=fetch,
        client_factory=client_factory,
        draft_callback=draft_callback,
    )


# ===========================================================================
# Tool surface -- shape, count, capability declarations
# ===========================================================================

def test_list_tools_returns_seven_discord_tools():
    plugin = _make_plugin()
    tools = plugin.list_tools()
    names = [t.name for t in tools]
    assert set(names) == {
        "discord_list_conversations",
        "discord_get_messages",
        "discord_send_message",
        "discord_set_presence",
        "discord_react",
        "discord_edit",
        "discord_delete",
    }


def test_outbound_tools_marked_irreversible():
    plugin = _make_plugin()
    tools = {t.name: t for t in plugin.list_tools()}
    # All four outbound-mutating tools are irreversible (Issue #175 + #178).
    for name in (
        "discord_send_message",
        "discord_react",
        "discord_edit",
        "discord_delete",
    ):
        assert tools[name].irreversible is True, name
    # Read and presence tools are not.
    for name in (
        "discord_list_conversations",
        "discord_get_messages",
        "discord_set_presence",
    ):
        assert tools[name].irreversible is False, name


def test_required_capabilities_declares_self_bot_quad():
    """ADR-0006 specifies four required capabilities. Over-declaration
    of secrets_read is deliberate (the openclaw_channels precedent)."""
    assert PLUGIN_MOD.REQUIRED_CAPABILITIES == frozenset({
        "secrets_read",
        "external_data_read",
        "external_data_write",
        "network_egress_cloud",
    })


# ===========================================================================
# discord_list_conversations
# ===========================================================================

async def test_list_conversations_returns_dm_threads():
    fetch = FakeFetch(responses={
        ("GET", "users/@me/channels"): [
            {
                "id": "100", "type": 1,
                "recipients": [{"id": "2", "username": "Alice",
                                "global_name": "Alice"}],
                "last_message_id": "m1",
            },
            {
                "id": "200", "type": 0,  # text channel -- filtered out
                "name": "#general", "recipients": [],
            },
            {
                "id": "300", "type": 3,  # group DM
                "recipients": [
                    {"id": "2", "username": "Alice"},
                    {"id": "3", "username": "Bob"},
                ],
                "name": "weekend plans",
                "last_message_id": "m2",
            },
        ],
    })
    plugin = _make_plugin(fetch=fetch)

    result = await plugin.call_tool("discord_list_conversations", {})
    assert result.is_error is False
    payload = json.loads(result.content)
    threads = payload["conversations"]
    # Two DM-like threads, the text channel was filtered out.
    assert len(threads) == 2
    by_id = {t["id"]: t for t in threads}
    assert by_id["100"]["type"] == "dm"
    assert by_id["100"]["recipient_names"] == ["Alice"]
    assert by_id["300"]["type"] == "group_dm"
    assert "Alice" in by_id["300"]["recipient_names"]
    assert "Bob" in by_id["300"]["recipient_names"]


async def test_list_conversations_authorization_header_is_raw_token():
    """Discord user-account tokens go in ``Authorization: <token>`` -- no
    Bearer prefix (that's the OAuth path), no Bot prefix (that's the
    bot-API path). ADR-0006 documents this; the test pins it."""
    secret = "RAW-USER-TOKEN-DO-NOT-LEAK"
    fetch = FakeFetch(responses={
        ("GET", "users/@me/channels"): [],
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    await plugin.call_tool("discord_list_conversations", {})
    assert fetch.calls
    headers = fetch.calls[0]["headers"]
    assert headers.get("Authorization") == secret
    assert "Bearer" not in str(headers.get("Authorization", ""))
    assert "Bot " not in str(headers.get("Authorization", ""))


# ===========================================================================
# discord_get_messages
# ===========================================================================

async def test_get_messages_requires_channel_id():
    plugin = _make_plugin(fetch=FakeFetch())
    result = await plugin.call_tool("discord_get_messages", {})
    assert result.is_error is True
    assert "channel_id" in result.content


async def test_get_messages_returns_shaped_messages():
    fetch = FakeFetch(responses={
        ("GET", "channels/100/messages"): [
            {
                "id": "m1", "channel_id": "100",
                "author": {"id": "2", "username": "Alice",
                           "global_name": "Alice"},
                "content": "hi Felix",
                "timestamp": "2026-05-27T10:00:00Z",
            },
        ],
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool(
        "discord_get_messages", {"channel_id": "100", "limit": 5},
    )
    assert result.is_error is False
    payload = json.loads(result.content)
    msgs = payload["messages"]
    assert len(msgs) == 1
    assert msgs[0]["content"] == "hi Felix"
    assert msgs[0]["author_name"] == "Alice"
    # Limit is forwarded as a query param.
    assert fetch.calls[0]["params"] == {"limit": 5}


async def test_get_messages_clamps_limit_to_bounds():
    fetch = FakeFetch(responses={
        ("GET", "channels/100/messages"): [],
    })
    plugin = _make_plugin(fetch=fetch)
    # Over the max -- clamp down.
    await plugin.call_tool(
        "discord_get_messages", {"channel_id": "100", "limit": 9_999},
    )
    assert fetch.calls[-1]["params"]["limit"] == 100
    # Zero / negative -- clamp up.
    await plugin.call_tool(
        "discord_get_messages", {"channel_id": "100", "limit": 0},
    )
    assert fetch.calls[-1]["params"]["limit"] == 1


# ===========================================================================
# discord_send_message -- the confirm gate
# ===========================================================================

async def test_send_message_without_confirm_returns_preview_no_http_call():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)

    result = await plugin.call_tool("discord_send_message", {
        "channel_id": "100", "content": "Hello",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is False
    assert payload["preview"] == "Hello"
    # Confirm gate must short-circuit BEFORE any HTTP call.
    assert fetch.calls == []


async def test_send_message_with_confirm_calls_post_and_returns_message():
    fetch = FakeFetch(responses={
        ("POST", "channels/100/messages"): {
            "id": "m5", "channel_id": "100",
            "author": {"id": "1", "username": "felix"},
            "content": "Hello",
            "timestamp": "2026-05-27T10:01:00Z",
        },
    })
    plugin = _make_plugin(fetch=fetch)

    result = await plugin.call_tool("discord_send_message", {
        "channel_id": "100", "content": "Hello", "confirm": True,
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is True
    assert payload["message"]["content"] == "Hello"
    # Exactly one POST hit the transport.
    assert len(fetch.calls) == 1
    assert fetch.calls[0]["method"] == "POST"
    assert fetch.calls[0]["json"] == {"content": "Hello"}


async def test_send_message_missing_args_is_error():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_send_message", {
        "content": "Hello", "confirm": True,
    })
    assert result.is_error is True
    assert "channel_id" in result.content

    result = await plugin.call_tool("discord_send_message", {
        "channel_id": "100", "confirm": True,
    })
    assert result.is_error is True
    assert "content" in result.content


# ===========================================================================
# discord_set_presence -- slice-1 stub
# ===========================================================================

async def test_set_presence_records_state_when_subscriber_offline():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_set_presence", {
        "status": "idle",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["status"] == "idle"
    assert payload["applied"] is False
    assert "subscriber not running" in payload["note"]
    # Internal state recorded so the next connect picks it up.
    assert plugin._desired_presence == "idle"


async def test_set_presence_rejects_invalid_status():
    plugin = _make_plugin(fetch=FakeFetch())
    result = await plugin.call_tool("discord_set_presence", {
        "status": "bogus",
    })
    assert result.is_error is True


# ===========================================================================
# Token resolution + scrubbing
# ===========================================================================

async def test_call_tool_with_no_token_returns_error():
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    plugin = plugin_cls(token_provider=None, fetch_fn=FakeFetch())
    # Module-level factory returns None (fixture default).
    result = await plugin.call_tool("discord_list_conversations", {})
    assert result.is_error is True
    assert ("token provider not wired" in result.content
            or "no Discord user-account token" in result.content)


async def test_token_scrubbed_from_error_messages():
    secret = "USER-TOKEN-DO-NOT-LEAK-67890"
    fetch = FakeFetch(raises={
        ("GET", "users/@me/channels"):
            RuntimeError(f"failed talking to discord with token={secret}"),
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    result = await plugin.call_tool("discord_list_conversations", {})
    assert result.is_error is True
    assert secret not in result.content
    assert "***" in result.content


# ===========================================================================
# Subscriber lifecycle + inbound event extraction
# ===========================================================================

async def _drain_until(predicate, *, timeout: float = 1.0, poll: float = 0.01):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return False


async def test_start_subscriber_validates_token_against_users_me():
    """A bogus token fails fast on /users/@me; subscriber doesn't start."""
    fetch = FakeFetch(raises={
        ("GET", "users/@me"): RuntimeError("HTTP 401 Unauthorized"),
    })
    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=_unused_draft,
    )
    await plugin.start_subscriber()
    assert plugin.subscriber_running is False
    # The client was never started because validation failed first.
    assert client.start_calls == []


async def test_start_subscriber_cancelled_validation_degrades(caplog):
    """Issue #182: a CancelledError raised by the in-flight /users/@me
    request (e.g. a sibling plugin's teardown disturbing the loop) must
    degrade gracefully like any other startup failure -- warn and return,
    never propagate up and kill Cerebral."""
    fetch = FakeFetch(raises={
        ("GET", "users/@me"): asyncio.CancelledError(),
    })
    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=_unused_draft,
    )
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()  # must not raise
    assert plugin.subscriber_running is False
    assert client.start_calls == []
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("cancelled" in m for m in msgs), msgs


async def test_start_subscriber_cancelled_during_deliberate_stop_reraises():
    """Issue #182 counterpart: when the cancellation is part of a
    deliberate stop (stop_subscriber set the stop event), it must
    propagate so shutdown isn't swallowed."""
    fetch = FakeFetch(raises={
        ("GET", "users/@me"): asyncio.CancelledError(),
    })
    plugin = _make_plugin(
        fetch=fetch, client=FakeDiscordClient(), draft_callback=_unused_draft,
    )
    plugin._stop_event.set()
    with pytest.raises(asyncio.CancelledError):
        await plugin.start_subscriber()


async def test_start_subscriber_starts_client_after_validation():
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })
    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=_unused_draft,
    )
    await plugin.start_subscriber()
    try:
        ok = await _drain_until(lambda: len(client.start_calls) >= 1)
        assert ok, "subscriber didn't call client.start"
        assert plugin.subscriber_running is True
    finally:
        await plugin.stop_subscriber()


async def test_inbound_dm_surfaces_via_draft_callback():
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })
    drafts: list[dict] = []

    async def draft_cb(event: dict) -> None:
        drafts.append(event)

    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=draft_cb,
    )
    await plugin.start_subscriber()
    try:
        await _drain_until(lambda: len(client.start_calls) >= 1)
        # Push a fake DM through the registered handler.
        await client.feed_message(_dm_message(
            author_name="Alice", text="hello Felix",
            channel_id="100", message_id="m1",
        ))
        ok = await _drain_until(lambda: len(drafts) >= 1)
        assert ok, "draft callback never fired"
    finally:
        await plugin.stop_subscriber()

    event = drafts[0]
    assert event["channel"] == "discord_user"
    assert event["session_key"] == "discord_user:dm:100"
    assert event["author_name"] == "Alice"
    assert event["text"] == "hello Felix"


async def test_inbound_server_message_is_ignored():
    """Slice 1 is DM-only -- server messages don't surface as drafts."""
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })
    drafts: list[dict] = []

    async def draft_cb(event: dict) -> None:
        drafts.append(event)

    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=draft_cb,
    )
    await plugin.start_subscriber()
    try:
        await _drain_until(lambda: len(client.start_calls) >= 1)
        # Server text channel -- ChannelType.name == "text".
        await client.feed_message(_dm_message(
            channel_type="text", text="general chat",
        ))
        # Give the handler a moment to (not) fire.
        await asyncio.sleep(0.05)
    finally:
        await plugin.stop_subscriber()
    assert drafts == []


async def test_inbound_self_authored_message_is_ignored():
    """A message we sent ourselves doesn't trigger a draft."""
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })
    drafts: list[dict] = []

    async def draft_cb(event: dict) -> None:
        drafts.append(event)

    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=draft_cb,
    )
    await plugin.start_subscriber()
    try:
        await _drain_until(lambda: len(client.start_calls) >= 1)
        await client.feed_message(_dm_message(
            is_self=True, text="reply from me",
        ))
        await asyncio.sleep(0.05)
    finally:
        await plugin.stop_subscriber()
    assert drafts == []


async def test_subscriber_does_not_start_without_token(caplog):
    fetch = FakeFetch()
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    plugin = plugin_cls(
        token_provider=_StaticTokenProvider(""),  # empty token
        fetch_fn=fetch,
        client_factory=lambda: FakeDiscordClient(),
        draft_callback=_unused_draft,
    )
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()
    assert plugin.subscriber_running is False
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("not configured" in m for m in msgs), msgs


async def test_subscriber_does_not_start_without_draft_callback(caplog):
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    plugin = plugin_cls(
        token_provider=_StaticTokenProvider("tok"),
        fetch_fn=fetch,
        client_factory=lambda: FakeDiscordClient(),
        draft_callback=None,  # not wired
    )
    # Module-level callback also not wired (fixture default is a no-op
    # callback). We swap it back to None for this test only.
    PLUGIN_MOD.set_draft_callback(None)  # type: ignore[arg-type]
    try:
        with caplog.at_level(logging.WARNING):
            await plugin.start_subscriber()
        assert plugin.subscriber_running is False
        msgs = [rec.getMessage() for rec in caplog.records]
        assert any("draft callback not wired" in m for m in msgs), msgs
    finally:
        PLUGIN_MOD.set_draft_callback(_unused_draft)  # type: ignore[arg-type]


async def test_stop_subscriber_is_idempotent():
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })
    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=_unused_draft,
    )
    await plugin.start_subscriber()
    await plugin.stop_subscriber()
    await plugin.stop_subscriber()  # second call must not raise
    assert plugin.subscriber_running is False


async def test_client_factory_failure_logs_and_no_crash(caplog):
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })

    def failing_factory():
        raise RuntimeError("discord.py-self is not installed")

    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    plugin = plugin_cls(
        token_provider=_StaticTokenProvider("tok"),
        fetch_fn=fetch,
        client_factory=failing_factory,
        draft_callback=_unused_draft,
    )
    with caplog.at_level(logging.WARNING):
        await plugin.start_subscriber()
    assert plugin.subscriber_running is False
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("client factory raised" in m for m in msgs), msgs


async def test_set_presence_applies_immediately_when_subscriber_running():
    fetch = FakeFetch(responses={
        ("GET", "users/@me"): {"id": "1", "username": "felix"},
    })
    client = FakeDiscordClient()
    plugin = _make_plugin(
        fetch=fetch, client=client, draft_callback=_unused_draft,
    )
    await plugin.start_subscriber()
    try:
        await _drain_until(lambda: len(client.start_calls) >= 1)
        result = await plugin.call_tool("discord_set_presence", {
            "status": "dnd",
        })
        assert result.is_error is False
        payload = json.loads(result.content)
        assert payload["applied"] is True
        # At least one change_presence ("dnd") landed on the client.
        assert "dnd" in client.presence_changes
    finally:
        await plugin.stop_subscriber()


# ===========================================================================
# Module-level lifecycle wrappers
# ===========================================================================

async def test_module_create_records_active_plugin_singleton():
    plugin = PLUGIN_MOD.create()
    try:
        assert PLUGIN_MOD._active_plugin is plugin
        # No token provider wired (fixture default) -> start_subscriber
        # logs the graceful-degradation line and exits.
        await PLUGIN_MOD.start_subscriber()
        await PLUGIN_MOD.stop_subscriber()
        assert PLUGIN_MOD.subscriber_running() is False
    finally:
        PLUGIN_MOD._active_plugin = None
