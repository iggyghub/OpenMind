"""
Discord presence controller -- Issue #178 (slice 3), ADR-0006.

Lives Cerebral-side (not in the plugin) so it can see all LLM activity,
not just Discord-bound tool calls.  The plugin owns the actual
``change_presence`` call; this controller owns the *policy*:

  - **Auto-online**: when ``on_activity()`` is called (the LLM pipeline
    dispatches a Discord-bound action), flip presence to ``online``.
  - **Auto-idle**: after ``idle_after_s`` of no ``on_activity()`` calls,
    a background checker flips to ``idle``.
  - **Sleep-hours wins**: if the sleep-hours window (configured in slice
    2's ``AutoReplySettings``) is active, all auto-transitions force
    ``invisible`` instead of ``online`` or ``idle``.
  - **Manual override**: ``discord_set_presence`` (the MCP tool) calls
    the plugin's ``on_manual_override`` seam which calls
    ``notify_manual_override(status)`` here.  Auto-presence is paused
    until the next ``on_activity()`` call clears the flag.

Wire-up (main.py):

  1. Instantiate ``DiscordPresenceController`` with a ``set_presence_fn``
     that calls ``plugin._client.change_presence(status=...)`` and
     updates ``plugin._desired_presence``.
  2. Pass ``controller.notify_manual_override`` as the plugin's
     ``on_manual_override`` kwarg so manual ``discord_set_presence``
     calls flow through.
  3. Call ``controller.on_activity()`` from the LLM dispatch path
     whenever a Discord-bound tool is about to execute.
  4. Call ``await controller.start()`` at subscriber start and
     ``await controller.stop()`` at subscriber stop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_IDLE_AFTER_S: float = 600.0   # 10 minutes -- plausible human idle
_DEFAULT_CHECK_INTERVAL_S: float = 30.0


class DiscordPresenceController:
    """State machine that drives auto-idle / auto-online presence transitions.

    All side-effect surfaces (set_presence_fn, clock, sleep,
    is_in_sleep_window) are injectable so the test suite exercises the
    full state machine deterministically without touching the network.
    """

    def __init__(
        self,
        set_presence_fn: Callable[[str], Awaitable[None]],
        *,
        idle_after_s: float = DEFAULT_IDLE_AFTER_S,
        is_in_sleep_window: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        check_interval_s: float = _DEFAULT_CHECK_INTERVAL_S,
    ) -> None:
        self._set_presence = set_presence_fn
        self._idle_after_s = idle_after_s
        self._is_in_sleep_window = is_in_sleep_window
        self._clock = clock
        self._sleep = sleep
        self._check_interval_s = check_interval_s

        self._last_activity: Optional[float] = None
        self._manual_override: bool = False
        self._current_presence: Optional[str] = None
        self._loop_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public interface -- called by Cerebral / plugin seam
    # ------------------------------------------------------------------

    async def on_activity(self) -> None:
        """Notify the controller that the LLM dispatched a Discord-bound
        action.  Clears any manual override and applies auto-presence."""
        self._last_activity = self._clock()
        self._manual_override = False
        await self._apply_auto_presence()

    def notify_manual_override(self, status: str) -> None:
        """Called synchronously by the plugin's ``on_manual_override``
        seam when ``discord_set_presence`` is invoked manually.  Pauses
        auto-presence until the next ``on_activity()`` call."""
        self._manual_override = True
        self._current_presence = status

    async def check(self) -> None:
        """Single idle-check pass.

        Called by the background loop; also callable directly in tests
        to drive the state machine without the loop overhead.
        """
        if self._manual_override:
            return
        if self._last_activity is None:
            return
        if self._is_in_sleep_window():
            if self._current_presence != "invisible":
                self._current_presence = "invisible"
                try:
                    await self._set_presence("invisible")
                except Exception as exc:
                    logger.debug(
                        "[discord_presence] idle-check set invisible failed: %s",
                        exc,
                    )
        else:
            elapsed = self._clock() - self._last_activity
            if elapsed >= self._idle_after_s:
                if self._current_presence != "idle":
                    self._current_presence = "idle"
                    try:
                        await self._set_presence("idle")
                    except Exception as exc:
                        logger.debug(
                            "[discord_presence] idle-check set idle failed: %s",
                            exc,
                        )

    async def start(self) -> None:
        """Start the background idle-checker loop."""
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._idle_check_loop())
        logger.debug("[discord_presence] idle-checker loop started")

    async def stop(self) -> None:
        """Stop the background idle-checker loop. Idempotent."""
        task = self._loop_task
        self._loop_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logger.debug("[discord_presence] idle-checker loop stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _apply_auto_presence(self) -> None:
        """Decide and apply presence based on sleep-window state."""
        if self._is_in_sleep_window():
            target = "invisible"
        else:
            target = "online"
        if self._current_presence != target:
            self._current_presence = target
            try:
                await self._set_presence(target)
            except Exception as exc:
                logger.debug(
                    "[discord_presence] _apply_auto_presence failed: %s", exc,
                )

    async def _idle_check_loop(self) -> None:
        while True:
            try:
                await self._sleep(self._check_interval_s)
            except asyncio.CancelledError:
                return
            try:
                await self.check()
            except Exception as exc:  # pragma: no cover -- defensive
                logger.debug(
                    "[discord_presence] idle-check loop iteration failed: %s",
                    exc,
                )
