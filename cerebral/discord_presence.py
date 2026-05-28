"""
Discord presence controller -- Issue #178 (slice 3), ADR-0006.

Auto-presence sits at the plugin / Cerebral seam:

  - ``plugins/discord_user.py`` owns the actual ``change_presence``
    wire call (the slice-1 ``DiscordClient`` Protocol). Slice 3 adds
    an internal-only ``apply_presence`` method on the plugin that
    bypasses the LLM ``confirm`` ceremony -- parity with slice 2's
    ``send_dm`` / ``trigger_typing``.
  - The controller below decides WHEN to flip presence, driven by:
      * an LLM-activity tick fed from wherever the LLM pipeline
        dispatches Discord-bound work (``tick_activity``),
      * a periodic check that flips to idle when the tick is older
        than the configured threshold (``tick_check``),
      * the sleep-hours window from slice 2 (read via
        ``cerebral.discord_auto_reply._hour_in_sleep_window``) --
        inside the window, presence is forced to ``invisible``
        regardless of LLM activity.
  - The slice-1 ``discord_set_presence`` MCP tool stays as the
    manual-override path: it wins until the next auto-trigger
    (activity tick or idle threshold expiring or sleep-window
    boundary crossing).

Settings live in ``discord_user_settings`` alongside the slice-2
auto-reply settings; the controller reads them via an injected
``SettingsLookup`` callback so the test suite can drive the state
machine deterministically without touching the database.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

from cerebral.discord_auto_reply import _hour_in_sleep_window

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings -- defaults + per-profile overrides
# ---------------------------------------------------------------------------

SETTING_AUTO_IDLE_THRESHOLD_S = "auto_idle_threshold_s"
SETTING_AUTO_PRESENCE_ENABLED = "auto_presence_enabled"
SETTING_AUTO_PRESENCE_CHECK_INTERVAL_S = "auto_presence_check_interval_s"

# 5 minutes is the same threshold a real Discord client uses to flip
# the user idle on the desktop client -- matching it makes Felix's
# behavioural fingerprint less recognisably automated.
_DEFAULTS: dict[str, str] = {
    SETTING_AUTO_IDLE_THRESHOLD_S: "300",
    SETTING_AUTO_PRESENCE_ENABLED: "1",
    SETTING_AUTO_PRESENCE_CHECK_INTERVAL_S: "30",
}

PRESENCE_ALLOWED_SETTING_KEYS: frozenset[str] = frozenset(_DEFAULTS)


@dataclass(frozen=True)
class PresenceSettings:
    auto_idle_threshold_s: float
    auto_presence_enabled: bool
    auto_presence_check_interval_s: float


def _parse_float(raw: str, fallback: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _parse_bool(raw: str, fallback: bool) -> bool:
    if raw is None:
        return fallback
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return fallback


def presence_settings_from_overrides(
    overrides: dict[str, str],
) -> PresenceSettings:
    def g(k: str) -> str:
        return overrides.get(k, _DEFAULTS[k])

    return PresenceSettings(
        auto_idle_threshold_s=_parse_float(
            g(SETTING_AUTO_IDLE_THRESHOLD_S), 300.0,
        ),
        auto_presence_enabled=_parse_bool(
            g(SETTING_AUTO_PRESENCE_ENABLED), True,
        ),
        auto_presence_check_interval_s=_parse_float(
            g(SETTING_AUTO_PRESENCE_CHECK_INTERVAL_S), 30.0,
        ),
    )


# ---------------------------------------------------------------------------
# Seams the controller leans on
# ---------------------------------------------------------------------------

class PresenceSender(Protocol):
    """Minimal slice of the slice-1 plugin's presence surface."""

    async def apply_presence(self, status: str) -> bool: ...


PresenceSettingsLookup = Callable[[], PresenceSettings]
SleepHoursLookup = Callable[[], tuple[Optional[int], Optional[int]]]
LocalHourFn = Callable[[], int]
ClockFn = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------

class DiscordPresenceController:
    """Drive Felix's Discord presence so it tracks LLM activity.

    State the controller carries:
      - ``_last_activity``: monotonic timestamp of the most recent
        LLM-driven Discord action (set by ``tick_activity``). ``None``
        before the first activity tick.
      - ``_current_presence``: the presence we last actually applied to
        the wire; lets ``_apply`` skip redundant transitions (no-op
        flips would be a detection signal of their own).
      - ``_manual_override``: True after a manual ``discord_set_presence``
        call until the next auto-trigger clears it. Auto-triggers are
        activity ticks, idle-threshold expirations, and sleep-window
        boundary crossings.

    The controller never sleeps internally -- the caller owns the
    background loop that pumps ``tick_check`` on the configured
    interval (see ``main.py``'s _auto_presence_loop).
    """

    def __init__(
        self,
        *,
        sender: PresenceSender,
        get_presence_settings: PresenceSettingsLookup,
        sleep_hours: SleepHoursLookup,
        local_hour: LocalHourFn,
        clock: ClockFn = None,
    ) -> None:
        self._sender = sender
        self._get_presence_settings = get_presence_settings
        self._sleep_hours = sleep_hours
        self._local_hour = local_hour
        self._clock = clock or time.monotonic
        self._last_activity: Optional[float] = None
        self._current_presence: Optional[str] = None
        self._manual_override: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_presence(self) -> Optional[str]:
        return self._current_presence

    @property
    def manual_override_active(self) -> bool:
        return self._manual_override

    async def tick_activity(self) -> None:
        """Notify the controller that the LLM dispatched Discord-bound
        work. This is an auto-trigger -- it clears any manual override.

        Inside the sleep-hours window the target is still ``invisible``
        (the sleep window wins over auto-presence per the slice-3
        acceptance criterion).
        """
        settings = self._get_presence_settings()
        if not settings.auto_presence_enabled:
            return
        self._last_activity = self._clock()
        target = self._sleep_status() or "online"
        await self._auto_apply(target)

    async def tick_check(self) -> None:
        """Periodic auto-presence check -- called by the background
        loop. Idempotent.

        Behaviour:

          - Inside the sleep window, force ``invisible`` (this is an
            auto-trigger and clears manual override).
          - Outside the sleep window: if no activity has ever been
            recorded OR the last activity is older than the configured
            idle threshold, target is ``idle`` (auto-trigger). Else
            no-op (manual override survives this branch).
        """
        settings = self._get_presence_settings()
        if not settings.auto_presence_enabled:
            return

        sleep_target = self._sleep_status()
        if sleep_target is not None:
            await self._auto_apply(sleep_target)
            return

        threshold = max(0.0, settings.auto_idle_threshold_s)
        if self._last_activity is None:
            # No activity recorded yet -- treat as already-idle.
            await self._auto_apply("idle")
            return
        elapsed = self._clock() - self._last_activity
        if elapsed >= threshold:
            await self._auto_apply("idle")
        # Else: still inside the activity window. Don't touch presence
        # (manual override, if any, survives because no auto-trigger
        # fired).

    async def apply_manual_override(self, status: str) -> None:
        """A manual ``discord_set_presence`` arrived -- apply it and
        set the override flag. The next auto-trigger will clear the
        flag.
        """
        self._manual_override = True
        ok = await self._sender.apply_presence(status)
        if ok:
            self._current_presence = status

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sleep_status(self) -> Optional[str]:
        """Return ``"invisible"`` iff we're inside the sleep-hours
        window, else None. Slice 2's window definition is reused via
        ``cerebral.discord_auto_reply._hour_in_sleep_window``.
        """
        start, end = self._sleep_hours()
        if _hour_in_sleep_window(self._local_hour(), start, end):
            return "invisible"
        return None

    async def _auto_apply(self, status: str) -> None:
        """Apply an auto-driven presence change. Clears manual override
        (auto-trigger semantics) and skips the wire call when the
        target matches what we last applied.
        """
        self._manual_override = False
        if status == self._current_presence:
            return
        ok = await self._sender.apply_presence(status)
        if ok:
            self._current_presence = status


# ---------------------------------------------------------------------------
# Background loop helper -- main.py uses this; tests drive tick_check directly
# ---------------------------------------------------------------------------

async def run_presence_loop(
    controller: DiscordPresenceController,
    get_settings: PresenceSettingsLookup,
    *,
    stop_event: asyncio.Event,
) -> None:
    """Drive ``controller.tick_check`` on the configured interval until
    ``stop_event`` is set. Re-reads the interval from settings on every
    iteration so the user can tune it live without a restart.

    Failure swallowed and logged -- a transient hiccup must not crash
    the Cerebral event loop. The loop sleeps before the first tick so a
    profile switch immediately after start doesn't see a redundant
    flip.
    """
    while not stop_event.is_set():
        try:
            interval = max(
                0.01,
                float(get_settings().auto_presence_check_interval_s),
            )
        except Exception:
            interval = 30.0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await controller.tick_check()
        except Exception:
            logger.exception(
                "[discord_presence] tick_check raised -- continuing",
            )
