"""S16 #610: three-tier mode ladder + never-silent failure/notify.

Tests for:
  1. select_actuation_tier() -- planner-facing tier selection (pure logic).
  2. Window-bounded check dropped in session 2 (_in_isolated_session).
  3. Idle gate dropped in session 2.
  4. Consequence/irreversible modal unchanged in session 2.
  5. Worker failure -> FailureNotifyFn fires + fallback to local backend.
  6. screen_capture consent silenced in session 2 (_computer_use_effective_caps).
  7. Fail-closed on non-Windows (inherited from existing posture, regression guard).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import plugins.computer_use as cu_mod
from plugins.computer_use import (
    ComputerUsePlugin,
    select_actuation_tier,
)
import cerebral.main as main_mod


# ---------------------------------------------------------------------------
# Fake backend -- same shape as test_plugin_computer_use.py
# ---------------------------------------------------------------------------

class _FakeBackend:
    def __init__(
        self,
        read_ui_returns: list[list[dict]] | None = None,
        window_bounds_returns: list[int] | None = None,
        last_input_ms_returns: int | None = None,
    ) -> None:
        self._read_returns = read_ui_returns or []
        self._window_bounds = window_bounds_returns
        self._last_input_ms = last_input_ms_returns
        self.click_calls: list = []
        self.type_calls: list[str] = []
        self.read_calls: list[str] = []

    def read_ui(self, window_title: str) -> list[dict]:
        self.read_calls.append(window_title)
        idx = min(len(self.read_calls) - 1, len(self._read_returns) - 1)
        return list(self._read_returns[idx]) if self._read_returns else []

    def click(self, bbox: list[int]) -> None:
        self.click_calls.append(list(bbox))

    def type_text(self, text: str) -> None:
        self.type_calls.append(text)

    def window_bounds(self, window_title: str) -> list[int] | None:
        return self._window_bounds

    def last_input_ms(self) -> int | None:
        return self._last_input_ms


def _elem(name: str, role: str = "Button", bbox: list[int] | None = None) -> dict:
    return {"name": name, "role": role, "bbox": bbox or [0, 0, 10, 10]}


# ---------------------------------------------------------------------------
# 1. select_actuation_tier() -- pure planner-facing function
# ---------------------------------------------------------------------------

def test_select_tier_default_background():
    assert select_actuation_tier() == "background"


def test_select_tier_needs_foreground_returns_isolated():
    assert select_actuation_tier(needs_foreground=True) == "isolated_session"


def test_select_tier_live_desktop_only_returns_take_turns():
    assert select_actuation_tier(live_desktop_only=True) == "take_turns"


def test_select_tier_live_desktop_only_wins_over_needs_foreground():
    # live_desktop_only takes precedence -- the target is session-1-only.
    assert select_actuation_tier(needs_foreground=True, live_desktop_only=True) == "take_turns"


# ---------------------------------------------------------------------------
# 2. Window-bounded check dropped in session 2
# ---------------------------------------------------------------------------

async def test_click_skips_window_bounds_in_session_2():
    """When session dispatch is wired (session 2), a bbox outside the local
    window bounds is NOT refused -- full-desktop actions are allowed."""
    out_of_bounds_elem = _elem("OK", bbox=[999, 999, 1009, 1009])
    backend = _FakeBackend(
        window_bounds_returns=[0, 0, 100, 100],  # tight bounds
    )

    dispatch_calls: list = []

    async def fake_dispatch(action: str, params: dict) -> dict:
        dispatch_calls.append((action, params))
        if action == "read_ui":
            # Worker returns the element with coords outside local bounds.
            return {"elements": [out_of_bounds_elem]}
        return {}

    plugin = ComputerUsePlugin(
        backend=backend,
        session_dispatch_fn=fake_dispatch,
        full_autonomy_fn=lambda: True,  # bypass idle gate for session-1 fallback
    )
    result = await plugin.call_tool("click_element", {
        "window_title": "TestWin",
        "name": "OK",
    })
    # The dispatch was called (not refused by bounds) -- session 2 succeeded.
    assert any(action == "click" for action, _ in dispatch_calls), (
        "click dispatch should fire; bounds check must be skipped in session 2"
    )
    assert not result.is_error


async def test_click_enforces_window_bounds_in_session_1():
    """Outside session 2, the window-bounded check still refuses out-of-bounds
    clicks (regression guard -- no change to session-1 behavior)."""
    elems = [[_elem("OK", bbox=[999, 999, 1009, 1009])]]
    backend = _FakeBackend(
        read_ui_returns=elems,
        window_bounds_returns=[0, 0, 100, 100],
    )
    plugin = ComputerUsePlugin(backend=backend)
    result = await plugin.call_tool("click_element", {
        "window_title": "TestWin",
        "name": "OK",
    })
    # No dispatch seam; click refused by bounds check.
    assert result.is_error
    import json
    trace = json.loads(result.content)
    assert any(
        "outside window bounds" in t.get("actual", "")
        for t in trace.get("tries", [])
    )


# ---------------------------------------------------------------------------
# 3. Idle gate dropped in session 2
# ---------------------------------------------------------------------------

async def test_foreground_dispatch_skips_idle_gate_in_session_2():
    """In session 2 (dispatch seam wired), a 'user present' idle time must NOT
    block the dispatch -- the idle gate only applies to session-1 foreground."""
    go_elem = _elem("Go")
    backend = _FakeBackend(
        last_input_ms_returns=0,  # user just touched input -- would block session 1
    )

    dispatch_calls: list = []

    async def fake_dispatch(action: str, params: dict) -> dict:
        dispatch_calls.append(action)
        if action == "read_ui":
            return {"elements": [go_elem]}
        return {}

    # Full autonomy OFF so the idle gate would normally fire on session 1.
    plugin = ComputerUsePlugin(
        backend=backend,
        session_dispatch_fn=fake_dispatch,
        full_autonomy_fn=lambda: False,
        user_idle_ms_fn=lambda: 5000,  # threshold 5 s; idle_ms=0 << threshold
    )
    result = await plugin.call_tool("click_element", {
        "window_title": "TestWin",
        "name": "Go",
    })
    assert not result.is_error, "session-2 click should succeed despite user-present idle"
    assert "click" in dispatch_calls


async def test_foreground_gate_still_blocks_session_1():
    """Session-1 behavior is unchanged: user-present idle blocks (regression)."""
    elems = [[_elem("Go")]]
    backend = _FakeBackend(
        read_ui_returns=elems,
        last_input_ms_returns=0,  # user present
    )
    plugin = ComputerUsePlugin(
        backend=backend,
        full_autonomy_fn=lambda: False,
        user_idle_ms_fn=lambda: 5000,
    )
    result = await plugin.call_tool("click_element", {
        "window_title": "TestWin",
        "name": "Go",
        "retries": 1,
    })
    # Idle gate blocks; no local click was made.
    assert backend.click_calls == []
    import json
    trace = json.loads(result.content)
    assert any(
        "waiting for idle" in t.get("actual", "")
        for t in trace.get("tries", [])
    )


# ---------------------------------------------------------------------------
# 4. Consequence/irreversible modal unchanged in session 2
# ---------------------------------------------------------------------------

def test_committing_click_is_flagged_regardless_of_session():
    """is_committing_action does not read session mode -- it is always path-
    independent. Regression guard for the key S16 safety constraint."""
    assert cu_mod.is_committing_action("click_element", {"name": "Send"}) is True
    assert cu_mod.is_committing_action("click_element", {"name": "Two"}) is False


# ---------------------------------------------------------------------------
# 5. Worker failure -> FailureNotifyFn fires + fallback to local backend
# ---------------------------------------------------------------------------

async def test_prim_read_ui_failure_notifies_and_falls_back():
    """When the worker dispatch raises, the failure seam fires and the plugin
    falls back to the local backend -- never a silent failure."""
    elems = [_elem("X")]
    backend = _FakeBackend(read_ui_returns=[elems])

    async def broken_dispatch(action: str, params: dict) -> dict:
        raise RuntimeError("worker disconnected")

    notified: list[tuple] = []

    async def fake_notify(mode: str, reason: str, fallback: str) -> None:
        notified.append((mode, reason, fallback))

    plugin = ComputerUsePlugin(
        backend=backend,
        session_dispatch_fn=broken_dispatch,
        failure_notify_fn=fake_notify,
    )
    result = await plugin.call_tool("read_ui", {"window_title": "W"})
    # Notification fired.
    assert len(notified) == 1
    mode, reason, fallback = notified[0]
    assert mode == "isolated_session"
    assert "worker disconnected" in reason
    assert fallback == "take_turns"
    # Tool still succeeded via local backend.
    assert not result.is_error


async def test_prim_click_failure_notifies_and_falls_back():
    """Click dispatch failure triggers notify + local backend fallback."""
    elems = [[_elem("Btn")]]
    backend = _FakeBackend(read_ui_returns=elems)

    async def broken_dispatch(action: str, params: dict) -> dict:
        raise RuntimeError("session 2 gone")

    notified: list[tuple] = []

    async def fake_notify(mode: str, reason: str, fallback: str) -> None:
        notified.append((mode, reason, fallback))

    plugin = ComputerUsePlugin(
        backend=backend,
        session_dispatch_fn=broken_dispatch,
        failure_notify_fn=fake_notify,
        full_autonomy_fn=lambda: True,  # bypass idle gate for local fallback
    )
    result = await plugin.call_tool("click_element", {
        "window_title": "W",
        "name": "Btn",
    })
    assert len(notified) >= 1
    assert notified[0][0] == "isolated_session"
    assert notified[0][2] == "take_turns"
    # Local backend took the click.
    assert backend.click_calls, "local backend should have clicked after worker failure"
    assert not result.is_error


async def test_failure_notify_fn_unwired_is_silent():
    """Without a failure_notify_fn the plugin still falls back -- just no
    notification (pre-S16 behaviour; regression guard)."""
    backend = _FakeBackend(read_ui_returns=[[_elem("X")]])

    async def broken_dispatch(action: str, params: dict) -> dict:
        raise RuntimeError("gone")

    plugin = ComputerUsePlugin(
        backend=backend,
        session_dispatch_fn=broken_dispatch,
    )
    # Must not crash.
    result = await plugin.call_tool("read_ui", {"window_title": "W"})
    assert not result.is_error


# ---------------------------------------------------------------------------
# 6. screen_capture consent silenced via _computer_use_effective_caps
# ---------------------------------------------------------------------------

def test_screen_capture_removed_from_caps_in_isolated_session(monkeypatch):
    """_computer_use_effective_caps drops screen_capture when
    _isolated_session_mode is True and plugin is computer_use."""
    monkeypatch.setattr(main_mod, "_isolated_session_mode", True)
    caps = frozenset({"screen_capture", "device_control"})
    effective = main_mod._computer_use_effective_caps("computer_use", caps)
    assert "screen_capture" not in effective
    assert "device_control" in effective


def test_screen_capture_kept_in_session_1(monkeypatch):
    """When NOT in isolated session, caps are unchanged (session-1 behavior)."""
    monkeypatch.setattr(main_mod, "_isolated_session_mode", False)
    caps = frozenset({"screen_capture", "device_control"})
    effective = main_mod._computer_use_effective_caps("computer_use", caps)
    assert effective == caps


def test_caps_unchanged_for_other_plugins(monkeypatch):
    """Even in isolated-session mode, only computer_use gets the screen_capture
    exemption -- other plugins are unaffected."""
    monkeypatch.setattr(main_mod, "_isolated_session_mode", True)
    caps = frozenset({"screen_capture", "device_control"})
    effective = main_mod._computer_use_effective_caps("other_plugin", caps)
    assert effective == caps


def test_caps_none_returned_unchanged(monkeypatch):
    """None caps pass through without error (no plugin)."""
    monkeypatch.setattr(main_mod, "_isolated_session_mode", True)
    assert main_mod._computer_use_effective_caps("computer_use", None) is None


# ---------------------------------------------------------------------------
# 7. Fail-closed on non-Windows (regression guard)
# ---------------------------------------------------------------------------

def test_fail_closed_on_non_windows(monkeypatch):
    """On non-Windows platforms the plugin returns an error, no crash."""
    monkeypatch.setattr(cu_mod.sys, "platform", "linux")
    plugin = ComputerUsePlugin(backend=None)

    async def _run():
        return await plugin.call_tool("read_ui", {"window_title": "X"})

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result.is_error
    assert "not available" in result.content
