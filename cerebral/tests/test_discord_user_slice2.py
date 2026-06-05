"""Tests for Discord-as-user slice 2 -- Issue #177, ADR-0006.

Three layers covered:

1. ``cerebral.db.profiles`` allowlist + settings CRUD.
2. ``cerebral.discord_auto_reply.DiscordAutoReplyController`` -- the
   policy gauntlet (allowlist routing, distribution sampling, typing
   indicator emission, rate-limit fallback, sleep-hours fallback,
   per-channel serialisation).
3. ``plugins/discord_user`` slice-2 internal sender methods
   (``trigger_typing`` + ``send_dm``).

No live network. The plugin's transport is faked via ``fetch_fn``
injection, mirroring slice 1's ``test_discord_user_plugin.py``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import itertools
import statistics
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

import pytest

from cerebral.db.profiles import ProfileManager
from cerebral.discord_auto_reply import (
    AutoReplySettings,
    DiscordAutoReplyController,
    SETTING_DELAY_MAX_S,
    SETTING_DELAY_MEAN_S,
    SETTING_DELAY_MIN_S,
    SETTING_DELAY_SIGMA,
    SETTING_RATE_LIMIT_MAX,
    SETTING_RATE_LIMIT_WINDOW_S,
    SETTING_SLEEP_END_HOUR,
    SETTING_SLEEP_START_HOUR,
    SETTING_TYPING_INDICATOR,
    _hour_in_sleep_window,
    settings_from_overrides,
)


# ---------------------------------------------------------------------------
# Plugin loader -- mirror slice-1 importlib pattern
# ---------------------------------------------------------------------------

def _load_plugin_module():
    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "discord_user.py"
    spec = importlib.util.spec_from_file_location(
        "openmind_plugin_discord_user_slice2", plugin_path,
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
    """Same shape as the slice-1 fixture -- (method, suffix) keyed
    response map, plus a ``calls`` list for assertion."""

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


def _make_plugin(*, token: str = "TOK", fetch: Optional[FakeFetch] = None):
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    return plugin_cls(
        token_provider=_StaticTokenProvider(token),
        fetch_fn=fetch,
    )


# ---------------------------------------------------------------------------
# Recording sender -- captures controller -> plugin interactions
# ---------------------------------------------------------------------------

class RecordingSender:
    def __init__(self, *, send_ok: bool = True) -> None:
        self.typing_calls: list[str] = []
        self.send_calls: list[tuple[str, str]] = []
        self.send_ok = send_ok

    async def trigger_typing(self, channel_id: str) -> None:
        self.typing_calls.append(channel_id)

    async def send_dm(self, channel_id: str, content: str) -> bool:
        self.send_calls.append((channel_id, content))
        return self.send_ok


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = settings_from_overrides({})


def _settings(**over) -> AutoReplySettings:
    """Build settings from defaults with a few keys overridden."""
    base = settings_from_overrides({})
    return base.__class__(**{**base.__dict__, **over})


def _fixed_clock(start: float = 1000.0) -> Callable[[], float]:
    state = {"t": start}

    def now() -> float:
        return state["t"]

    def advance(delta: float) -> None:
        state["t"] += delta

    now.advance = advance  # type: ignore[attr-defined]
    return now


def _no_sleep():
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    sleep.calls = sleeps  # type: ignore[attr-defined]
    return sleep


def _event(*, author_id: str = "AUTHOR", channel_id: str = "CH1",
           text: str = "hi") -> dict:
    return {
        "channel": "discord_user",
        "session_key": f"discord_user:dm:{channel_id}",
        "channel_id": channel_id,
        "channel_type": "private",
        "message_id": "M1",
        "author_id": author_id,
        "author_name": "Sender",
        "text": text,
    }


def _controller(
    *,
    sender: Optional[RecordingSender] = None,
    reply: str = "auto-reply text",
    allowlist: Optional[set[str]] = None,
    settings: Optional[AutoReplySettings] = None,
    hour: int = 12,
    clock=None,
    sleep=None,
    random_fn=None,
):
    sender = sender or RecordingSender()
    allowlist = allowlist if allowlist is not None else {"AUTHOR"}
    settings = settings or settings_from_overrides({})

    async def reply_generator(event: dict) -> str:
        return reply

    ctrl = DiscordAutoReplyController(
        sender=sender,
        reply_generator=reply_generator,
        is_allowlisted=lambda aid: aid in allowlist,
        get_settings=lambda: settings,
        local_hour=lambda: hour,
        clock=clock,
        sleep=sleep,
        random_fn=random_fn,
    )
    return ctrl, sender


# ===========================================================================
# ProfileManager -- allowlist + settings CRUD
# ===========================================================================

def _fresh_pm() -> ProfileManager:
    tmp = Path(tempfile.mkdtemp()) / "openmind.db"
    return ProfileManager(db_path=tmp)


def test_allowlist_add_then_list_then_remove():
    pm = _fresh_pm()
    p = pm.create(name="Alice")
    assert pm.list_discord_allowlist(p.id) == []
    pm.add_discord_allowlist(p.id, "USER_42", note="my partner")
    rows = pm.list_discord_allowlist(p.id)
    assert len(rows) == 1
    assert rows[0]["sender_id"] == "USER_42"
    assert rows[0]["note"] == "my partner"
    assert pm.is_discord_allowlisted(p.id, "USER_42") is True
    assert pm.is_discord_allowlisted(p.id, "OTHER") is False
    assert pm.remove_discord_allowlist(p.id, "USER_42") is True
    assert pm.list_discord_allowlist(p.id) == []
    # Removing again returns False (idempotent / "no row").
    assert pm.remove_discord_allowlist(p.id, "USER_42") is False


def test_allowlist_is_scoped_per_profile():
    pm = _fresh_pm()
    a = pm.create(name="Alice")
    b = pm.create(name="Bob")
    pm.add_discord_allowlist(a.id, "USER_42")
    assert pm.is_discord_allowlisted(a.id, "USER_42") is True
    assert pm.is_discord_allowlisted(b.id, "USER_42") is False


def test_settings_set_get_list_clear():
    pm = _fresh_pm()
    p = pm.create(name="Alice")
    assert pm.list_discord_settings(p.id) == {}
    pm.set_discord_setting(p.id, SETTING_RATE_LIMIT_MAX, "10")
    assert pm.get_discord_setting(p.id, SETTING_RATE_LIMIT_MAX) == "10"
    pm.set_discord_setting(p.id, SETTING_RATE_LIMIT_MAX, "5")  # update
    assert pm.list_discord_settings(p.id) == {SETTING_RATE_LIMIT_MAX: "5"}
    assert pm.clear_discord_setting(p.id, SETTING_RATE_LIMIT_MAX) is True
    assert pm.list_discord_settings(p.id) == {}


def test_settings_from_overrides_merges_with_defaults():
    settings = settings_from_overrides({
        SETTING_RATE_LIMIT_MAX: "9",
        SETTING_SLEEP_START_HOUR: "22",
        SETTING_SLEEP_END_HOUR: "7",
        SETTING_TYPING_INDICATOR: "off",
    })
    assert settings.rate_limit_max == 9
    assert settings.sleep_start_hour == 22
    assert settings.sleep_end_hour == 7
    assert settings.typing_indicator is False
    # Untouched keys fall back to defaults.
    assert settings.delay_min_s == 2.0


# ===========================================================================
# Allowlist routing
# ===========================================================================

async def test_non_allowlisted_sender_falls_through():
    ctrl, sender = _controller(allowlist=set())  # nobody on the allowlist
    consumed = await ctrl.handle_inbound(_event(author_id="OUTSIDER"))
    assert consumed is False
    assert sender.send_calls == []
    assert sender.typing_calls == []


async def test_empty_allowlist_drops_every_event():
    """Conservative default: an empty allowlist means the controller
    NEVER calls send/typing/LLM and always returns False so the caller
    drops the event."""
    ctrl, sender = _controller(allowlist=set())
    for i in range(5):
        out = await ctrl.handle_inbound(_event(author_id=f"U{i}"))
        assert out is False
    assert sender.send_calls == []
    assert sender.typing_calls == []


async def test_allowlisted_sender_auto_replies():
    sleep = _no_sleep()
    ctrl, sender = _controller(
        allowlist={"AUTHOR"}, sleep=sleep, random_fn=lambda: 0.5,
    )
    consumed = await ctrl.handle_inbound(_event())
    assert consumed is True
    assert sender.send_calls == [("CH1", "auto-reply text")]


async def test_event_missing_fields_falls_through():
    ctrl, sender = _controller(allowlist={"AUTHOR"})
    assert await ctrl.handle_inbound({"author_id": "AUTHOR"}) is False  # no channel
    assert await ctrl.handle_inbound({"channel_id": "CH1"}) is False  # no author
    assert sender.send_calls == []


# ===========================================================================
# Sleep-hours fallback
# ===========================================================================

def test_sleep_window_basic():
    assert _hour_in_sleep_window(3, 22, 7) is True
    assert _hour_in_sleep_window(22, 22, 7) is True
    assert _hour_in_sleep_window(7, 22, 7) is False  # exclusive end
    assert _hour_in_sleep_window(12, 22, 7) is False
    # Non-wrap window.
    assert _hour_in_sleep_window(10, 9, 17) is True
    assert _hour_in_sleep_window(17, 9, 17) is False
    # Either bound None => off.
    assert _hour_in_sleep_window(3, None, 7) is False
    assert _hour_in_sleep_window(3, 22, None) is False


async def test_sleep_hours_falls_back_to_draft():
    settings = _settings(sleep_start_hour=22, sleep_end_hour=7)
    ctrl, sender = _controller(allowlist={"AUTHOR"}, settings=settings, hour=3)
    consumed = await ctrl.handle_inbound(_event())
    assert consumed is False  # caller drops the event
    assert sender.send_calls == []
    assert sender.typing_calls == []


async def test_sleep_hours_off_lets_reply_through():
    settings = _settings(sleep_start_hour=22, sleep_end_hour=7)
    sleep = _no_sleep()
    ctrl, sender = _controller(
        allowlist={"AUTHOR"}, settings=settings, hour=12,
        sleep=sleep, random_fn=lambda: 0.5,
    )
    consumed = await ctrl.handle_inbound(_event())
    assert consumed is True
    assert sender.send_calls == [("CH1", "auto-reply text")]


# ===========================================================================
# Per-channel rate-limit fallback
# ===========================================================================

async def test_rate_limit_falls_back_after_budget_exhausted():
    settings = _settings(
        rate_limit_max=2, rate_limit_window_s=60.0,
    )
    clock = _fixed_clock(1000.0)
    sleep = _no_sleep()
    ctrl, sender = _controller(
        allowlist={"AUTHOR"}, settings=settings,
        clock=clock, sleep=sleep, random_fn=lambda: 0.5,
    )
    # Within window, channel CH1 has budget 2. The third inbound should
    # drop (NOT queue, NOT auto-reply).
    assert await ctrl.handle_inbound(_event(channel_id="CH1")) is True
    clock.advance(1.0)
    assert await ctrl.handle_inbound(_event(channel_id="CH1")) is True
    clock.advance(1.0)
    consumed = await ctrl.handle_inbound(_event(channel_id="CH1"))
    assert consumed is False  # over budget
    assert len(sender.send_calls) == 2


async def test_rate_limit_resets_after_window_passes():
    settings = _settings(rate_limit_max=2, rate_limit_window_s=10.0)
    clock = _fixed_clock(1000.0)
    sleep = _no_sleep()
    ctrl, sender = _controller(
        allowlist={"AUTHOR"}, settings=settings,
        clock=clock, sleep=sleep, random_fn=lambda: 0.5,
    )
    await ctrl.handle_inbound(_event(channel_id="CH1"))
    await ctrl.handle_inbound(_event(channel_id="CH1"))
    clock.advance(20.0)  # past the rolling window
    consumed = await ctrl.handle_inbound(_event(channel_id="CH1"))
    assert consumed is True
    assert len(sender.send_calls) == 3


async def test_rate_limit_scoped_per_channel():
    settings = _settings(rate_limit_max=1, rate_limit_window_s=60.0)
    clock = _fixed_clock(1000.0)
    sleep = _no_sleep()
    ctrl, sender = _controller(
        allowlist={"AUTHOR"}, settings=settings,
        clock=clock, sleep=sleep, random_fn=lambda: 0.5,
    )
    # Each channel gets its own budget.
    assert await ctrl.handle_inbound(_event(channel_id="CH1")) is True
    assert await ctrl.handle_inbound(_event(channel_id="CH2")) is True
    # Second hit on CH1 falls back.
    assert await ctrl.handle_inbound(_event(channel_id="CH1")) is False


# ===========================================================================
# Reply-delay distribution
# ===========================================================================

async def test_reply_delay_actually_varies():
    """Pump 50 inbound events through the controller and assert the
    sleep durations have non-trivial variance -- the distribution is
    NOT a constant. Acceptance: 'distribution is actually sampled'."""
    settings = _settings(rate_limit_max=10_000, rate_limit_window_s=1.0)
    sleep_calls: list[float] = []

    async def sleep(delay: float) -> None:
        sleep_calls.append(delay)

    # Deterministic non-constant RNG: walks across (0,1).
    counter = iter(itertools.cycle([0.1, 0.25, 0.5, 0.75, 0.9]))

    def random_fn() -> float:
        return next(counter)

    ctrl, _ = _controller(
        allowlist={"AUTHOR"}, settings=settings,
        sleep=sleep, random_fn=random_fn,
    )
    for i in range(50):
        await ctrl.handle_inbound(_event(channel_id=f"C{i}"))
    assert len(sleep_calls) == 50
    # Stdev > 0.1s on any plausible log-normal centered at 5s.
    assert statistics.pstdev(sleep_calls) > 0.1
    # All clamped to [min, max].
    assert all(settings.delay_min_s <= d <= settings.delay_max_s
               for d in sleep_calls)


async def test_reply_delay_clamps_to_min_and_max():
    settings = _settings(delay_min_s=2.0, delay_max_s=4.0,
                         delay_mean_s=5.0, delay_sigma=2.0)
    # Random=0.999 -> way above the median -> clamp to max.
    ctrl_hi, _ = _controller(
        allowlist={"AUTHOR"}, settings=settings,
        sleep=_no_sleep(), random_fn=lambda: 0.999,
    )
    assert ctrl_hi._sample_delay(settings) == pytest.approx(4.0, abs=1e-9)
    # Random=0.001 -> far below the median -> clamp to min.
    ctrl_lo, _ = _controller(
        allowlist={"AUTHOR"}, settings=settings,
        sleep=_no_sleep(), random_fn=lambda: 0.001,
    )
    assert ctrl_lo._sample_delay(settings) == pytest.approx(2.0, abs=1e-9)


# ===========================================================================
# Typing indicator
# ===========================================================================

async def test_typing_indicator_emitted_before_send():
    """Default: typing on. The sequence sender.typing then sender.send_dm
    is enforced by per-channel serialisation, so the indicator strictly
    precedes the send for any given event."""
    sleep = _no_sleep()
    sender = RecordingSender()

    events_order: list[str] = []

    class OrderedSender(RecordingSender):
        async def trigger_typing(self, channel_id: str) -> None:
            events_order.append(f"typing:{channel_id}")
            await super().trigger_typing(channel_id)

        async def send_dm(self, channel_id: str, content: str) -> bool:
            events_order.append(f"send:{channel_id}")
            return await super().send_dm(channel_id, content)

    sender = OrderedSender()
    ctrl, _ = _controller(
        sender=sender, allowlist={"AUTHOR"},
        sleep=sleep, random_fn=lambda: 0.5,
    )
    assert await ctrl.handle_inbound(_event()) is True
    assert events_order == ["typing:CH1", "send:CH1"]


async def test_typing_indicator_can_be_disabled():
    settings = _settings(typing_indicator=False)
    sleep = _no_sleep()
    ctrl, sender = _controller(
        allowlist={"AUTHOR"}, settings=settings,
        sleep=sleep, random_fn=lambda: 0.5,
    )
    await ctrl.handle_inbound(_event())
    assert sender.send_calls == [("CH1", "auto-reply text")]
    assert sender.typing_calls == []


# ===========================================================================
# Per-channel serialisation
# ===========================================================================

async def test_two_inbound_on_same_channel_serialise():
    """Two near-simultaneous inbound DMs on the same channel produce
    sends in strictly serial order, NOT interleaved."""
    settings = _settings(rate_limit_max=10, rate_limit_window_s=60.0)
    progression: list[str] = []
    sleep_release = asyncio.Event()

    async def slow_sleep(delay: float) -> None:
        progression.append("sleep-in")
        # Park the first delay until the second event has had a chance
        # to queue up behind the per-channel lock.
        await sleep_release.wait()
        progression.append("sleep-out")

    sender = RecordingSender()

    async def reply(event):
        return f"reply-{event['text']}"

    ctrl = DiscordAutoReplyController(
        sender=sender,
        reply_generator=reply,
        is_allowlisted=lambda aid: True,
        get_settings=lambda: settings,
        local_hour=lambda: 12,
        sleep=slow_sleep,
        random_fn=lambda: 0.5,
    )

    t1 = asyncio.create_task(ctrl.handle_inbound(_event(text="first")))
    # Yield enough cycles for t1 to acquire the per-channel lock + enter
    # sleep.
    for _ in range(5):
        await asyncio.sleep(0)
    t2 = asyncio.create_task(ctrl.handle_inbound(_event(text="second")))
    for _ in range(5):
        await asyncio.sleep(0)
    # t2 is blocked behind the per-channel lock: no second sleep yet.
    assert progression == ["sleep-in"]
    # Release the first event; the second proceeds afterwards.
    sleep_release.set()
    await asyncio.gather(t1, t2)
    # Send order matches arrival order: first then second.
    assert [c[1] for c in sender.send_calls] == ["reply-first", "reply-second"]


# ===========================================================================
# Reply-generator edge cases
# ===========================================================================

async def test_empty_llm_reply_skips_send_but_consumes_event():
    sleep = _no_sleep()
    ctrl, sender = _controller(
        allowlist={"AUTHOR"}, reply="   ", sleep=sleep,
        random_fn=lambda: 0.5,
    )
    consumed = await ctrl.handle_inbound(_event())
    # Consumed -- we don't re-draft an inbound where the LLM said
    # "nothing to say". The typing indicator still fired because we
    # decided before the LLM had a chance to say "nothing".
    assert consumed is True
    assert sender.send_calls == []
    assert sender.typing_calls == ["CH1"]


async def test_reply_generator_exception_swallowed():
    sleep = _no_sleep()

    async def raising_reply(event):
        raise RuntimeError("LLM down")

    sender = RecordingSender()
    ctrl = DiscordAutoReplyController(
        sender=sender,
        reply_generator=raising_reply,
        is_allowlisted=lambda aid: True,
        get_settings=lambda: settings_from_overrides({}),
        local_hour=lambda: 12,
        sleep=sleep,
        random_fn=lambda: 0.5,
    )
    consumed = await ctrl.handle_inbound(_event())
    assert consumed is True  # event consumed -- not double-surfaced
    assert sender.send_calls == []


# ===========================================================================
# Plugin: trigger_typing + send_dm (slice-2 internal sender)
# ===========================================================================

async def test_plugin_trigger_typing_hits_correct_endpoint():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    await plugin.trigger_typing("CH99")
    assert any(
        c["method"] == "POST" and c["url"].endswith("channels/CH99/typing")
        for c in fetch.calls
    )


async def test_plugin_trigger_typing_failure_is_silent():
    fetch = FakeFetch(raises={
        ("POST", "channels/CH99/typing"): RuntimeError("network down"),
    })
    plugin = _make_plugin(fetch=fetch)
    # Must not raise -- typing is a UX nicety, not a correctness gate.
    await plugin.trigger_typing("CH99")


async def test_plugin_send_dm_hits_post_messages():
    fetch = FakeFetch(responses={
        ("POST", "channels/CH1/messages"): {"id": "M1"},
    })
    plugin = _make_plugin(fetch=fetch)
    ok = await plugin.send_dm("CH1", "hello")
    assert ok is True
    msg_calls = [c for c in fetch.calls if c["url"].endswith("CH1/messages")]
    assert len(msg_calls) == 1
    assert msg_calls[0]["method"] == "POST"
    assert msg_calls[0]["json"] == {"content": "hello"}


async def test_plugin_send_dm_returns_false_on_transport_failure():
    fetch = FakeFetch(raises={
        ("POST", "channels/CH1/messages"): RuntimeError("HTTP 500"),
    })
    plugin = _make_plugin(fetch=fetch)
    ok = await plugin.send_dm("CH1", "hello")
    assert ok is False


async def test_plugin_send_dm_skips_no_channel_or_content():
    fetch = FakeFetch()
    plugin = _make_plugin(fetch=fetch)
    assert await plugin.send_dm("", "hello") is False
    assert await plugin.send_dm("CH1", "") is False
    assert fetch.calls == []


async def test_plugin_send_dm_no_token_returns_false_without_crash():
    fetch = FakeFetch()
    plugin_cls = PLUGIN_MOD.DiscordUserPlugin
    plugin = plugin_cls(token_provider=None, fetch_fn=fetch)
    ok = await plugin.send_dm("CH1", "hello")
    assert ok is False
    # No HTTP attempted because token resolution returned an error.
    msg_calls = [c for c in fetch.calls if c["url"].endswith("CH1/messages")]
    assert msg_calls == []
