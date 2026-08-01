"""Unit tests for the computer_use plugin (ADR-0016 S1 #574 / S2 #576).

All tests use a fake ComputerUseBackend -- no real UIA, mouse, keyboard, or
screen. Fail-closed on non-Windows is covered by forcing the default backend
to None on sys.platform != "win32".
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import plugins.computer_use as cu_mod
from plugins.computer_use import (
    DEFAULT_RETRY_LIMIT,
    MAX_RETRY_LIMIT,
    ComputerUsePlugin,
    CornerAbort,
    _bbox_within,
    _find_element,
    _make_default_backend,
)


# ---------------------------------------------------------------------------
# Fake backend -- feeds scripted read_ui results, records actuation calls.
# ---------------------------------------------------------------------------

class _FakeBackend:
    def __init__(
        self,
        read_ui_returns: list[list[dict]] | None = None,
        raise_on_read: Exception | None = None,
        raise_on_click: Exception | None = None,
    ) -> None:
        self._read_returns = read_ui_returns or []
        self._raise_on_read = raise_on_read
        self._raise_on_click = raise_on_click
        self.click_calls: list[list[int]] = []
        self.type_calls: list[str] = []
        self.read_calls: list[str] = []
        self.capture_calls: list[str] = []

    def read_ui(self, window_title: str) -> list[dict]:
        self.read_calls.append(window_title)
        if self._raise_on_read is not None:
            raise self._raise_on_read
        idx = min(len(self.read_calls) - 1, len(self._read_returns) - 1)
        return list(self._read_returns[idx]) if self._read_returns else []

    def click(self, bbox: list[int]) -> None:
        if self._raise_on_click is not None:
            raise self._raise_on_click
        self.click_calls.append(list(bbox))

    def type_text(self, text: str) -> None:
        self.type_calls.append(text)

    def capture_frame(self, window_title: str) -> bytes | None:
        self.capture_calls.append(window_title)
        return None


def _elems(*items) -> list[dict]:
    """Compact helper: _elems(('Two', 'Button', [0,0,10,10]), ...) -> element dicts."""
    return [{"name": n, "role": r, "bbox": b} for (n, r, b) in items]


# ---------------------------------------------------------------------------
# Plugin surface
# ---------------------------------------------------------------------------

def test_plugin_declares_expected_capabilities():
    assert cu_mod.REQUIRED_CAPABILITIES == frozenset({"screen_capture", "device_control"})


def test_plugin_registers_three_tools():
    plugin = ComputerUsePlugin(backend=_FakeBackend())
    names = {t.name for t in plugin.list_tools()}
    assert names == {"read_ui", "click_element", "type_into"}


def test_source_passes_inspectability_scan():
    """The plugin file must survive the ADR-0005 static-pattern scan."""
    from cerebral.security import scan_source
    src = Path(cu_mod.__file__).read_text(encoding="utf-8")
    assert scan_source(src) is None


# ---------------------------------------------------------------------------
# Fail-closed on non-Windows -- construction + tool call
# ---------------------------------------------------------------------------

def test_default_backend_none_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _make_default_backend() is None


async def test_calls_fail_closed_when_no_backend():
    """Explicit backend=None -> every tool returns is_error with a clear reason."""
    plugin = ComputerUsePlugin(backend=None)
    for tool in ("read_ui", "click_element", "type_into"):
        args = {"window_title": "X", "name": "Y", "text": "z"}
        r = await plugin.call_tool(tool, args)
        assert r.is_error, tool
        assert "not available" in r.content, tool


def test_construction_forces_none_on_non_windows(monkeypatch):
    """A caller who omits backend on non-Windows still gets fail-closed."""
    monkeypatch.setattr(sys, "platform", "linux")
    plugin = ComputerUsePlugin()  # no backend passed
    assert plugin._backend is None


# ---------------------------------------------------------------------------
# read_ui -- returns elements and records a single successful try
# ---------------------------------------------------------------------------

async def test_read_ui_returns_element_list_and_trace():
    els = _elems(("Two", "Button", [10, 20, 50, 60]), ("Result", "Text", [0, 100, 300, 130]))
    plugin = ComputerUsePlugin(backend=_FakeBackend([els]))
    r = await plugin.call_tool("read_ui", {"window_title": "Calculator"})
    assert not r.is_error
    data = json.loads(r.content)
    assert data["tool"] == "read_ui"
    assert data["ok"] is True
    assert data["elements"] == els
    assert len(data["tries"]) == 1 and data["tries"][0]["ok"] is True


async def test_read_ui_backend_failure_recorded():
    plugin = ComputerUsePlugin(backend=_FakeBackend(raise_on_read=RuntimeError("uia dead")))
    r = await plugin.call_tool("read_ui", {"window_title": "Calculator"})
    assert r.is_error
    data = json.loads(r.content)
    assert data["ok"] is False
    assert "uia dead" in data["tries"][0]["actual"]


# ---------------------------------------------------------------------------
# click_element -- happy path + retry-then-succeed + retry-exhaust
# ---------------------------------------------------------------------------

async def test_click_element_happy_path():
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    fake = _FakeBackend([els])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element", {"window_title": "Calc", "name": "Two"}
    )
    assert not r.is_error
    data = json.loads(r.content)
    assert data["ok"] is True
    assert data["target"] == {"name": "Two", "role": "Button", "bbox": [10, 20, 50, 60]}
    assert data["tries"][-1]["ok"] is True
    assert fake.click_calls == [[10, 20, 50, 60]]


async def test_click_element_retries_until_visible():
    """First observation is empty; the button shows up on try 2 and gets clicked."""
    fake = _FakeBackend([[], _elems(("Two", "Button", [10, 20, 50, 60]))])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element", {"window_title": "Calc", "name": "Two", "retries": 3}
    )
    assert not r.is_error
    data = json.loads(r.content)
    assert data["ok"] is True
    assert len(data["tries"]) == 2
    assert data["tries"][0]["ok"] is False
    assert data["tries"][1]["ok"] is True
    assert fake.click_calls == [[10, 20, 50, 60]]


async def test_click_element_exhausts_retries_when_element_missing():
    fake = _FakeBackend([[], [], []])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element",
        {"window_title": "Calc", "name": "Ghost", "retries": 3},
    )
    assert r.is_error
    data = json.loads(r.content)
    assert data["ok"] is False
    assert len(data["tries"]) == 3
    assert all(not t["ok"] for t in data["tries"])
    assert fake.click_calls == []  # never actuated


async def test_click_element_respects_role_filter():
    els = _elems(("OK", "Text", [0, 0, 10, 10]), ("OK", "Button", [50, 50, 90, 90]))
    fake = _FakeBackend([els])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element", {"window_title": "Dlg", "name": "OK", "role": "Button"}
    )
    data = json.loads(r.content)
    assert data["ok"] is True
    assert data["target"]["role"] == "Button"
    assert fake.click_calls == [[50, 50, 90, 90]]


async def test_click_element_bad_bbox_records_and_retries():
    els = _elems(("Two", "Button", []))  # bad bbox
    fake = _FakeBackend([els])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element", {"window_title": "Calc", "name": "Two", "retries": 2}
    )
    assert r.is_error
    data = json.loads(r.content)
    assert data["ok"] is False
    assert fake.click_calls == []


async def test_retries_clamped_to_max():
    fake = _FakeBackend([[]])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element",
        {"window_title": "Calc", "name": "X", "retries": 99},
    )
    data = json.loads(r.content)
    assert len(data["tries"]) == MAX_RETRY_LIMIT


async def test_retries_default_when_unspecified():
    fake = _FakeBackend([[]])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element", {"window_title": "Calc", "name": "X"}
    )
    data = json.loads(r.content)
    assert len(data["tries"]) == DEFAULT_RETRY_LIMIT


# ---------------------------------------------------------------------------
# type_into -- verify with vs without a value read
# ---------------------------------------------------------------------------

async def test_type_into_verify_via_element_value():
    """After typing, re-read UIA and verify the target's value contains the text."""
    field_before = {"name": "Box", "role": "Edit", "bbox": [0, 0, 100, 20], "value": ""}
    field_after = {"name": "Box", "role": "Edit", "bbox": [0, 0, 100, 20], "value": "hello"}
    fake = _FakeBackend([[field_before], [field_after]])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "type_into",
        {"window_title": "App", "name": "Box", "text": "hello"},
    )
    assert not r.is_error
    data = json.loads(r.content)
    assert data["ok"] is True
    assert fake.type_calls == ["hello"]
    assert data["tries"][-1]["ok"] is True


async def test_type_into_retries_when_value_mismatches():
    """First post-type value doesn't match; second try succeeds."""
    box = {"name": "Box", "role": "Edit", "bbox": [0, 0, 100, 20], "value": ""}
    good = {"name": "Box", "role": "Edit", "bbox": [0, 0, 100, 20], "value": "hi"}
    # Sequence: pre1, post1(empty=bad), pre2, post2(good).
    fake = _FakeBackend([[box], [box], [box], [good]])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "type_into",
        {"window_title": "App", "name": "Box", "text": "hi", "retries": 3},
    )
    assert not r.is_error
    data = json.loads(r.content)
    assert data["ok"] is True
    assert len(data["tries"]) == 2
    assert fake.type_calls == ["hi", "hi"]


async def test_type_into_no_value_field_assumed_ok():
    """Backend without 'value' field -> best-effort verify: acted == ok."""
    box = {"name": "Box", "role": "Edit", "bbox": [0, 0, 100, 20]}
    fake = _FakeBackend([[box], [box]])
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "type_into",
        {"window_title": "App", "name": "Box", "text": "z"},
    )
    assert not r.is_error
    data = json.loads(r.content)
    assert data["ok"] is True


# ---------------------------------------------------------------------------
# Trace persistence seam -- record_trace_fn
# ---------------------------------------------------------------------------

async def test_record_trace_fn_fires_on_success():
    calls: list[dict] = []
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    plugin = ComputerUsePlugin(backend=_FakeBackend([els]), record_trace_fn=calls.append)
    await plugin.call_tool("click_element", {"window_title": "C", "name": "Two"})
    assert len(calls) == 1
    assert calls[0]["tool"] == "click_element"
    assert calls[0]["ok"] is True


async def test_record_trace_fn_fires_on_failure():
    calls: list[dict] = []
    plugin = ComputerUsePlugin(backend=_FakeBackend([[]]), record_trace_fn=calls.append)
    await plugin.call_tool(
        "click_element", {"window_title": "C", "name": "Ghost", "retries": 1}
    )
    assert len(calls) == 1
    assert calls[0]["ok"] is False


async def test_record_trace_fn_never_receives_frame_bytes():
    """ADR-0016 audio-buffer rule: raw frames never persist. Trace has no bytes."""
    calls: list[dict] = []
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    plugin = ComputerUsePlugin(backend=_FakeBackend([els]), record_trace_fn=calls.append)
    await plugin.call_tool("click_element", {"window_title": "C", "name": "Two"})

    def _has_bytes(v):
        if isinstance(v, (bytes, bytearray)):
            return True
        if isinstance(v, dict):
            return any(_has_bytes(x) for x in v.values())
        if isinstance(v, list):
            return any(_has_bytes(x) for x in v)
        return False
    assert not _has_bytes(calls[0])


async def test_record_trace_sink_failure_does_not_break_tool():
    def _boom(_):
        raise RuntimeError("sink broken")
    plugin = ComputerUsePlugin(
        backend=_FakeBackend([_elems(("Two", "Button", [10, 20, 50, 60]))]),
        record_trace_fn=_boom,
    )
    r = await plugin.call_tool("click_element", {"window_title": "C", "name": "Two"})
    assert not r.is_error  # trace-sink error must not fail the call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_find_element_case_insensitive():
    els = _elems(("OK", "Button", [0, 0, 1, 1]))
    assert _find_element(els, "ok", None) is not None
    assert _find_element(els, "OK ", None) is not None
    assert _find_element(els, "Cancel", None) is None


async def test_unknown_tool_returns_error():
    plugin = ComputerUsePlugin(backend=_FakeBackend())
    r = await plugin.call_tool("something_else", {})
    assert r.is_error


# ---------------------------------------------------------------------------
# create() factory + non-Windows fail-closed on the factory too
# ---------------------------------------------------------------------------

def test_create_factory_fails_closed_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    plugin = cu_mod.create()
    assert plugin._backend is None


# ---------------------------------------------------------------------------
# S2 #576 -- 3-part kill switch + window-bounded region + driving broadcast
# ---------------------------------------------------------------------------

class _FakeBackendWithBounds(_FakeBackend):
    """Backend that reports a fixed window rect, so the bounded-region check
    engages. Existing S1 fake has no window_bounds and correctly disables
    the check for backwards compatibility."""

    def __init__(self, *a, window_bounds: list[int] | None = None, **kw) -> None:
        super().__init__(*a, **kw)
        self._window_bounds = window_bounds

    def window_bounds(self, window_title: str) -> list[int] | None:
        return list(self._window_bounds) if self._window_bounds else None


# ---- (a) Corner-failsafe leg ---------------------------------------------

async def test_click_aborts_on_corner_failsafe_mid_action():
    """CornerAbort raised by backend.click -> loop short-circuits with an
    'aborted' try, no further retries fired."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    fake = _FakeBackend([els, els, els], raise_on_click=CornerAbort("corner"))
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element",
        {"window_title": "Calc", "name": "Two", "retries": 5},
    )
    assert r.is_error
    data = json.loads(r.content)
    assert data["ok"] is False
    # One try, ended with the corner-failsafe abort marker.
    assert len(data["tries"]) == 1
    assert "corner-failsafe" in data["tries"][0]["actual"]
    # Abort event was set -- a subsequent tool call resets it (see below).
    assert plugin._abort_event.is_set()


async def test_type_aborts_on_corner_failsafe():
    box = {"name": "Box", "role": "Edit", "bbox": [0, 0, 50, 20]}
    fake = _FakeBackend([[box]], raise_on_click=CornerAbort("corner"))
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "type_into",
        {"window_title": "App", "name": "Box", "text": "hi", "retries": 3},
    )
    assert r.is_error
    data = json.loads(r.content)
    assert "corner-failsafe" in data["tries"][-1]["actual"]


async def test_abort_event_resets_between_tool_calls():
    """A prior abort must not silently short-circuit the next tool call."""
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    plugin = ComputerUsePlugin(backend=_FakeBackend([els]))
    plugin.abort()  # simulate an earlier Stop
    r = await plugin.call_tool(
        "click_element", {"window_title": "Calc", "name": "Two"},
    )
    assert not r.is_error, json.loads(r.content)


# ---- (b) F11+F12 chord leg -----------------------------------------------

def test_hotkey_register_fn_receives_abort_callback_at_construction():
    captured: list = []
    plugin = ComputerUsePlugin(
        backend=_FakeBackend(),
        hotkey_register_fn=lambda cb: captured.append(cb),
    )
    assert len(captured) == 1
    assert callable(captured[0])
    # Calling the captured callback fires the plugin's abort event.
    assert not plugin._abort_event.is_set()
    captured[0]()
    assert plugin._abort_event.is_set()


async def test_hotkey_abort_stops_loop_before_retries_exhaust():
    """Fire the injected 'F11+F12' hotkey between tries -> loop stops early."""
    captured: list = []
    # Empty read_ui -> element not present -> the loop keeps retrying up to
    # 5 tries. We fire the hotkey after one yield so the abort catches at
    # the top of iteration 2 (or 3), well short of 5.
    fake = _FakeBackend([[], [], [], [], []])
    plugin = ComputerUsePlugin(
        backend=fake,
        hotkey_register_fn=lambda cb: captured.append(cb),
    )
    hotkey_cb = captured[0]

    async def _fire_hotkey_soon() -> None:
        # Yield twice so the loop starts its first try; then trigger the abort.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        hotkey_cb()

    call = plugin.call_tool(
        "click_element", {"window_title": "X", "name": "Ghost", "retries": 5},
    )
    r, _ = await asyncio.gather(call, _fire_hotkey_soon())
    assert r.is_error
    data = json.loads(r.content)
    assert len(data["tries"]) < 5, data["tries"]
    assert data["tries"][-1]["actual"] == "aborted by kill switch"


def test_hotkey_register_failure_does_not_disable_plugin():
    """A broken hotkey registrar must not crash the plugin -- (a) + (c) legs
    remain usable even if (b) is unavailable."""
    def _boom(_cb):
        raise RuntimeError("no keyboard package")
    plugin = ComputerUsePlugin(backend=_FakeBackend(), hotkey_register_fn=_boom)
    # Still callable; abort event exists.
    plugin.abort()
    assert plugin._abort_event.is_set()


# ---- (c) Visualiser "Felix is driving" broadcast + Stop ------------------

async def test_driving_broadcast_fires_true_then_false_around_click():
    calls: list[bool] = []
    async def _driving(state: bool) -> None:
        calls.append(state)
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    plugin = ComputerUsePlugin(backend=_FakeBackend([els]), driving_fn=_driving)
    await plugin.call_tool("click_element", {"window_title": "C", "name": "Two"})
    assert calls == [True, False]


async def test_driving_broadcast_flips_off_even_on_error():
    """A failing loop must still emit driving=False so the Stop control isn't
    left stuck on after the tool call ends."""
    calls: list[bool] = []
    async def _driving(state: bool) -> None:
        calls.append(state)
    plugin = ComputerUsePlugin(
        backend=_FakeBackend([[]]), driving_fn=_driving,
    )
    await plugin.call_tool(
        "click_element", {"window_title": "C", "name": "Ghost", "retries": 1},
    )
    assert calls == [True, False]


async def test_driving_broadcast_failure_does_not_break_tool():
    async def _broken(_state: bool) -> None:
        raise RuntimeError("sink dead")
    els = _elems(("Two", "Button", [10, 20, 50, 60]))
    plugin = ComputerUsePlugin(backend=_FakeBackend([els]), driving_fn=_broken)
    r = await plugin.call_tool("click_element", {"window_title": "C", "name": "Two"})
    assert not r.is_error


async def test_abort_current_signals_the_singleton_plugin():
    """Module-level abort_current() (wired to the Visualiser Stop IPC) fires
    the abort event of the last-constructed plugin instance."""
    plugin = ComputerUsePlugin(backend=_FakeBackend())
    assert not plugin._abort_event.is_set()
    cu_mod.abort_current()
    assert plugin._abort_event.is_set()


# ---- Loop yields between actions -----------------------------------------

async def test_loop_yields_between_iterations_for_peer_tasks():
    """The retry loop must ``await asyncio.sleep(0)`` between tries so other
    coroutines (input handling, the abort event, the driving broadcast) get
    to run. Verifiable by a peer task that ticks on each yield."""
    fake = _FakeBackend([[], [], [], [], []])
    plugin = ComputerUsePlugin(backend=fake)
    tick = 0

    async def _peer() -> None:
        nonlocal tick
        for _ in range(20):
            tick += 1
            await asyncio.sleep(0)

    call = plugin.call_tool(
        "click_element", {"window_title": "X", "name": "Ghost", "retries": 5},
    )
    await asyncio.gather(call, _peer())
    # If the loop never yielded, the peer would only tick once (before the
    # call was awaited) or after the call completed. With per-iteration yields
    # the peer runs interleaved -- at least 2 ticks recorded during 5 tries.
    assert tick >= 2, tick


# ---- Window-bounded region -----------------------------------------------

def test_bbox_within_helper():
    assert _bbox_within([10, 10, 50, 50], [0, 0, 100, 100]) is True
    assert _bbox_within([0, 0, 100, 100], [0, 0, 100, 100]) is True  # equal edges
    assert _bbox_within([90, 90, 110, 110], [0, 0, 100, 100]) is False  # right/bottom out
    assert _bbox_within([-1, 0, 10, 10], [0, 0, 100, 100]) is False  # left out
    assert _bbox_within([0, 0, 0], [0, 0, 100, 100]) is False  # malformed


async def test_click_refused_when_bbox_outside_window_bounds():
    """A click aimed outside the target window's bounds is refused and never
    actuated. Fresh backend so a mis-grounded coordinate can't hit another app."""
    el_outside = {"name": "Ghost", "role": "Button", "bbox": [500, 500, 550, 550]}
    fake = _FakeBackendWithBounds(
        [[el_outside]], window_bounds=[0, 0, 200, 200],
    )
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element",
        {"window_title": "SmallApp", "name": "Ghost", "retries": 1},
    )
    assert r.is_error
    data = json.loads(r.content)
    assert data["ok"] is False
    assert "outside window bounds" in data["tries"][-1]["actual"]
    assert fake.click_calls == []  # NEVER actuated


async def test_click_accepted_when_bbox_inside_window_bounds():
    el_inside = {"name": "OK", "role": "Button", "bbox": [50, 50, 90, 90]}
    fake = _FakeBackendWithBounds(
        [[el_inside]], window_bounds=[0, 0, 200, 200],
    )
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element",
        {"window_title": "App", "name": "OK"},
    )
    assert not r.is_error
    assert fake.click_calls == [[50, 50, 90, 90]]


async def test_type_into_refused_outside_window_bounds():
    el_outside = {"name": "Field", "role": "Edit", "bbox": [500, 10, 600, 30]}
    fake = _FakeBackendWithBounds(
        [[el_outside]], window_bounds=[0, 0, 200, 200],
    )
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "type_into",
        {"window_title": "App", "name": "Field", "text": "hi", "retries": 1},
    )
    assert r.is_error
    assert fake.type_calls == []  # NEVER typed


async def test_bounds_check_skipped_when_backend_returns_none():
    """A backend that can't report bounds (window missing / no support) must
    not block otherwise-valid actions -- the bounded-region check is a soft
    guard, not the fail-closed gate."""
    el = {"name": "OK", "role": "Button", "bbox": [500, 500, 550, 550]}
    fake = _FakeBackendWithBounds([[el]], window_bounds=None)
    plugin = ComputerUsePlugin(backend=fake)
    r = await plugin.call_tool(
        "click_element", {"window_title": "X", "name": "OK"},
    )
    assert not r.is_error
    assert fake.click_calls == [[500, 500, 550, 550]]
