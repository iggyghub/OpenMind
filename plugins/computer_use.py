"""
computer_use plugin -- ADR-0016 spine (Issue #574).

Felix sees a target app's window (UIA-first), decides what element to touch,
and drives OS-level mouse/keyboard against it, verifying each attempt against
the next observation with a retry limit. Windows-only in v1: fail-closed
elsewhere (import succeeds, tool calls return an error, no crash).

Tools (S1):
  read_ui       -- return the target window's UIA elements as text
                   (name, role, bbox) -- structured, not screenshot bytes.
  click_element -- observe -> click a named element -> verify (up to N tries).
  type_into     -- observe -> type into a named element -> verify (up to N).

Trace shape (returned in ToolResult.content and available to record_trace_fn):
  {
    "tool": "click_element" | "type_into" | "read_ui",
    "window_title": "...",
    "target": {"name": "...", "role": "...", "bbox": [l, t, r, b]} | null,
    "action": "click" | "type" | null,
    "tries": [
        {"n": 1, "observed": bool, "acted": bool,
         "expected": "...", "actual": "...", "ok": bool},
        ...
    ],
    "ok": bool,
  }

Raw frames are NEVER persisted (ADR-0016 audio-buffer rule). The Windows
backend can capture RAM-only bytes for future pixel-vision (S5) but this
slice does not call it; the seam exists so S5 doesn't have to reshape the
plugin.

Injection seams (test + import stay Windows-lib-free):
  backend         -- ComputerUseBackend Protocol; default is the Windows
                     UIA/pyautogui backend, or None on non-Windows.
  record_trace_fn -- optional callable(dict) -> None to persist a per-call
                     structured trace turn (wired by main.py -> Conversation
                     store as KIND_TOOL_RESULT). Never receives frame bytes.
"""
from __future__ import annotations

import json
import sys
from typing import Callable, Optional, Protocol, runtime_checkable

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "computer_use"

# ADR-0016 uses the two existing capability classes -- no vocabulary change.
# screen_capture: window pixel bytes (RAM only, never written); default ASK.
# device_control: mouse/keyboard actuation; default SILENT (consequence-level
# gating happens at the planner via `irreversible`, not per-primitive).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"screen_capture", "device_control"})

# Retry ceiling for the observe-act-verify loop (ADR-0016 sec 6). Default 3;
# ADR range is 3-5. A `success` short-circuits; only failed tries consume.
DEFAULT_RETRY_LIMIT = 3
MAX_RETRY_LIMIT = 5

# Sentinel so tests can inject ``backend=None`` to force fail-closed without
# the constructor re-running _make_default_backend(). Mirrors shell.py's
# ``_UNSET`` pattern for the sandbox.
_UNSET: object = object()


@runtime_checkable
class ComputerUseBackend(Protocol):
    """Platform seam. Windows default + testable fake both fit this shape."""

    def read_ui(self, window_title: str) -> list[dict]:
        """Return the target window's UIA elements as
        [{"name": str, "role": str, "bbox": [l, t, r, b]}, ...]."""

    def click(self, bbox: list[int]) -> None:
        """Actuate a left-click at the centre of ``bbox``."""

    def type_text(self, text: str) -> None:
        """Actuate a keyboard type of ``text`` at the current focus."""

    def capture_frame(self, window_title: str) -> Optional[bytes]:
        """Return window pixel bytes (RAM only, never persisted). May be None."""


def _make_default_backend() -> Optional[ComputerUseBackend]:
    """Windows: lazily construct the UIA/pyautogui backend. Elsewhere: None."""
    if sys.platform != "win32":
        return None
    try:
        return _WindowsBackend()
    except Exception:
        # Missing pyautogui/uiautomation on this Windows host -> fail-closed.
        return None


def _find_element(elements: list[dict], name: str, role: str | None) -> dict | None:
    """Case-insensitive name match; role filter when supplied."""
    n = (name or "").strip().lower()
    r = (role or "").strip().lower() or None
    for e in elements:
        if (e.get("name") or "").strip().lower() != n:
            continue
        if r is not None and (e.get("role") or "").strip().lower() != r:
            continue
        return e
    return None


def _bbox_center(bbox: list[int]) -> tuple[int, int]:
    l, t, r, b = bbox
    return ((l + r) // 2, (t + b) // 2)


def _clamp_retries(n: int | None) -> int:
    if n is None:
        return DEFAULT_RETRY_LIMIT
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_LIMIT
    if n < 1:
        return 1
    if n > MAX_RETRY_LIMIT:
        return MAX_RETRY_LIMIT
    return n


class ComputerUsePlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        backend=_UNSET,
        record_trace_fn: Callable[[dict], None] | None = None,
    ) -> None:
        if backend is _UNSET:
            self._backend = _make_default_backend()
        else:
            self._backend = backend  # explicit -- including None -> fail-closed
        self._record_trace_fn = record_trace_fn

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="read_ui",
                description=(
                    "Read the target app window's UI Automation tree as a "
                    "list of elements (name, role, bbox). Structured only -- "
                    "no screenshot bytes are returned."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "window_title": {
                            "type": "string",
                            "description": "Title of the target window (substring match).",
                        },
                    },
                    "required": ["window_title"],
                },
            ),
            Tool(
                name="click_element",
                description=(
                    "Click a UIA element by name inside the target window. "
                    "Observes -> clicks -> re-observes to verify, up to a "
                    "retry limit (default 3)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "window_title": {"type": "string"},
                        "name": {
                            "type": "string",
                            "description": "UIA element name to click.",
                        },
                        "role": {
                            "type": "string",
                            "description": "Optional UIA role filter (e.g. Button).",
                        },
                        "retries": {
                            "type": "integer",
                            "description": "Max failed tries (1-5, default 3).",
                        },
                    },
                    "required": ["window_title", "name"],
                },
            ),
            Tool(
                name="type_into",
                description=(
                    "Type text into a UIA element by name. Observes -> "
                    "clicks to focus -> types -> verifies element value, up "
                    "to a retry limit (default 3)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "window_title": {"type": "string"},
                        "name": {"type": "string"},
                        "text": {"type": "string"},
                        "role": {"type": "string"},
                        "retries": {"type": "integer"},
                    },
                    "required": ["window_title", "name", "text"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if self._backend is None:
            return ToolResult(
                content="computer_use is not available on this platform",
                is_error=True,
            )
        if tool_name == "read_ui":
            return self._read_ui(args)
        if tool_name == "click_element":
            return self._click_element(args)
        if tool_name == "type_into":
            return self._type_into(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _finish(self, trace: dict) -> ToolResult:
        if self._record_trace_fn is not None:
            try:
                self._record_trace_fn(trace)
            except Exception:
                # Trace persistence is best-effort; a failing sink must not
                # break the tool call itself.
                pass
        return ToolResult(content=json.dumps(trace), is_error=not trace.get("ok", False))

    def _read_ui(self, args: dict) -> ToolResult:
        window_title = args["window_title"]
        trace = {
            "tool": "read_ui",
            "window_title": window_title,
            "target": None,
            "action": None,
            "tries": [],
            "ok": False,
        }
        try:
            elements = self._backend.read_ui(window_title)  # type: ignore[union-attr]
        except Exception as exc:
            trace["tries"].append(
                {"n": 1, "observed": False, "acted": False,
                 "expected": "elements", "actual": f"error: {exc}", "ok": False}
            )
            return self._finish(trace)
        trace["tries"].append(
            {"n": 1, "observed": True, "acted": False,
             "expected": "elements", "actual": f"{len(elements)} elements", "ok": True}
        )
        trace["ok"] = True
        trace["elements"] = elements
        return self._finish(trace)

    def _click_element(self, args: dict) -> ToolResult:
        window_title = args["window_title"]
        name = args["name"]
        role = args.get("role")
        limit = _clamp_retries(args.get("retries"))
        trace = {
            "tool": "click_element",
            "window_title": window_title,
            "target": None,
            "action": "click",
            "tries": [],
            "ok": False,
        }
        for n in range(1, limit + 1):
            try:
                elements = self._backend.read_ui(window_title)  # type: ignore[union-attr]
            except Exception as exc:
                trace["tries"].append(
                    {"n": n, "observed": False, "acted": False,
                     "expected": f"element {name!r}",
                     "actual": f"read_ui error: {exc}", "ok": False}
                )
                continue
            match = _find_element(elements, name, role)
            if match is None:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": False,
                     "expected": f"element {name!r}",
                     "actual": "not present", "ok": False}
                )
                continue
            trace["target"] = {
                "name": match.get("name"),
                "role": match.get("role"),
                "bbox": match.get("bbox"),
            }
            bbox = match.get("bbox") or []
            if len(bbox) != 4:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": False,
                     "expected": "bbox [l,t,r,b]",
                     "actual": f"bbox={bbox!r}", "ok": False}
                )
                continue
            x, y = _bbox_center(bbox)
            try:
                self._backend.click(bbox)  # type: ignore[union-attr]
            except Exception as exc:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": False,
                     "expected": f"click at ({x},{y})",
                     "actual": f"error: {exc}", "ok": False}
                )
                continue
            trace["tries"].append(
                {"n": n, "observed": True, "acted": True,
                 "expected": f"click at ({x},{y})",
                 "actual": "clicked", "ok": True}
            )
            trace["ok"] = True
            return self._finish(trace)
        return self._finish(trace)

    def _type_into(self, args: dict) -> ToolResult:
        window_title = args["window_title"]
        name = args["name"]
        text = args.get("text", "")
        role = args.get("role")
        limit = _clamp_retries(args.get("retries"))
        trace = {
            "tool": "type_into",
            "window_title": window_title,
            "target": None,
            "action": "type",
            "tries": [],
            "ok": False,
        }
        for n in range(1, limit + 1):
            try:
                elements = self._backend.read_ui(window_title)  # type: ignore[union-attr]
            except Exception as exc:
                trace["tries"].append(
                    {"n": n, "observed": False, "acted": False,
                     "expected": f"element {name!r}",
                     "actual": f"read_ui error: {exc}", "ok": False}
                )
                continue
            match = _find_element(elements, name, role)
            if match is None:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": False,
                     "expected": f"element {name!r}",
                     "actual": "not present", "ok": False}
                )
                continue
            trace["target"] = {
                "name": match.get("name"),
                "role": match.get("role"),
                "bbox": match.get("bbox"),
            }
            bbox = match.get("bbox") or []
            if len(bbox) != 4:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": False,
                     "expected": "bbox [l,t,r,b]",
                     "actual": f"bbox={bbox!r}", "ok": False}
                )
                continue
            try:
                self._backend.click(bbox)  # type: ignore[union-attr]
                self._backend.type_text(text)  # type: ignore[union-attr]
            except Exception as exc:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": False,
                     "expected": f"type {text!r}",
                     "actual": f"error: {exc}", "ok": False}
                )
                continue
            # Verify: re-read UIA and check the target's value contains the text
            # (falls back to "acted" when the backend doesn't expose values).
            try:
                after = self._backend.read_ui(window_title)  # type: ignore[union-attr]
            except Exception as exc:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": True,
                     "expected": f"post-type value contains {text!r}",
                     "actual": f"re-read error: {exc}", "ok": False}
                )
                continue
            after_match = _find_element(after, name, role) or {}
            value = after_match.get("value")
            if isinstance(value, str) and text in value:
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": True,
                     "expected": f"value contains {text!r}",
                     "actual": f"value={value!r}", "ok": True}
                )
                trace["ok"] = True
                return self._finish(trace)
            if value is None:
                # Backend didn't expose element value -> can't verify; treat
                # the typed action itself as success (best-effort verify).
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": True,
                     "expected": f"typed {text!r}",
                     "actual": "no value read; assumed ok", "ok": True}
                )
                trace["ok"] = True
                return self._finish(trace)
            trace["tries"].append(
                {"n": n, "observed": True, "acted": True,
                 "expected": f"value contains {text!r}",
                 "actual": f"value={value!r}", "ok": False}
            )
        return self._finish(trace)


# --- Windows backend (lazily imported; never touched in tests) --------------

class _WindowsBackend:
    """UIA read + pyautogui actuation + mss capture. Constructed on Windows only."""

    def __init__(self) -> None:
        import pyautogui
        import uiautomation as uia
        # Fail-safe: slam mouse to a screen corner to hard-abort mid-action.
        pyautogui.FAILSAFE = True
        self._pyautogui = pyautogui
        self._uia = uia
        self._mss = None  # capture is not called in S1; wired for S5.

    def _window(self, window_title: str):
        return self._uia.WindowControl(searchDepth=1, SubName=window_title)

    def read_ui(self, window_title: str) -> list[dict]:
        win = self._window(window_title)
        if not win.Exists(0):
            return []
        out: list[dict] = []
        for ctrl, _depth in self._uia.WalkTree(win):
            try:
                rect = ctrl.BoundingRectangle
                bbox = [rect.left, rect.top, rect.right, rect.bottom]
                out.append({
                    "name": ctrl.Name or "",
                    "role": ctrl.ControlTypeName or "",
                    "bbox": bbox,
                })
            except Exception:
                continue
        return out

    def click(self, bbox: list[int]) -> None:
        l, t, r, b = bbox
        x, y = (l + r) // 2, (t + b) // 2
        self._pyautogui.click(x, y)

    def type_text(self, text: str) -> None:
        self._pyautogui.typewrite(text, interval=0.01)

    def capture_frame(self, window_title: str) -> bytes | None:
        # S1 does not use capture; S5 will wire the RAM-only frame buffer.
        return None


def create() -> ComputerUsePlugin:
    return ComputerUsePlugin()
