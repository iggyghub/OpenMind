"""Tests for Discord-as-user slice 3 -- Issue #178, ADR-0006.

Three layers covered:

1. ``plugins/discord_user.py`` -- discord_react, discord_edit,
   discord_delete tool surface: confirm gate, REST endpoint shape,
   scrub discipline, missing-arg errors.
2. ``cerebral.discord_presence.DiscordPresenceController`` -- the
   auto-idle / auto-online state machine with mocked clock, sleep, and
   set_presence_fn.

No live network. Plugin transport is faked via ``fetch_fn`` injection,
mirroring slice-1/2's patterns. DiscordPresenceController dependencies
are all injectable.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import pytest

from cerebral.discord_presence import (
    DEFAULT_IDLE_AFTER_S,
    DiscordPresenceController,
)


# ---------------------------------------------------------------------------
# Plugin loader -- mirror slice-1 importlib pattern
# ---------------------------------------------------------------------------

def _load_plugin_module():
    plugin_path = (
        Path(__file__).resolve().parents[2] / "plugins" / "discord_user.py"
    )
    spec = importlib.util.spec_from_file_location(
        "openmind_plugin_discord_user_slice3", plugin_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


PLUGIN_MOD = _load_plugin_module()


class _StaticTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def current(self) -> Optional[str]:
        return self._token or None


class FakeFetch:
    """(method, url-suffix) keyed canned-response fake, identical to
    the slice-1/2 fixture."""

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
            "method": method, "url": url,
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


def _make_plugin(
    *,
    token: str = "TOK",
    fetch: Optional[FakeFetch] = None,
    on_manual_override: Optional[Callable[[str], None]] = None,
):
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    return plugin_cls(
        token_provider=_StaticTokenProvider(token),
        fetch_fn=fetch or FakeFetch(),
        on_manual_override=on_manual_override,
    )


# ===========================================================================
# Tool surface -- slice-3 tools appear in list_tools
# ===========================================================================

def test_list_tools_includes_react_edit_delete():
    plugin = _make_plugin()
    names = {t.name for t in plugin.list_tools()}
    assert "discord_react" in names
    assert "discord_edit" in names
    assert "discord_delete" in names


def test_react_edit_delete_marked_irreversible():
    plugin = _make_plugin()
    tools = {t.name: t for t in plugin.list_tools()}
    for name in ("discord_react", "discord_edit", "discord_delete"):
        assert tools[name].irreversible is True, name


# ===========================================================================
# discord_react
# ===========================================================================

async def test_react_without_confirm_returns_preview_no_http():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "\U0001f44d", "action": "add",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is False
    assert payload["emoji"] == "\U0001f44d"
    assert fetch.calls == []


async def test_react_add_calls_put_with_encoded_emoji():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "\U0001f44d", "action": "add", "confirm": True,
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is True
    assert len(fetch.calls) == 1
    call = fetch.calls[0]
    assert call["method"] == "PUT"
    assert "channels/100/messages/m1/reactions/" in call["url"]
    assert "/@me" in call["url"]


async def test_react_remove_calls_delete():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "\U0001f44d", "action": "remove", "confirm": True,
    })
    assert result.is_error is False
    assert fetch.calls[0]["method"] == "DELETE"


async def test_react_custom_emoji_name_id_format():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "pepe:12345", "action": "add", "confirm": True,
    })
    assert result.is_error is False
    call = fetch.calls[0]
    # The emoji must appear URL-encoded in the path
    assert "pepe" in call["url"]
    assert "12345" in call["url"]


async def test_react_invalid_action_is_error():
    plugin = _make_plugin()
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "\U0001f44d", "action": "like", "confirm": True,
    })
    assert result.is_error is True
    assert "action" in result.content.lower()


async def test_react_missing_required_args():
    plugin = _make_plugin()
    # Missing emoji
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "action": "add",
        "confirm": True,
    })
    assert result.is_error is True

    # Missing action
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "\U0001f44d", "confirm": True,
    })
    assert result.is_error is True


async def test_react_transport_failure_is_error():
    fetch = FakeFetch(raises={("PUT", "/@me"): RuntimeError("HTTP 403")})
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "\U0001f44d", "action": "add", "confirm": True,
    })
    assert result.is_error is True


async def test_react_token_scrubbed_from_error():
    secret = "SECRET-REACT-TOKEN"
    fetch = FakeFetch(raises={
        ("PUT", "/@me"): RuntimeError(f"token={secret}"),
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "\U0001f44d", "action": "add", "confirm": True,
    })
    assert result.is_error is True
    assert secret not in result.content


# ===========================================================================
# discord_edit
# ===========================================================================

async def test_edit_without_confirm_returns_preview_no_http():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "edited text",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is False
    assert payload["preview"] == "edited text"
    assert fetch.calls == []


async def test_edit_with_confirm_calls_patch_and_returns_message():
    resp_msg = {
        "id": "m1", "channel_id": "100",
        "author": {"id": "1", "username": "felix"},
        "content": "edited text",
        "timestamp": "2026-06-01T10:00:00Z",
    }
    fetch = FakeFetch(responses={
        ("PATCH", "channels/100/messages/m1"): resp_msg,
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "edited text", "confirm": True,
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is True
    assert payload["message"]["content"] == "edited text"
    assert len(fetch.calls) == 1
    call = fetch.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("channels/100/messages/m1")
    assert call["json"] == {"content": "edited text"}


async def test_edit_ownership_error_surfaces_from_discord():
    """Discord returns 403 when editing a message not owned by the caller;
    the plugin surfaces it rather than swallowing it."""
    fetch = FakeFetch(raises={
        ("PATCH", "channels/100/messages/m1"): RuntimeError(
            "HTTP 403 Forbidden -- Cannot edit message authored by other user",
        ),
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "new text", "confirm": True,
    })
    assert result.is_error is True
    assert "403" in result.content or "Cannot edit" in result.content


async def test_edit_missing_required_args():
    plugin = _make_plugin()
    result = await plugin.call_tool("discord_edit", {
        "message_id": "m1", "new_content": "text", "confirm": True,
    })
    assert result.is_error is True
    assert "channel_id" in result.content


async def test_edit_token_scrubbed_from_error():
    secret = "SECRET-EDIT-TOKEN"
    fetch = FakeFetch(raises={
        ("PATCH", "channels/100/messages/m1"): RuntimeError(
            f"token={secret}",
        ),
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "text", "confirm": True,
    })
    assert result.is_error is True
    assert secret not in result.content


# ===========================================================================
# discord_delete
# ===========================================================================

async def test_delete_without_confirm_returns_preview_no_http():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is False
    assert fetch.calls == []


async def test_delete_with_confirm_calls_delete_endpoint():
    fetch = FakeFetch()  # DELETE returns None (204)
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1", "confirm": True,
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is True
    assert payload["channel_id"] == "100"
    assert payload["message_id"] == "m1"
    assert len(fetch.calls) == 1
    call = fetch.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"].endswith("channels/100/messages/m1")


async def test_delete_ownership_error_surfaces_from_discord():
    fetch = FakeFetch(raises={
        ("DELETE", "channels/100/messages/m1"): RuntimeError(
            "HTTP 403 Forbidden -- Missing Permissions",
        ),
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1", "confirm": True,
    })
    assert result.is_error is True
    assert "403" in result.content or "Permissions" in result.content


async def test_delete_missing_required_args():
    plugin = _make_plugin()
    result = await plugin.call_tool("discord_delete", {
        "message_id": "m1", "confirm": True,
    })
    assert result.is_error is True
    assert "channel_id" in result.content


async def test_delete_token_scrubbed_from_error():
    secret = "SECRET-DELETE-TOKEN"
    fetch = FakeFetch(raises={
        ("DELETE", "channels/100/messages/m1"): RuntimeError(
            f"token={secret}",
        ),
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1", "confirm": True,
    })
    assert result.is_error is True
    assert secret not in result.content


# ===========================================================================
# on_manual_override seam
# ===========================================================================

async def test_set_presence_calls_on_manual_override():
    overrides: list[str] = []
    plugin = _make_plugin(on_manual_override=lambda s: overrides.append(s))
    result = await plugin.call_tool("discord_set_presence", {"status": "dnd"})
    assert result.is_error is False
    assert overrides == ["dnd"]


async def test_set_presence_no_callback_does_not_raise():
    plugin = _make_plugin(on_manual_override=None)
    result = await plugin.call_tool("discord_set_presence", {"status": "idle"})
    assert result.is_error is False


# ===========================================================================
# DiscordPresenceController -- state machine
# ===========================================================================

def _fixed_clock(start: float = 1000.0):
    state = {"t": start}

    def now() -> float:
        return state["t"]

    def advance(delta: float) -> None:
        state["t"] += delta

    now.advance = advance  # type: ignore[attr-defined]
    return now


def _recording_set_presence():
    presences: list[str] = []

    async def set_presence(status: str) -> None:
        presences.append(status)

    set_presence.calls = presences  # type: ignore[attr-defined]
    return set_presence


async def test_on_activity_sets_online():
    fn = _recording_set_presence()
    ctrl = DiscordPresenceController(set_presence_fn=fn)
    await ctrl.on_activity()
    assert fn.calls[-1] == "online"


async def test_on_activity_clears_manual_override():
    fn = _recording_set_presence()
    ctrl = DiscordPresenceController(set_presence_fn=fn)
    ctrl.notify_manual_override("dnd")
    assert ctrl._manual_override is True
    await ctrl.on_activity()
    assert ctrl._manual_override is False
    assert fn.calls[-1] == "online"


async def test_auto_idle_fires_after_threshold():
    fn = _recording_set_presence()
    clock = _fixed_clock(1000.0)
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        idle_after_s=600.0,
        clock=clock,
    )
    await ctrl.on_activity()
    assert fn.calls[-1] == "online"

    # Advance past the idle threshold then tick the check
    clock.advance(601.0)
    await ctrl.check()
    assert fn.calls[-1] == "idle"


async def test_auto_idle_does_not_fire_before_threshold():
    fn = _recording_set_presence()
    clock = _fixed_clock(1000.0)
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        idle_after_s=600.0,
        clock=clock,
    )
    await ctrl.on_activity()
    initial_count = len(fn.calls)
    clock.advance(599.0)  # just under threshold
    await ctrl.check()
    assert fn.calls[-1] == "online"  # no idle transition
    assert len(fn.calls) == initial_count  # no new calls (already online)


async def test_manual_override_blocks_idle_check():
    fn = _recording_set_presence()
    clock = _fixed_clock(1000.0)
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        idle_after_s=600.0,
        clock=clock,
    )
    await ctrl.on_activity()
    ctrl.notify_manual_override("dnd")
    clock.advance(601.0)
    await ctrl.check()
    # Manual override is set so check does nothing; last call was online
    assert fn.calls[-1] == "online"


async def test_next_activity_clears_manual_override_and_goes_online():
    fn = _recording_set_presence()
    ctrl = DiscordPresenceController(set_presence_fn=fn)
    # Establish manual override
    ctrl.notify_manual_override("dnd")
    assert ctrl._manual_override is True
    # Next LLM Discord action clears it
    await ctrl.on_activity()
    assert ctrl._manual_override is False
    assert fn.calls[-1] == "online"


async def test_sleep_window_forces_invisible_on_activity():
    fn = _recording_set_presence()
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        is_in_sleep_window=lambda: True,
    )
    await ctrl.on_activity()
    assert fn.calls[-1] == "invisible"


async def test_sleep_window_forces_invisible_in_idle_check():
    fn = _recording_set_presence()
    in_sleep = [True]
    clock = _fixed_clock(1000.0)
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        idle_after_s=600.0,
        is_in_sleep_window=lambda: in_sleep[0],
        clock=clock,
    )
    # Simulate some prior activity so _last_activity is set
    ctrl._last_activity = 1000.0
    await ctrl.check()
    assert fn.calls[-1] == "invisible"


async def test_sleep_window_wins_over_idle_when_in_window():
    """Inside the sleep window, check() sets invisible rather than idle."""
    fn = _recording_set_presence()
    in_sleep = [True]
    clock = _fixed_clock(1000.0)
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        idle_after_s=600.0,
        is_in_sleep_window=lambda: in_sleep[0],
        clock=clock,
    )
    ctrl._last_activity = 0.0  # far in the past -> would idle if no window
    await ctrl.check()
    assert fn.calls[-1] == "invisible"
    assert "idle" not in fn.calls


async def test_sleep_window_outside_window_allows_idle():
    fn = _recording_set_presence()
    clock = _fixed_clock(1000.0)
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        idle_after_s=600.0,
        is_in_sleep_window=lambda: False,
        clock=clock,
    )
    ctrl._last_activity = 0.0  # far in the past
    await ctrl.check()
    assert fn.calls[-1] == "idle"


async def test_check_no_activity_is_noop():
    """If on_activity() was never called, check() does nothing."""
    fn = _recording_set_presence()
    ctrl = DiscordPresenceController(set_presence_fn=fn)
    await ctrl.check()
    assert fn.calls == []


async def test_deduplication_does_not_re_set_same_presence():
    """check() only calls set_presence when the status actually changes."""
    fn = _recording_set_presence()
    clock = _fixed_clock(1000.0)
    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        idle_after_s=600.0,
        clock=clock,
    )
    await ctrl.on_activity()  # -> online (1 call)
    clock.advance(601.0)
    await ctrl.check()        # -> idle (2nd call)
    await ctrl.check()        # already idle -- no third call
    assert fn.calls == ["online", "idle"]


async def test_background_loop_start_stop():
    """start() creates the loop task; stop() cancels it without raising."""
    fn = _recording_set_presence()

    async def slow_sleep(delay: float) -> None:
        await asyncio.sleep(999)  # will be cancelled

    ctrl = DiscordPresenceController(
        set_presence_fn=fn,
        sleep=slow_sleep,
        check_interval_s=999,
    )
    await ctrl.start()
    assert ctrl._loop_task is not None
    assert not ctrl._loop_task.done()
    await ctrl.stop()
    assert ctrl._loop_task is None


async def test_background_loop_start_idempotent():
    """Calling start() twice does not create a second loop task."""
    fn = _recording_set_presence()

    async def slow_sleep(delay: float) -> None:
        await asyncio.sleep(999)

    ctrl = DiscordPresenceController(set_presence_fn=fn, sleep=slow_sleep)
    await ctrl.start()
    task1 = ctrl._loop_task
    await ctrl.start()
    task2 = ctrl._loop_task
    assert task1 is task2
    await ctrl.stop()
