"""Unit tests for the #593 idle gate + focus-theft soft-trip (ADR-0016
amendment 2026-08-02, decisions d/g).

Fake backend injects scripted ``last_input_ms()`` / ``foreground_window()``
values -- no real GetLastInputInfo/GetForegroundWindow, no cursor, no
keyboard. Async tests throughout (asyncio_mode=auto) per project convention:
never call asyncio.run inside a sync test body.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from plugins.computer_use import ComputerUsePlugin, DEFAULT_USER_IDLE_MS


def _elems(*items) -> list[dict]:
    return [{"name": n, "role": r, "bbox": b} for (n, r, b) in items]


class _IdleBackend:
    """Fake backend exposing the #593 seam methods on top of the #592
    pattern seam. ``idle_values`` is consumed one-per-call (last value
    repeats) so a test can script "present" then "idle" across retries.
    ``foreground_values`` likewise scripts ``foreground_window()`` results
    consumed one-per-call across the before/after probes."""

    def __init__(
        self,
        read_ui_returns: list[list[dict]] | None = None,
        idle_values: list[int] | None = None,
        foreground_values: list[str | None] | None = None,
        resolve_returns: list | None = None,
        pattern_click_result: bool | Exception | None = None,
    ) -> None:
        self._read_returns = read_ui_returns or []
        self._idle_values = idle_values if idle_values is not None else [DEFAULT_USER_IDLE_MS]
        self._foreground_values = foreground_values if foreground_values is not None else [None]
        self._resolve_returns = resolve_returns or []
        self._pattern_click_result = pattern_click_result
        self.read_calls: list[str] = []
        self.click_calls: list[list[int]] = []
        self.type_calls: list[str] = []
        self.idle_calls = 0
        self.foreground_calls = 0
        self.resolve_calls: list[tuple] = []
        self.pattern_click_calls: list = []

    def read_ui(self, window_title: str) -> list[dict]:
        self.read_calls.append(window_title)
        idx = min(len(self.read_calls) - 1, len(self._read_returns) - 1)
        return list(self._read_returns[idx]) if self._read_returns else []

    def click(self, bbox: list[int]) -> None:
        self.click_calls.append(list(bbox))

    def type_text(self, text: str) -> None:
        self.type_calls.append(text)

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
        if isinstance(self._pattern_click_result, Exception):
            raise self._pattern_click_result
        return bool(self._pattern_click_result)

    def last_input_ms(self) -> int:
        idx = min(self.idle_calls, len(self._idle_values) - 1)
        self.idle_calls += 1
        return self._idle_values[idx]

    def foreground_window(self):
        idx = min(self.foreground_calls, len(self._foreground_values) - 1)
        self.foreground_calls += 1
        return self._foreground_values[idx]


# ---- (a) foreground fallback skipped when the user is present ------------

async def test_foreground_fallback_skipped_when_user_present():
    """last_input_ms stays under user_idle_ms on every try -- the plugin
    never touches backend.click(); retries exhaust and it hands off."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _IdleBackend(read_ui_returns=[els], idle_values=[500])  # "just touched"

    handoff_calls = []

    async def fake_handoff(window_title, reason):
        handoff_calls.append((window_title, reason))
        return False

    plugin = ComputerUsePlugin(
        backend=backend,
        background_actuation_fn=lambda: False,  # force straight to foreground path
        user_idle_ms_fn=lambda: 3000,
        full_autonomy_fn=lambda: False,
        attended_handoff_fn=fake_handoff,
    )
    r = await plugin.call_tool(
        "click_element", {"window_title": "Calc", "name": "Two", "retries": 3},
    )
    data = json.loads(r.content)
    assert data["ok"] is False
    assert backend.click_calls == []  # never grabbed the mouse
    assert any("user present" in t.get("actual", "") for t in data["tries"])
    assert handoff_calls  # exhaustion still escalates via the existing S6 seam


# ---- (b) foreground fallback proceeds once idle exceeds the threshold ----

async def test_foreground_fallback_proceeds_when_idle():
    """last_input_ms well over the threshold -- the click fires as before."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _IdleBackend(read_ui_returns=[els], idle_values=[10_000])
    plugin = ComputerUsePlugin(
        backend=backend,
        background_actuation_fn=lambda: False,
        user_idle_ms_fn=lambda: 3000,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    data = json.loads(r.content)
    assert data["ok"] is True
    assert backend.click_calls == [[10, 20, 50, 60]]
    assert data["tries"][-1]["foregrounded"] is False  # no foreground_window support -> False


async def test_full_autonomy_bypasses_idle_gate():
    """Full-autonomy proceeds even while the user is present (mirrors sec 4's
    irreversible-floor bypass)."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _IdleBackend(read_ui_returns=[els], idle_values=[0])
    plugin = ComputerUsePlugin(
        backend=backend,
        background_actuation_fn=lambda: False,
        user_idle_ms_fn=lambda: 3000,
        full_autonomy_fn=lambda: True,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    data = json.loads(r.content)
    assert data["ok"] is True
    assert backend.click_calls == [[10, 20, 50, 60]]


async def test_backend_without_idle_probe_proceeds_unconditionally():
    """A backend that doesn't expose last_input_ms can't block -- matches
    pre-#593 behaviour (the getattr seam is optional)."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))

    class _NoProbeBackend:
        def read_ui(self, window_title):
            return list(els)

        def click(self, bbox):
            pass

        def type_text(self, text):
            pass

    plugin = ComputerUsePlugin(backend=_NoProbeBackend(), user_idle_ms_fn=lambda: 3000)
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    data = json.loads(r.content)
    assert data["ok"] is True


# ---- (c) foregrounded stamped from before/after foreground_window --------

async def test_foregrounded_stamped_true_when_window_becomes_foreground():
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _IdleBackend(
        read_ui_returns=[els],
        idle_values=[10_000],
        foreground_values=["Explorer", "Calculator - Calc"],  # before -> after
    )
    plugin = ComputerUsePlugin(
        backend=backend, background_actuation_fn=lambda: False, user_idle_ms_fn=lambda: 3000,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    data = json.loads(r.content)
    assert data["ok"] is True
    assert data["tries"][-1]["foregrounded"] is True


async def test_foregrounded_stamped_false_when_already_foreground():
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _IdleBackend(
        read_ui_returns=[els],
        idle_values=[10_000],
        foreground_values=["Calculator - Calc", "Calculator - Calc"],  # unchanged
    )
    plugin = ComputerUsePlugin(
        backend=backend, background_actuation_fn=lambda: False, user_idle_ms_fn=lambda: 3000,
    )
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    data = json.loads(r.content)
    assert data["ok"] is True
    assert data["tries"][-1]["foregrounded"] is False


# ---- (d) unexpected foreground on a pattern action is a soft trip --------

async def test_pattern_action_soft_trip_stops_without_foreground_fallback():
    """A background pattern click that unexpectedly foregrounds the target
    stops the loop -- the trace records the trip and backend.click() (the
    input-stealing fallback) is NEVER called."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _IdleBackend(
        read_ui_returns=[els],
        resolve_returns=[("live-two", [10, 20, 50, 60])],
        pattern_click_result=True,
        foreground_values=["Explorer", "Calculator - Calc"],  # changed -> soft trip
    )
    plugin = ComputerUsePlugin(backend=backend, background_actuation_fn=lambda: True)
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    assert r.is_error  # soft trip is a failed call, not a silent success
    data = json.loads(r.content)
    assert data["ok"] is False
    last = data["tries"][-1]
    assert last["path"] == "uia_pattern"
    assert last["foregrounded"] is True
    assert "soft trip" in last["actual"]
    assert backend.click_calls == []  # never escalated to the foreground fallback


async def test_pattern_action_no_foreground_change_still_succeeds():
    """Sanity check: the normal (no theft) background-click path is
    untouched by the #593 probe -- same success shape as pre-#593."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    backend = _IdleBackend(
        read_ui_returns=[els],
        resolve_returns=[("live-two", [10, 20, 50, 60])],
        pattern_click_result=True,
        foreground_values=["Calculator - Calc", "Calculator - Calc"],
    )
    plugin = ComputerUsePlugin(backend=backend, background_actuation_fn=lambda: True)
    r = await plugin.call_tool("click_element", {"window_title": "Calc", "name": "Two"})
    assert not r.is_error, r.content
    data = json.loads(r.content)
    assert data["ok"] is True
    assert data["tries"][-1]["path"] == "uia_pattern"
    assert data["tries"][-1]["foregrounded"] is False
    assert backend.click_calls == []
