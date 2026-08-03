"""Unit tests for the #594 mode-aware "Felix is driving" indicator (ADR-0016
amendment 2026-08-02, decision f).

``_emit_driving`` now broadcasts a payload -- {"driving", "mode",
"window_title", "action"} -- instead of a bare bool, so the Visualiser can
render "Felix is acting in <window> (background) -- you can keep working" vs
the foreground cursor-in-use urgency. Fake backend injects scripted
``resolve_element`` / ``pattern_click`` / ``foreground_window`` values -- no
real UIA, no cursor, no keyboard. Async tests throughout (asyncio_mode=auto)
per project convention: never call asyncio.run inside a sync test body.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from plugins.computer_use import ComputerUsePlugin


def _elems(*items) -> list[dict]:
    return [{"name": n, "role": r, "bbox": b} for (n, r, b) in items]


class _DrivingBackend:
    """Fake backend exposing the #592 pattern seam + the #593 focus-theft
    probe. ``foreground_values`` is consumed one-per-call across the
    before/after probes (same convention as test_computer_use_idle_gate.py)."""

    def __init__(
        self,
        read_ui_returns: list[list[dict]] | None = None,
        resolve_returns: list | None = None,
        pattern_click_result: bool | None = None,
        foreground_values: list[str | None] | None = None,
    ) -> None:
        self._read_returns = read_ui_returns or []
        self._resolve_returns = resolve_returns or []
        self._pattern_click_result = pattern_click_result
        self._foreground_values = foreground_values if foreground_values is not None else [None]
        self.read_calls: list[str] = []
        self.click_calls: list[list[int]] = []
        self.resolve_calls: list[tuple] = []
        self.pattern_click_calls: list = []
        self.foreground_calls = 0

    def read_ui(self, window_title: str) -> list[dict]:
        self.read_calls.append(window_title)
        idx = min(len(self.read_calls) - 1, len(self._read_returns) - 1)
        return list(self._read_returns[idx]) if self._read_returns else []

    def click(self, bbox: list[int]) -> None:
        self.click_calls.append(list(bbox))

    def type_text(self, text: str) -> None:
        pass

    def window_bounds(self, window_title: str):
        return None

    def resolve_element(self, window_title, name, role):
        self.resolve_calls.append((window_title, name, role))
        if not self._resolve_returns:
            return None
        idx = min(len(self.resolve_calls) - 1, len(self._resolve_returns) - 1)
        return self._resolve_returns[idx]

    def pattern_click(self, element) -> bool:
        self.pattern_click_calls.append(element)
        return bool(self._pattern_click_result)

    def foreground_window(self):
        idx = min(self.foreground_calls, len(self._foreground_values) - 1)
        self.foreground_calls += 1
        return self._foreground_values[idx]


async def test_background_action_broadcasts_mode_background_window_and_action():
    """A pattern-clickable element actuates in the background -- the very
    first driving broadcast (before any try runs) already guesses
    mode="background", carrying the window title + tool name."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _DrivingBackend(
        read_ui_returns=[els],
        resolve_returns=[("live-two", [10, 20, 50, 60])],
        pattern_click_result=True,
        foreground_values=["Calculator", "Calculator"],  # unchanged -> no theft
    )
    calls: list[dict] = []

    async def _driving(payload: dict) -> None:
        calls.append(payload)

    plugin = ComputerUsePlugin(
        backend=backend, driving_fn=_driving, background_actuation_fn=lambda: True,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    assert not r.is_error, r.content
    data = json.loads(r.content)
    assert data["tries"][-1]["path"] == "uia_pattern"

    assert calls[0] == {
        "driving": True, "mode": "background",
        "window_title": "Calc", "action": "click_element",
    }
    assert calls[-1]["driving"] is False
    # No theft -> mode never had to flip; the pattern click was the only
    # actuation and it stayed background throughout.
    assert all(c["mode"] == "background" for c in calls[:-1])


async def test_foreground_action_broadcasts_mode_foreground():
    """background_actuation off -> the initial guess is already
    "foreground" (no pattern will ever be attempted)."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _DrivingBackend(read_ui_returns=[els])
    calls: list[dict] = []

    async def _driving(payload: dict) -> None:
        calls.append(payload)

    plugin = ComputerUsePlugin(
        backend=backend, driving_fn=_driving, background_actuation_fn=lambda: False,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    assert not r.is_error, r.content
    assert calls[0]["mode"] == "foreground"
    assert calls[0]["window_title"] == "Calc"
    assert calls[0]["action"] == "click_element"
    assert backend.click_calls == [[10, 20, 50, 60]]


async def test_no_usable_pattern_flips_mode_to_foreground_before_the_click():
    """Background actuation is enabled but the target has no usable pattern
    -- the initial "background" guess must flip to "foreground" in real time
    before the pyautogui fallback actually steals the cursor."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _DrivingBackend(
        read_ui_returns=[els],
        resolve_returns=[("live-two", [10, 20, 50, 60])],
        pattern_click_result=False,  # no usable pattern
    )
    calls: list[dict] = []

    async def _driving(payload: dict) -> None:
        calls.append(payload)

    plugin = ComputerUsePlugin(
        backend=backend, driving_fn=_driving, background_actuation_fn=lambda: True,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    assert not r.is_error, r.content
    modes = [c["mode"] for c in calls if c["driving"]]
    assert modes == ["background", "foreground"]
    assert backend.click_calls == [[10, 20, 50, 60]]  # fallback actually fired


async def test_focus_theft_soft_trip_yields_foreground_urgent_payload():
    """A background pattern click that unexpectedly foregrounds the target
    (#593 soft trip) must flip the live indicator to the foreground/urgent
    style before the call ends -- the whole point is real-time visibility
    into a mode change that just happened mid-action."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _DrivingBackend(
        read_ui_returns=[els],
        resolve_returns=[("live-two", [10, 20, 50, 60])],
        pattern_click_result=True,
        foreground_values=["Explorer", "Calculator"],  # changed -> soft trip
    )
    calls: list[dict] = []

    async def _driving(payload: dict) -> None:
        calls.append(payload)

    plugin = ComputerUsePlugin(
        backend=backend, driving_fn=_driving, background_actuation_fn=lambda: True,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    assert r.is_error  # soft trip is a failed call
    data = json.loads(r.content)
    assert data["tries"][-1]["foregrounded"] is True

    # The broadcast sequence: initial background guess, then the real-time
    # flip to foreground/urgent on the soft trip, then driving off.
    assert calls[0]["mode"] == "background"
    driving_true_calls = [c for c in calls if c["driving"]]
    assert driving_true_calls[-1]["mode"] == "foreground"
    assert calls[-1]["driving"] is False
    # Never escalated to the input-stealing foreground fallback.
    assert backend.click_calls == []
