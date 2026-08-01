"""Unit tests for the computer_use plugin (ADR-0016 S1 / Issue #574).

All tests use a fake ComputerUseBackend -- no real UIA, mouse, keyboard, or
screen. Fail-closed on non-Windows is covered by forcing the default backend
to None on sys.platform != "win32".
"""
from __future__ import annotations

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
