"""Tests for Discord-as-user slice 3 -- Issue #178, ADR-0006.

Two layers covered:

1. New ``plugins/discord_user.py`` MCP tools (``discord_react`` /
   ``discord_edit`` / ``discord_delete``). All three are ``irreversible=True``
   and ``confirm``-gated, mirroring slice 1's ``discord_send_message``.
   Ownership constraints on edit/delete are enforced by Discord
   server-side; the plugin surfaces the error rather than catches.

2. ``cerebral.discord_presence.DiscordPresenceController`` -- the
   auto-presence state machine. Auto-idle fires after the configured
   threshold; auto-online fires on the next activity tick; manual
   override survives until the next auto-trigger; sleep-hours wins.

No live network. Plugin transport is faked via ``fetch_fn`` injection,
mirroring slice 1/2 test fixtures.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import pytest

from cerebral.discord_auto_reply import (
    SETTING_SLEEP_END_HOUR,
    SETTING_SLEEP_START_HOUR,
)
from cerebral.discord_presence import (
    DiscordPresenceController,
    PresenceSettings,
    SETTING_AUTO_IDLE_THRESHOLD_S,
    SETTING_AUTO_PRESENCE_CHECK_INTERVAL_S,
    SETTING_AUTO_PRESENCE_ENABLED,
    presence_settings_from_overrides,
    run_presence_loop,
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
    *, token: str = "TOK", fetch: Optional[FakeFetch] = None,
):
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    return plugin_cls(
        token_provider=_StaticTokenProvider(token),
        fetch_fn=fetch,
    )


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Clear module-level setters between tests so order doesn't matter.
    The slice-3 seams (set_activity_callback, set_manual_presence_callback)
    are intentionally process-global -- isolate them per test."""
    PLUGIN_MOD.set_token_provider(lambda: None)
    PLUGIN_MOD.set_activity_callback(None)
    PLUGIN_MOD.set_manual_presence_callback(None)
    yield
    PLUGIN_MOD.set_token_provider(lambda: None)
    PLUGIN_MOD.set_activity_callback(None)
    PLUGIN_MOD.set_manual_presence_callback(None)


# ===========================================================================
# Tool surface: shape, count, irreversible declarations
# ===========================================================================

def test_list_tools_now_returns_seven_tools_with_slice3_additions():
    plugin = _make_plugin()
    names = [t.name for t in plugin.list_tools()]
    assert set(names) == {
        "discord_list_conversations",
        "discord_get_messages",
        "discord_send_message",
        "discord_react",
        "discord_edit",
        "discord_delete",
        "discord_set_presence",
    }


def test_slice3_tools_are_marked_irreversible():
    plugin = _make_plugin()
    tools = {t.name: t for t in plugin.list_tools()}
    assert tools["discord_react"].irreversible is True
    assert tools["discord_edit"].irreversible is True
    assert tools["discord_delete"].irreversible is True
    # And the slice-1 send is still irreversible.
    assert tools["discord_send_message"].irreversible is True
    # Read-only tools still aren't.
    assert tools["discord_list_conversations"].irreversible is False
    assert tools["discord_get_messages"].irreversible is False
    assert tools["discord_set_presence"].irreversible is False


def test_required_capabilities_unchanged_in_slice3():
    """Acceptance: slice 3 adds no new capability declaration --
    external_data_write already covers reactions / edits / deletes."""
    assert PLUGIN_MOD.REQUIRED_CAPABILITIES == frozenset({
        "secrets_read",
        "external_data_read",
        "external_data_write",
        "network_egress_cloud",
    })


# ===========================================================================
# discord_react -- the confirm gate, add/remove, error surfacing
# ===========================================================================

async def test_react_without_confirm_returns_preview_no_http_call():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is False
    assert payload["emoji"] == "\U0001F44D"
    assert payload["action"] == "add"
    assert fetch.calls == []


async def test_react_add_puts_to_at_me_endpoint_with_confirm():
    fetch = FakeFetch()  # 204 no-content equivalent: returns None
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
        "action": "add", "confirm": True,
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is True
    assert len(fetch.calls) == 1
    call = fetch.calls[0]
    assert call["method"] == "PUT"
    # Path ends with /@me. Emoji URL-encoded.
    assert call["url"].endswith("/reactions/%F0%9F%91%8D/@me")


async def test_react_remove_uses_delete_method():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
        "action": "remove", "confirm": True,
    })
    assert fetch.calls[0]["method"] == "DELETE"


async def test_react_custom_emoji_name_id_format_passes_through():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1",
        "emoji": "thumbsup:12345", "confirm": True,
    })
    # ``name:id`` is left intact -- the colon is safe.
    assert "/reactions/thumbsup:12345/@me" in fetch.calls[0]["url"]


async def test_react_rejects_invalid_action():
    plugin = _make_plugin(fetch=FakeFetch())
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
        "action": "bogus", "confirm": True,
    })
    assert result.is_error is True


async def test_react_missing_args_is_error():
    plugin = _make_plugin(fetch=FakeFetch())
    for missing in ("channel_id", "message_id", "emoji"):
        args = {
            "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
            "confirm": True,
        }
        args.pop(missing)
        result = await plugin.call_tool("discord_react", args)
        assert result.is_error is True
        assert missing in result.content


async def test_react_surfaces_discord_error_on_failure():
    """Discord returns an error if removing a reaction you never added.
    The plugin surfaces it rather than catches -- the LLM sees the
    failure verbatim (modulo token scrubbing)."""
    fetch = FakeFetch(raises={
        ("DELETE", "/@me"): RuntimeError("HTTP 404 unknown reaction"),
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
        "action": "remove", "confirm": True,
    })
    assert result.is_error is True
    assert "404" in result.content or "unknown reaction" in result.content


# ===========================================================================
# discord_edit -- the confirm gate, ownership-error surfacing
# ===========================================================================

async def test_edit_without_confirm_returns_preview_no_http_call():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "fixed typo",
    })
    payload = json.loads(result.content)
    assert payload["confirmed"] is False
    assert payload["preview"] == "fixed typo"
    assert fetch.calls == []


async def test_edit_with_confirm_patches_and_returns_message():
    fetch = FakeFetch(responses={
        ("PATCH", "channels/100/messages/m1"): {
            "id": "m1", "channel_id": "100",
            "author": {"id": "1", "username": "felix"},
            "content": "fixed typo",
            "timestamp": "2026-05-28T10:00:00Z",
            "edited_timestamp": "2026-05-28T10:01:00Z",
        },
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "fixed typo", "confirm": True,
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is True
    assert payload["message"]["content"] == "fixed typo"
    assert fetch.calls[0]["method"] == "PATCH"
    assert fetch.calls[0]["json"] == {"content": "fixed typo"}


async def test_edit_surfaces_discord_ownership_error():
    """Discord rejects edits to non-owned messages (50005). Surface, don't
    catch."""
    fetch = FakeFetch(raises={
        ("PATCH", "channels/100/messages/m1"):
            RuntimeError("HTTP 403 cannot edit a message authored by another user"),
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "nope", "confirm": True,
    })
    assert result.is_error is True
    assert "403" in result.content or "another user" in result.content


async def test_edit_missing_args_is_error():
    plugin = _make_plugin(fetch=FakeFetch())
    for missing in ("channel_id", "message_id", "new_content"):
        args = {
            "channel_id": "100", "message_id": "m1",
            "new_content": "x", "confirm": True,
        }
        args.pop(missing)
        result = await plugin.call_tool("discord_edit", args)
        assert result.is_error is True


# ===========================================================================
# discord_delete -- the confirm gate, ownership-error surfacing
# ===========================================================================

async def test_delete_without_confirm_no_http_call():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1",
    })
    payload = json.loads(result.content)
    assert payload["confirmed"] is False
    assert fetch.calls == []


async def test_delete_with_confirm_calls_delete_method():
    fetch = FakeFetch()  # 204 -> None
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1", "confirm": True,
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["confirmed"] is True
    assert fetch.calls[0]["method"] == "DELETE"
    assert fetch.calls[0]["url"].endswith("channels/100/messages/m1")


async def test_delete_surfaces_discord_ownership_error():
    fetch = FakeFetch(raises={
        ("DELETE", "channels/100/messages/m1"):
            RuntimeError("HTTP 403 cannot delete a message authored by another user"),
    })
    plugin = _make_plugin(fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1", "confirm": True,
    })
    assert result.is_error is True
    assert "403" in result.content or "another user" in result.content


async def test_delete_missing_args_is_error():
    plugin = _make_plugin(fetch=FakeFetch())
    for missing in ("channel_id", "message_id"):
        args = {
            "channel_id": "100", "message_id": "m1", "confirm": True,
        }
        args.pop(missing)
        result = await plugin.call_tool("discord_delete", args)
        assert result.is_error is True


# ===========================================================================
# Token scrubbing for the new tools
# ===========================================================================

async def test_react_scrubs_token_from_errors():
    secret = "USER-TOKEN-SLICE3-REACT"
    fetch = FakeFetch(raises={
        ("PUT", "/@me"): RuntimeError(f"talk to discord with token={secret}"),
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    result = await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
        "confirm": True,
    })
    assert result.is_error is True
    assert secret not in result.content


async def test_edit_scrubs_token_from_errors():
    secret = "USER-TOKEN-SLICE3-EDIT"
    fetch = FakeFetch(raises={
        ("PATCH", "channels/100/messages/m1"):
            RuntimeError(f"token={secret} oh no"),
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    result = await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "x", "confirm": True,
    })
    assert result.is_error is True
    assert secret not in result.content


async def test_delete_scrubs_token_from_errors():
    secret = "USER-TOKEN-SLICE3-DELETE"
    fetch = FakeFetch(raises={
        ("DELETE", "channels/100/messages/m1"):
            RuntimeError(f"oh no token={secret}"),
    })
    plugin = _make_plugin(token=secret, fetch=fetch)
    result = await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1", "confirm": True,
    })
    assert result.is_error is True
    assert secret not in result.content


# ===========================================================================
# Activity-callback seam -- fired after confirm gate clears
# ===========================================================================

async def test_send_message_fires_activity_callback_on_confirm():
    fetch = FakeFetch(responses={
        ("POST", "channels/100/messages"): {
            "id": "m1", "channel_id": "100",
            "author": {"id": "1", "username": "felix"},
            "content": "hi", "timestamp": "2026-05-28T10:00:00Z",
        },
    })
    plugin = _make_plugin(fetch=fetch)
    ticks: list[int] = []

    async def cb() -> None:
        ticks.append(1)

    PLUGIN_MOD.set_activity_callback(cb)
    await plugin.call_tool("discord_send_message", {
        "channel_id": "100", "content": "hi", "confirm": True,
    })
    assert ticks == [1]


async def test_send_message_without_confirm_does_not_fire_activity():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    ticks: list[int] = []

    async def cb() -> None:
        ticks.append(1)

    PLUGIN_MOD.set_activity_callback(cb)
    await plugin.call_tool("discord_send_message", {
        "channel_id": "100", "content": "hi",
    })
    assert ticks == []  # confirm gate short-circuited before tick


async def test_react_edit_delete_each_fire_activity():
    fetch = FakeFetch(responses={
        ("PATCH", "channels/100/messages/m1"): {
            "id": "m1", "channel_id": "100",
            "author": {"id": "1"}, "content": "x",
        },
    })
    plugin = _make_plugin(fetch=fetch)
    ticks: list[str] = []

    async def cb() -> None:
        ticks.append("tick")

    PLUGIN_MOD.set_activity_callback(cb)
    await plugin.call_tool("discord_react", {
        "channel_id": "100", "message_id": "m1", "emoji": "\U0001F44D",
        "confirm": True,
    })
    await plugin.call_tool("discord_edit", {
        "channel_id": "100", "message_id": "m1",
        "new_content": "x", "confirm": True,
    })
    await plugin.call_tool("discord_delete", {
        "channel_id": "100", "message_id": "m1", "confirm": True,
    })
    assert ticks == ["tick", "tick", "tick"]


async def test_activity_callback_exceptions_swallowed():
    """A misbehaving controller must not break the tool path."""
    fetch = FakeFetch(responses={
        ("POST", "channels/100/messages"): {
            "id": "m1", "channel_id": "100",
            "author": {"id": "1"}, "content": "hi",
        },
    })
    plugin = _make_plugin(fetch=fetch)

    async def boom() -> None:
        raise RuntimeError("controller down")

    PLUGIN_MOD.set_activity_callback(boom)
    result = await plugin.call_tool("discord_send_message", {
        "channel_id": "100", "content": "hi", "confirm": True,
    })
    assert result.is_error is False  # tool path unaffected


# ===========================================================================
# Manual-override seam -- set_presence flows through the controller
# ===========================================================================

async def test_set_presence_routes_through_manual_callback_when_wired():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    seen: list[str] = []

    async def cb(status: str) -> bool:
        seen.append(status)
        return True

    PLUGIN_MOD.set_manual_presence_callback(cb)
    result = await plugin.call_tool("discord_set_presence", {
        "status": "dnd",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["applied"] is True
    assert payload.get("manual_override") is True
    assert seen == ["dnd"]


async def test_set_presence_falls_back_when_callback_declines():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)

    async def cb(status: str) -> bool:
        return False  # controller not wired / declined

    PLUGIN_MOD.set_manual_presence_callback(cb)
    result = await plugin.call_tool("discord_set_presence", {
        "status": "idle",
    })
    assert result.is_error is False
    payload = json.loads(result.content)
    # We get the "delegate declined" branch, not slice-1's applied=False.
    assert payload["applied"] is False
    assert "declined" in payload.get("note", "")


async def test_set_presence_without_callback_keeps_slice1_behaviour():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    # Callback explicitly not wired (autouse fixture).
    result = await plugin.call_tool("discord_set_presence", {
        "status": "idle",
    })
    payload = json.loads(result.content)
    # Slice 1: subscriber not running -> applied=False, "next connect" note.
    assert payload["status"] == "idle"
    assert payload["applied"] is False
    assert "next connect" in payload.get("note", "")


# ===========================================================================
# DiscordPresenceController -- the state machine
# ===========================================================================

class RecordingPresenceSender:
    def __init__(self, *, ok: bool = True) -> None:
        self.applied: list[str] = []
        self.ok = ok

    async def apply_presence(self, status: str) -> bool:
        self.applied.append(status)
        return self.ok


def _fixed_clock(start: float = 1000.0):
    state = {"t": start}

    def now() -> float:
        return state["t"]

    def advance(delta: float) -> None:
        state["t"] += delta

    now.advance = advance  # type: ignore[attr-defined]
    return now


def _settings(
    *,
    threshold: float = 60.0,
    enabled: bool = True,
    interval: float = 30.0,
) -> PresenceSettings:
    return PresenceSettings(
        auto_idle_threshold_s=threshold,
        auto_presence_enabled=enabled,
        auto_presence_check_interval_s=interval,
    )


def _controller(
    *,
    sender: Optional[RecordingPresenceSender] = None,
    settings: Optional[PresenceSettings] = None,
    sleep_hours: tuple[Optional[int], Optional[int]] = (None, None),
    hour: int = 12,
    clock=None,
):
    sender = sender or RecordingPresenceSender()
    settings = settings or _settings()
    return (
        DiscordPresenceController(
            sender=sender,
            get_presence_settings=lambda: settings,
            sleep_hours=lambda: sleep_hours,
            local_hour=lambda: hour,
            clock=clock,
        ),
        sender,
    )


def test_presence_settings_defaults():
    s = presence_settings_from_overrides({})
    assert s.auto_idle_threshold_s == 300.0
    assert s.auto_presence_enabled is True
    assert s.auto_presence_check_interval_s == 30.0


def test_presence_settings_override_parsing():
    s = presence_settings_from_overrides({
        SETTING_AUTO_IDLE_THRESHOLD_S: "120",
        SETTING_AUTO_PRESENCE_ENABLED: "off",
        SETTING_AUTO_PRESENCE_CHECK_INTERVAL_S: "10",
    })
    assert s.auto_idle_threshold_s == 120.0
    assert s.auto_presence_enabled is False
    assert s.auto_presence_check_interval_s == 10.0


async def test_tick_activity_applies_online():
    ctrl, sender = _controller()
    await ctrl.tick_activity()
    assert sender.applied == ["online"]
    assert ctrl.current_presence == "online"


async def test_tick_check_with_no_activity_applies_idle():
    ctrl, sender = _controller()
    await ctrl.tick_check()
    assert sender.applied == ["idle"]


async def test_auto_idle_after_threshold():
    """Acceptance: auto-idle fires after the configured no-activity
    threshold (mocked clock); auto-online fires on the next LLM-driven
    Discord action."""
    clock = _fixed_clock(1000.0)
    ctrl, sender = _controller(
        settings=_settings(threshold=60.0), clock=clock,
    )

    # t=0: activity tick -> online.
    await ctrl.tick_activity()
    assert ctrl.current_presence == "online"

    # t=30s: still within threshold -- check is no-op.
    clock.advance(30.0)
    await ctrl.tick_check()
    assert ctrl.current_presence == "online"  # unchanged

    # t=70s: past threshold -- auto-idle.
    clock.advance(40.0)
    await ctrl.tick_check()
    assert ctrl.current_presence == "idle"

    # Next activity tick -- auto-online.
    await ctrl.tick_activity()
    assert ctrl.current_presence == "online"

    # The full applied sequence (no duplicate flips).
    assert sender.applied == ["online", "idle", "online"]


async def test_no_redundant_flip_when_already_at_target():
    """Two consecutive activity ticks must NOT spam change_presence --
    detection eats redundant flips for breakfast."""
    ctrl, sender = _controller()
    await ctrl.tick_activity()
    await ctrl.tick_activity()
    await ctrl.tick_activity()
    assert sender.applied == ["online"]  # exactly one wire call


async def test_manual_override_survives_until_next_auto_trigger():
    """Acceptance: manual ``discord_set_presence`` overrides auto-presence
    until the next auto-trigger."""
    clock = _fixed_clock(1000.0)
    ctrl, sender = _controller(
        settings=_settings(threshold=60.0), clock=clock,
    )

    # Auto goes online.
    await ctrl.tick_activity()
    assert ctrl.current_presence == "online"
    assert ctrl.manual_override_active is False

    # Manual override to dnd.
    await ctrl.apply_manual_override("dnd")
    assert ctrl.current_presence == "dnd"
    assert ctrl.manual_override_active is True

    # Within the no-activity threshold, a periodic check is a no-op --
    # the override stays.
    clock.advance(10.0)
    await ctrl.tick_check()
    assert ctrl.current_presence == "dnd"
    assert ctrl.manual_override_active is True

    # The first auto-trigger after override: another activity tick.
    await ctrl.tick_activity()
    assert ctrl.current_presence == "online"
    assert ctrl.manual_override_active is False


async def test_manual_override_cleared_by_idle_threshold_passing():
    """The idle threshold expiring IS an auto-trigger -- it clears the
    override."""
    clock = _fixed_clock(1000.0)
    ctrl, sender = _controller(
        settings=_settings(threshold=60.0), clock=clock,
    )
    await ctrl.tick_activity()
    await ctrl.apply_manual_override("dnd")
    assert ctrl.manual_override_active is True

    # Push the clock well past the threshold without any activity ticks.
    clock.advance(200.0)
    await ctrl.tick_check()
    assert ctrl.current_presence == "idle"
    assert ctrl.manual_override_active is False


async def test_sleep_window_forces_invisible_over_activity():
    """Acceptance: sleep-hours window wins over auto-presence. Inside
    the window, presence stays invisible even if the LLM is active."""
    ctrl, sender = _controller(
        sleep_hours=(22, 7),
        hour=3,  # inside the 22-07 window
    )
    await ctrl.tick_activity()
    assert ctrl.current_presence == "invisible"
    # A periodic check in-window also forces invisible.
    await ctrl.tick_check()
    assert sender.applied == ["invisible"]  # no redundant flip


async def test_sleep_window_outside_lets_activity_through():
    ctrl, sender = _controller(
        sleep_hours=(22, 7),
        hour=12,  # outside the window
    )
    await ctrl.tick_activity()
    assert ctrl.current_presence == "online"


async def test_sleep_window_check_forces_invisible_clearing_override():
    """A manual override into the sleep window is overridden by the
    next sleep-window auto-trigger -- the sleep window is the strongest
    auto-trigger."""
    ctrl, sender = _controller(
        sleep_hours=(22, 7),
        hour=3,
    )
    await ctrl.apply_manual_override("dnd")
    assert ctrl.current_presence == "dnd"
    assert ctrl.manual_override_active is True
    # Periodic check inside sleep window -> invisible, override cleared.
    await ctrl.tick_check()
    assert ctrl.current_presence == "invisible"
    assert ctrl.manual_override_active is False


async def test_auto_presence_disabled_means_no_op():
    ctrl, sender = _controller(settings=_settings(enabled=False))
    await ctrl.tick_activity()
    await ctrl.tick_check()
    assert sender.applied == []
    assert ctrl.current_presence is None


async def test_run_presence_loop_calls_tick_check_then_stops():
    """Drive ``run_presence_loop`` with a very short interval -- the
    wait_for(stop) inside the loop times out, the loop ticks, and the
    next iteration's wait_for catches the stop and exits cleanly."""
    ctrl, sender = _controller()
    stop = asyncio.Event()
    settings = _settings(interval=0.02)
    loop_task = asyncio.create_task(
        run_presence_loop(
            ctrl,
            lambda: settings,
            stop_event=stop,
        )
    )
    # Wait long enough for the loop to time out at least once and tick.
    await asyncio.sleep(0.08)
    stop.set()
    await asyncio.wait_for(loop_task, timeout=1.0)
    # tick_check fired -> applied at least "idle" (no activity yet).
    assert "idle" in sender.applied
