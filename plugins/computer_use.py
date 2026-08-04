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

Background actuation (ADR-0016 amendment 2026-08-02, Issue #592):
  click_element / type_into now try a UIA control pattern first (Invoke /
  Toggle / SelectionItem.Select / ExpandCollapse for clicks, ValuePattern.
  SetValue for text) -- no cursor move, no synthetic keystrokes, so the user
  keeps the mouse/keyboard while Felix drives. The live control is re-resolved
  just-in-time per try (``_resolve_element`` on the backend) and never carried
  across an ``await``. When no usable pattern exists, the SAME re-resolved
  element's bbox feeds the existing pyautogui foreground fallback. Each try
  records ``path``: ``"uia_pattern"`` (background), ``"uia_synthetic"``
  (today's foreground bbox click/type, keeps its old behaviour+name), or
  ``"pixel"`` (S5, unchanged). SetValue only fires on an allowlisted role
  (``computer_use.setvalue_roles``, default Edit/Document) outside a browser/
  Electron surface -- it fills, it never submits. Both knobs live in
  felix-settings.json as flat keys ``background_actuation`` (master, default
  on) and ``setvalue_roles`` (default ``["Edit", "Document"]``; empty forces
  all text through foreground typing while leaving pattern clicks background).
  Idle-gating the foreground fallback (#593) and live-verify docs (#595) are
  the other follow-on slices -- #595 is not built here.

Idle gate + focus-theft probe (ADR-0016 amendment d/g, Issue #593):
  Before ANY pyautogui foreground action -- the click_element/type_into
  fallback, browser_navigate, and the pixel-vision click -- the plugin
  checks ``GetLastInputInfo`` via the backend's ``last_input_ms()``. If the
  user touched input more recently than ``computer_use.user_idle_ms``
  (default 4000ms) they are PRESENT, so Felix does not grab input: the try
  is recorded as waiting and the existing observe-act-verify retry loop
  carries the wait, escalating to attended-handoff (S6) on exhaustion same
  as any other failure -- no new escalation path. Full-autonomy bypasses the
  gate exactly as it bypasses the irreversible floor (sec 4). Independently,
  every actuated try (pattern AND foreground) captures
  ``GetForegroundWindow()`` before/after via ``foreground_window()`` and
  stamps ``foregrounded: bool``. A BACKGROUND pattern action that
  unexpectedly foregrounds the target is a SOFT TRIP: the try is recorded
  failed and the call returns immediately -- it never falls through to the
  input-stealing foreground fallback. Both backend methods are optional:
  absent -> the gate always allows (unknown idle can't block) and
  ``foregrounded`` always stamps False (matches pre-#593 behaviour).

Mode-aware driving indicator (ADR-0016 amendment f, Issue #594):
  A background pattern action has no moving cursor, so the "Felix is
  driving" broadcast is its only feedback. ``_emit_driving`` therefore
  carries a payload -- not a bare bool -- with ``mode: "background" |
  "foreground"``, ``window_title``, and ``action`` alongside ``driving``.
  The initial guess for a click/type call is "background" when
  ``background_actuation`` is on (a pattern is attempted first), else
  "foreground"; ``browser_navigate`` has no control-pattern equivalent so it
  is always "foreground". The call sites re-emit with ``mode="foreground"``
  the moment a try actually falls through to the pyautogui fallback, and
  again immediately on a #593 soft trip -- so a focus-theft mid-action flips
  the live indicator to the foreground/urgent style in real time, same
  broadcast, no second gate. The ``irreversible``/``is_committing_action``
  gate is untouched (sec 3): this is visibility only.

Mode ladder + session-2 relaxations (ADR-0016 S16 #610):
  ``select_actuation_tier(needs_foreground, live_desktop_only)`` exposes the
  three-tier planner ladder: ``"background"`` (UIA control patterns, no
  cursor), ``"isolated_session"`` (Felix's dedicated session, default for
  foreground/pixel work), ``"take_turns"`` (live-desktop foreground, opt-in
  when a target exists ONLY in the user's session). Inside session 2 (i.e.
  when ``_session_dispatch_fn`` is wired -- ``_in_isolated_session()`` is
  True): the window-bounded region check and the foreground idle-gate are
  both dropped (full-desktop actions are allowed and there is no user cursor
  to yield to in session 2). screen_capture consent is silenced for session-2
  tool calls at the main.py capability-check layer. A ``FailureNotifyFn``
  seam is called whenever a dedicated-path dispatch raises (worker disconnect,
  session death) -- it fires before falling back to the local backend so the
  user is NEVER left in a silent-failure state. The consequence/irreversible
  modal is unchanged: committing actions still route through the ADR-0005
  modal regardless of session.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import re
import sys
from collections import deque
from typing import Awaitable, Callable, Literal, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "computer_use"

# ADR-0016 uses the two existing capability classes -- no vocabulary change.
# screen_capture: window pixel bytes (RAM only, never written); default ASK.
# device_control: mouse/keyboard actuation; default SILENT (consequence-level
# gating happens at the planner via `irreversible`, not per-primitive).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"screen_capture", "device_control"})

# ADR-0016 sec 3 -- consequence-level gate. device_control primitives are
# SILENT, but a click whose target *commits* an unrecoverable effect
# (send / submit / delete / pay ...) is flagged irreversible so it routes
# through the ADR-0005 modal. The classifier is intentionally conservative:
# it matches whole words in the UIA element name so "Send" trips but
# "Sender name" (a text field) does not, and "Two"/"Plus"/"Cancel" never do.
# Cerebral (main.py) calls is_committing_action at the gate site and, when it
# returns True, raises the irreversible CallFlag so the action routes through
# the modal. No Tool here declares a static irreversible marking -- the
# consequence is dynamic (depends on the click target). On the pixel-fallback path
# the intent still arrives as a named target, so the same classification
# applies before the UIA-vs-pixel resolution is known -- the gate sees the
# planner's intent, not the resolution path.
_COMMIT_VERBS: frozenset[str] = frozenset({
    "send", "submit", "post", "publish", "delete", "remove", "discard",
    "pay", "buy", "purchase", "checkout", "order", "confirm", "transfer",
    "withdraw", "deposit", "unsubscribe", "deactivate", "disable",
})


def is_committing_action(tool_name: str, args: dict | None) -> bool:
    """True when a computer_use call commits an unrecoverable effect.

    Only click_element (and the browser URL-submit) can commit; read_ui and
    plain typing do not. Matches a whole word in the target element name
    against _COMMIT_VERBS. Pure + import-free so Cerebral's gate wiring and
    the test suite can call it without a plugin instance.
    """
    args = args or {}
    if tool_name != "click_element":
        return False
    name = str(args.get("name") or "").lower()
    if not name:
        return False
    words = set(re.findall(r"[a-z]+", name))
    return bool(words & _COMMIT_VERBS)

# Retry ceiling for the observe-act-verify loop (ADR-0016 sec 6). Default 3;
# ADR range is 3-5. A `success` short-circuits; only failed tries consume.
DEFAULT_RETRY_LIMIT = 3
MAX_RETRY_LIMIT = 5

# read_ui subtree walk depth cap. ponytail: guards a pathological UIA tree
# (a browser DOM surfaced as UIA can be tens of thousands of nodes deep);
# raise it if a real target legitimately sits deeper than this.
_UIA_MAX_DEPTH = 50

# S5 #578: RAM-only thumbnail ring for in-session debug. Bytes are held for
# the lifetime of the plugin instance and dropped when the ring rolls over;
# nothing is written to disk (ADR-0016 sec 7 audio-buffer rule). "Cleared
# on restart" = the ring lives in process memory, so a Cerebral restart
# creates a fresh empty ring.
DEFAULT_THUMBNAIL_RING_SIZE = 8

# Sentinel so tests can inject ``backend=None`` to force fail-closed without
# the constructor re-running _make_default_backend(). Mirrors shell.py's
# ``_UNSET`` pattern for the sandbox.
_UNSET: object = object()


class CornerAbort(Exception):
    """Backend raised the pyautogui corner-failsafe: mouse slammed to a screen
    corner mid-action. The plugin treats this as the (a) kill-switch leg of
    the ADR-0016 three-part containment: hard-abort the observe-act loop."""


# Module-level seams -- main.py wires these once at startup; tests inject
# per-instance via the constructor (constructor-injected wins).
# #594 (ADR-0016 amendment f): the payload evolved from a bare bool to a
# dict -- {"driving": bool, "mode": "background"|"foreground", "window_title":
# str, "action": str} -- so the mode-aware indicator has somewhere to read
# mode/window/action from. A sink written against the old bool-only contract
# still receives a single positional argument and does not crash; it simply
# needs updating to read ``payload["driving"]`` (main.py + the tray renderer
# are updated together with this change).
DrivingFn = Callable[[dict], Awaitable[None]]
HotkeyRegisterFn = Callable[[Callable[[], None]], Optional[Callable[[], None]]]
# S5 #578: pixel-vision grounding seam. Given the target element name (as the
# user described it) and the target window's raw frame bytes, return a
# ``(x, y)`` screen coordinate to click, or None when grounding refuses /
# fails. Wired in main.py to run through the router's ``complete_with_images``
# priority chain (ADR-0016 sec 5); tests inject a fake to avoid a live model.
VisionGroundFn = Callable[[str, bytes], Awaitable[Optional[tuple[int, int]]]]
# S6 #579: attended-handoff seam. Given the target window title and a compact
# reason, notify the user + surface the target window + await the human's
# "done" (True) or "declined" (False). Wired in main.py to emit the notify +
# broadcast pair and await an IPC ``computer_use_handoff_done`` reply; tests
# inject a fake that returns immediately. When unwired, the plugin falls
# through with the failed trace (matches pre-S6 behaviour on exhaustion).
AttendedHandoffFn = Callable[[str, str], Awaitable[bool]]
# #592 (ADR-0016 amendment): felix-settings.json getters, mirroring
# browser_session.py's ``set_pause_on_verification`` seam exactly -- a
# nullary callable read fresh on every actuation (never cached), so a
# mid-session settings change takes effect on the next tool call.
BackgroundActuationFn = Callable[[], bool]
SetValueRolesFn = Callable[[], list[str]]
# #593 (ADR-0016 amendment d): same getter-seam pattern for the idle gate.
# ``UserIdleMsFn`` reads ``computer_use.user_idle_ms``; ``FullAutonomyFn``
# mirrors main.py's ``_computer_use_full_autonomy`` badge switch (sec 4) --
# full-autonomy bypasses the idle gate exactly as it bypasses the
# irreversible floor, so it needs the same read-fresh getter, not a copy.
UserIdleMsFn = Callable[[], int]
FullAutonomyFn = Callable[[], bool]
# S11 #605: async fn that routes a primitive action to the in-session worker.
# Signature: (action: str, params: dict) -> Awaitable[dict]
# When set, the 3 core primitives (read_ui / click / type) route to the worker
# instead of the local _WindowsBackend; None = local backend (default).
SessionDispatchFn = Callable[..., "Awaitable[dict]"]
# S15 #609: broadcast one just-captured window frame to the Visualiser as a
# passive thumbnail stream. Fire-and-forget: a broken sink must never break a
# tool call. Wired by main.py to _broadcast_thumbnail; unwired = ring-only
# (matches pre-S15 behaviour).
ThumbnailEmitFn = Callable[[bytes], "Awaitable[None]"]
# S16 #610: notified when a dedicated-path (isolated-session worker) dispatch
# fails. Arguments: (mode, reason, fallback). mode is the tier that failed
# ("isolated_session"); reason is the exception message; fallback is the tier
# the plugin is falling back to ("take_turns" on attended fallback). Wired by
# main.py to _notify_user so the user is never left in a silent-failure state.
FailureNotifyFn = Callable[[str, str, str], "Awaitable[None]"]

_driving_fn: Optional[DrivingFn] = None
_hotkey_register_fn: Optional[HotkeyRegisterFn] = None
_vision_ground_fn: Optional[VisionGroundFn] = None
_attended_handoff_fn: Optional[AttendedHandoffFn] = None
_background_actuation_fn: Optional[BackgroundActuationFn] = None
_setvalue_roles_fn: Optional[SetValueRolesFn] = None
_user_idle_ms_fn: Optional[UserIdleMsFn] = None
_full_autonomy_fn: Optional[FullAutonomyFn] = None
_session_dispatch_fn: Optional["SessionDispatchFn"] = None  # S11 #605
_terminate_worker_fn: Optional[Callable[[], None]] = None  # S12 #606
_thumbnail_emit_fn: Optional["ThumbnailEmitFn"] = None  # S15 #609
_failure_notify_fn: Optional["FailureNotifyFn"] = None  # S16 #610
_plugin_instance: Optional["ComputerUsePlugin"] = None

# ADR-0016 amendment defaults -- used whenever no getter is wired (tests,
# or a host that hasn't started main.py's settings wiring yet). Match
# felix-settings.json's shipped defaults in cerebral/settings.py.
DEFAULT_BACKGROUND_ACTUATION = True
DEFAULT_SETVALUE_ROLES: tuple[str, ...] = ("Edit", "Document")
# #593: midpoint of the ADR's documented 3000-5000ms range.
DEFAULT_USER_IDLE_MS = 4000
DEFAULT_FULL_AUTONOMY = False


def set_driving_fn(fn: DrivingFn) -> None:
    """Wire main.py's async broadcaster for the "Felix is driving" IPC event
    that lights up the Visualiser's Stop control."""
    global _driving_fn
    _driving_fn = fn


def set_hotkey_register_fn(fn: HotkeyRegisterFn) -> None:
    """Wire the F11+F12 global-hotkey registrar. Called once with a nullary
    ``abort`` callback; may return an unregister function (unused today)."""
    global _hotkey_register_fn
    _hotkey_register_fn = fn


def set_vision_ground_fn(fn: VisionGroundFn) -> None:
    """Wire the pixel-vision grounding seam (S5 #578). Fallback stays disabled
    when this is unset -- structured-only is the safe default."""
    global _vision_ground_fn
    _vision_ground_fn = fn


def set_attended_handoff_fn(fn: AttendedHandoffFn) -> None:
    """Wire the attended-handoff seam (S6 #579). Called on retry exhaustion or
    DRM-black escalation -- surface the target window + await the human. When
    unwired the plugin fails the tool call as before (no silent handoff)."""
    global _attended_handoff_fn
    _attended_handoff_fn = fn


def set_background_actuation_fn(fn: BackgroundActuationFn) -> None:
    """Wire a getter for the ``background_actuation`` setting (#592 master
    switch). True (default) tries the UIA control pattern before pyautogui;
    False restores pre-amendment pure-foreground behaviour."""
    global _background_actuation_fn
    _background_actuation_fn = fn


def set_setvalue_roles_fn(fn: SetValueRolesFn) -> None:
    """Wire a getter for the ``setvalue_roles`` allowlist (#592). Controls
    whose UIA role is in the list may be filled via ValuePattern.SetValue;
    an empty list keeps pattern clicks background but forces all text
    through foreground typing (SetValue is the ONLY gated primitive)."""
    global _setvalue_roles_fn
    _setvalue_roles_fn = fn


def set_user_idle_ms_fn(fn: UserIdleMsFn) -> None:
    """Wire a getter for the ``user_idle_ms`` setting (#593, ADR-0016
    amendment d). Read fresh before every foreground (pyautogui) action --
    a present user (last input younger than this threshold) blocks the
    input-stealing fallback."""
    global _user_idle_ms_fn
    _user_idle_ms_fn = fn


def set_full_autonomy_fn(fn: FullAutonomyFn) -> None:
    """Wire a getter mirroring main.py's badged full-autonomy switch (#593,
    ADR-0016 amendment d / sec 4). While on, the foreground fallback ignores
    the idle gate exactly as full-autonomy already bypasses the irreversible
    floor for computer_use."""
    global _full_autonomy_fn
    _full_autonomy_fn = fn


def set_session_dispatch_fn(fn: "SessionDispatchFn | None") -> None:
    """S11 #605: wire (or clear) the in-session worker dispatch seam.

    When set and isolated_session_mode is on, the 3 core primitives
    (read_ui / click / type) are routed to the in-session worker over
    Cerebral's WS IPC instead of the local _WindowsBackend. Pass None to
    restore local-backend routing (worker disconnected / mode off)."""
    global _session_dispatch_fn
    _session_dispatch_fn = fn


def set_terminate_worker_fn(fn: "Callable[[], None] | None") -> None:
    """S12 #606: wire (or clear) the out-of-session worker termination seam.

    Called by main.py when a worker process handle is tracked. When set,
    abort_current() (Visualiser Stop + F11+F12 path) also kills the worker
    process so stop always crosses the session boundary. Pass None to clear
    (worker disconnected / handle released)."""
    global _terminate_worker_fn
    _terminate_worker_fn = fn


def abort_current() -> None:
    """Module-level abort: signals the kill-switch. IPC in main.py calls this
    on a ``computer_use_stop`` message; F11+F12 routes through this too (S12).

    S12 #606: also calls _terminate_worker_fn() when wired so the in-session
    worker is killed even if the WS heartbeat leg hasn't fired yet."""
    if _plugin_instance is not None:
        _plugin_instance.abort()
    if _terminate_worker_fn is not None:
        try:
            _terminate_worker_fn()
        except Exception:
            logger.warning("[computer_use] terminate_worker_fn failed", exc_info=True)


def set_thumbnail_emit_fn(fn: "ThumbnailEmitFn | None") -> None:
    """S15 #609: wire (or clear) the passive thumbnail-stream broadcast seam.

    Called once per captured window frame with the raw bytes so main.py can
    forward it to the Visualiser. Best-effort: a failure never propagates."""
    global _thumbnail_emit_fn
    _thumbnail_emit_fn = fn


def set_failure_notify_fn(fn: "FailureNotifyFn | None") -> None:
    """S16 #610: wire (or clear) the dedicated-path failure notification seam.

    Called when a session-2 worker dispatch fails (connect error, session
    death). Arguments forwarded to fn: (mode, reason, fallback). main.py
    wires this to _notify_user so the Visualiser / OpenClaw push fires on
    failure -- the user is never left in a silent-failure state. Pass None
    to clear (e.g. at teardown)."""
    global _failure_notify_fn
    _failure_notify_fn = fn


def select_actuation_tier(
    *,
    needs_foreground: bool = False,
    live_desktop_only: bool = False,
) -> "Literal['background', 'isolated_session', 'take_turns']":
    """S16 #610: three-tier planner-facing mode ladder (ADR-0016 point 9).

    Returns the recommended actuation tier for a computer-use task:
      "background"        -- UIA control patterns, no cursor, concurrent
                            with the user. Cheapest; try first for structured
                            targets with a usable control pattern.
      "isolated_session"  -- Felix's dedicated session (#603-#609). Default
                            when foreground or pixel input-stealing paths are
                            needed, or for bulk autonomous work.
      "take_turns"        -- Live-desktop foreground. Opt-in when the target
                            exists ONLY in the user's own session (something
                            the user has open that Felix has no separate
                            login for) -- the existing idle-gated path.

    Planner picks per task, same pattern as select_web_path (ADR-0016 sec 2)
    and the two Discord paths (ADR-0006). Not a hardware call -- pure logic,
    safe to call from any context."""
    if live_desktop_only:
        return "take_turns"
    if needs_foreground:
        return "isolated_session"
    return "background"


def pause_current() -> None:
    """S15 #609: soft-pause the observe-act loop -- the ``Take over``
    reversible sibling of abort_current(). Blocks input-emitting primitives
    (click / type / press_key) until resume_current() is called; read_ui /
    capture keep flowing so the thumbnail stream doesn't stall. Reuses the
    same IPC/seam pipe as the kill switch (per #609 acceptance)."""
    if _plugin_instance is not None:
        _plugin_instance.pause()


def resume_current() -> None:
    """S15 #609: release the ``Take over`` pause. Idempotent."""
    if _plugin_instance is not None:
        _plugin_instance.resume()


@runtime_checkable
class ComputerUseBackend(Protocol):
    """Platform seam. Windows default + testable fake both fit this shape."""

    def read_ui(self, window_title: str) -> list[dict]:
        """Return the target window's UIA elements as
        [{"name": str, "role": str, "bbox": [l, t, r, b]}, ...]."""

    def click(self, bbox: list[int]) -> None:
        """Actuate a left-click at the centre of ``bbox``.

        On Windows, must translate ``pyautogui.FailSafeException`` into the
        plugin's ``CornerAbort`` so the loop can treat corner-slam as a
        first-class kill-switch event without importing pyautogui here."""

    def type_text(self, text: str) -> None:
        """Actuate a keyboard type of ``text`` at the current focus."""

    def capture_frame(self, window_title: str) -> Optional[bytes]:
        """Return window pixel bytes (RAM only, never persisted). May be None."""

    def window_bounds(self, window_title: str) -> Optional[list[int]]:
        """Return the target window's client-rect ``[l, t, r, b]`` in screen
        coordinates, or None when the window is missing / bounds unavailable.
        Used as the soft window-bounded-region check for every actuation."""

    def surface_window(self, window_title: str) -> None:
        """S6 #579: bring the target window to the foreground so the human can
        take over. Best-effort -- failures must not break the handoff."""

    def press_key(self, key: str) -> None:
        """S7 #580: press a named key (e.g. ``"enter"``). Optional -- backends
        without it disable the browser-as-app path's URL-submit step; the
        plugin falls back to appending ``\\n`` via ``type_text`` when absent.
        On Windows, must translate ``pyautogui.FailSafeException`` into
        ``CornerAbort`` (same rule as the other actuation methods)."""

    def resolve_element(
        self, window_title: str, name: str, role: str | None,
    ) -> tuple[object, list[int]] | None:
        """#592: re-walk the LIVE UIA tree just-in-time and return
        ``(live_element, bbox)`` for the first name/role match (same
        first-match semantics as ``_find_element`` / ``read_ui``), or ``None``
        when absent. ``live_element`` is opaque to the plugin -- only
        ``pattern_click`` / ``pattern_set_value`` inspect it, and it must
        never be held across an ``await`` (resolve fresh on every try).
        Optional: a backend without this method disables background
        actuation entirely and the plugin falls straight to the existing
        foreground path."""

    def pattern_click(self, element: object) -> bool:
        """#592: attempt a background UIA control-pattern click on the live
        ``element`` -- ``InvokePattern.Invoke()``, then ``TogglePattern``,
        then ``SelectionItemPattern.Select()``, then
        ``ExpandCollapsePattern`` -- no cursor movement, no synthetic click.
        Returns True when a pattern fired; False when the element exposes
        none of them, so the caller falls back to a foreground bbox click on
        the same resolved element."""

    def pattern_set_value(self, element: object, text: str) -> bool:
        """#592: attempt ``ValuePattern.SetValue(text)`` on the live
        ``element`` -- atomic fill, no keystrokes, never a submit. Returns
        False (never raises) when the pattern is unavailable or the control
        reports read-only, so the caller falls back to foreground click+type."""

    def window_class(self, window_title: str) -> Optional[str]:
        """#592: the target window's native class name (e.g.
        ``"Chrome_WidgetWin_1"``), or ``None`` when unavailable. Used to keep
        SetValue out of any browser/Electron (webview) surface regardless of
        the control's own role (ADR-0016 amendment c)."""

    def last_input_ms(self) -> int:
        """#593: milliseconds since the user's last physical input
        (``GetLastInputInfo``). The idle gate for the foreground fallback --
        "what I'm doing takes priority" (ADR-0016 amendment d). Optional: a
        backend without this method disables the idle gate (foreground
        proceeds unconditionally, matching pre-#593 behaviour)."""

    def foreground_window(self) -> Optional[str]:
        """#593: the current OS foreground window's title
        (``GetForegroundWindow`` + ``GetWindowText``), used as the
        focus-theft probe -- captured before/after every actuation to stamp
        ``foregrounded`` in the trace (ADR-0016 amendment d/g). Optional: a
        backend without this method always stamps ``foregrounded: False``."""


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


def _count_matches(elements: list[dict], name: str, role: str | None) -> int:
    """Count of name/role matches -- same predicate as ``_find_element``, used
    to flag a non-unique target in the trace instead of silently guessing
    (ADR-0016 amendment b). First-match semantics are unchanged; this only
    adds a ``multi_match`` note when more than one element qualifies."""
    n = (name or "").strip().lower()
    r = (role or "").strip().lower() or None
    count = 0
    for e in elements:
        if (e.get("name") or "").strip().lower() != n:
            continue
        if r is not None and (e.get("role") or "").strip().lower() != r:
            continue
        count += 1
    return count


# ADR-0016 amendment (c): SetValue must never fire inside a Chromium/Gecko
# render surface -- a normal browser OR an Electron app, which embeds the
# same Chromium shell and is indistinguishable from a browser by window
# class. Matched by substring on the native window class so locale/version
# don't matter. ponytail: class-name allowlist is a real UIA signal, not a
# guess by app name; upgrade to process-name introspection only if a
# Chromium-classed native app turns up that legitimately needs SetValue.
_WEBVIEW_WINDOW_CLASSES: tuple[str, ...] = ("chrome_widgetwin", "mozillawindowclass")


def _is_webview_class(window_class: str | None) -> bool:
    """True when ``window_class`` names a browser/Electron render surface, or
    when it's unknown. Unknown means doubt, and ADR-0016 amendment (c) is
    explicit: any doubt falls to foreground typing, never SetValue."""
    if not window_class:
        return True
    c = window_class.strip().lower()
    return any(w in c for w in _WEBVIEW_WINDOW_CLASSES)


def _bbox_center(bbox: list[int]) -> tuple[int, int]:
    l, t, r, b = bbox
    return ((l + r) // 2, (t + b) // 2)


def _bbox_within(inner: list[int], outer: list[int]) -> bool:
    """True when ``inner`` bbox sits fully inside ``outer``. Both are
    ``[left, top, right, bottom]``; equal edges count as inside."""
    if len(inner) != 4 or len(outer) != 4:
        return False
    il, it, ir, ib = inner
    ol, ot, orr, ob = outer
    return il >= ol and it >= ot and ir <= orr and ib <= ob


def _is_black_frame(frame: bytes | None) -> bool:
    """S5 #578: DRM/GPU-protected windows capture as a fully-black raster
    (Netflix, some anti-cheat games, protected video). Detect the two ways
    this shows up so the pixel-vision fallback escalates instead of clicking
    blind at whatever coordinate a confused VL model returns for a black
    frame: (a) capture backend returned None / empty bytes, or (b) every
    byte is zero. ``any(bytes)`` short-circuits on the first non-zero byte
    so this is O(1) on a normal (non-DRM) frame."""
    if not frame:
        return True
    return not any(frame)


# S7 #580: browser-as-app stealth path. UIA names Chrome / Edge / Firefox use
# for the address bar. Substring match, first hit wins -- keeps the LLM from
# having to know the browser's exact locale/version string.
_ADDRESS_BAR_CANDIDATES: tuple[str, ...] = (
    "Address and search bar",   # Chrome, Edge (English)
    "Search or enter address",  # Firefox (English)
    "Search Google or type a URL",
    "Search or type URL",
    "URL bar",
    "Location",                 # older Firefox
)

# S7 #580: routing heuristic (stealth-sensitive vs benign). Hosts here are the
# places a CDP / navigator.webdriver fingerprint gets a session flagged or
# banned -- planner should route them through computer_use (OS-level input)
# rather than the Browser (Playwright) plugin. Discord is the motivating case
# in ADR-0016; the rest are common bot-detection targets (Cloudflare Turnstile,
# major socials). Match is exact-or-subdomain, so ``www.discord.com`` counts
# just like ``discord.com``.
_STEALTH_HOSTS: frozenset[str] = frozenset({
    "discord.com",
    "discord.gg",
    "discordapp.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "cloudflare.com",
    "challenges.cloudflare.com",
})

_URL_RE = re.compile(r"https?://[^\s'\"<>()\[\]]+", re.IGNORECASE)


def _extract_host(url_or_host: str) -> str | None:
    """Return the hostname of a URL or the bare token itself, lowercased."""
    if not url_or_host:
        return None
    s = url_or_host.strip().lower()
    if "://" in s:
        try:
            return (urlparse(s).hostname or "").lower() or None
        except Exception:
            return None
    return s.split("/", 1)[0] or None


def stealth_sensitive(url_or_text: str) -> bool:
    """True when the input names a stealth-sensitive host -- one where OS-level
    input beats Playwright/CDP because the site fingerprints automated clients
    (ADR-0016). Accepts a URL, a bare hostname, or free text containing one.
    Unknown hosts are benign by default (fast path stays default)."""
    if not url_or_text:
        return False
    text = url_or_text.strip()
    urls = _URL_RE.findall(text)
    if urls:
        candidates = urls
    else:
        # Try the whole thing as a bare host (no scheme).
        candidates = [text.split()[-1]] if text else []
    for cand in candidates:
        host = _extract_host(cand)
        if not host:
            continue
        for known in _STEALTH_HOSTS:
            if host == known or host.endswith("." + known):
                return True
    return False


def select_web_path(url_or_text: str) -> Literal["computer_use", "browser"]:
    """Pick which plugin family should handle a web target: ``computer_use``
    (OS input, no CDP fingerprint) for stealth-sensitive sites, ``browser``
    (Playwright DOM, faster) for the benign default. The two plugins coexist
    permanently -- ADR-0016 sec 2."""
    return "computer_use" if stealth_sensitive(url_or_text) else "browser"


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
        *,
        driving_fn: DrivingFn | None = None,
        hotkey_register_fn: HotkeyRegisterFn | None | object = _UNSET,
        vision_ground_fn: VisionGroundFn | None = None,
        attended_handoff_fn: AttendedHandoffFn | None = None,
        thumbnail_ring_size: int = DEFAULT_THUMBNAIL_RING_SIZE,
        background_actuation_fn: BackgroundActuationFn | None = None,
        setvalue_roles_fn: SetValueRolesFn | None = None,
        user_idle_ms_fn: UserIdleMsFn | None = None,
        full_autonomy_fn: FullAutonomyFn | None = None,
        session_dispatch_fn: "SessionDispatchFn | None" = None,
        failure_notify_fn: "FailureNotifyFn | None" = None,
    ) -> None:
        if backend is _UNSET:
            self._backend = _make_default_backend()
        else:
            self._backend = backend  # explicit -- including None -> fail-closed
        self._record_trace_fn = record_trace_fn
        # Constructor-injected seams win; module-level fallbacks are wired by
        # main.py at startup. driving_fn broadcasts the ADR-0016 (c) "Felix is
        # driving" state; hotkey_register_fn wires the ADR-0016 (b) F11+F12
        # chord -- both may be None (kill-switch legs are best-effort so an
        # unwired host is not silently disabled from computer_use entirely).
        self._driving_fn: DrivingFn | None = driving_fn or _driving_fn
        # S5 #578: pixel-vision grounding + RAM-only thumbnail ring. Fallback
        # is opt-in via the seam: unwired = structured-only (safe default and
        # what pre-S5 callers expect). Ring lives in process memory; a
        # restart clears it -- audio-buffer rule (ADR-0016 sec 7).
        self._vision_ground_fn: VisionGroundFn | None = vision_ground_fn or _vision_ground_fn
        # S6 #579: attended-handoff seam. Unwired = no handoff (fail as before).
        self._attended_handoff_fn: AttendedHandoffFn | None = (
            attended_handoff_fn or _attended_handoff_fn
        )
        # #592 (ADR-0016 amendment): felix-settings.json getters. Unwired =
        # DEFAULT_* constants (matches cerebral/settings.py's shipped
        # defaults) -- background-first is the safe-and-usable default.
        self._background_actuation_fn: BackgroundActuationFn | None = (
            background_actuation_fn or _background_actuation_fn
        )
        self._setvalue_roles_fn: SetValueRolesFn | None = (
            setvalue_roles_fn or _setvalue_roles_fn
        )
        # #593 (ADR-0016 amendment d): idle gate + full-autonomy bypass
        # getters. Unwired = DEFAULT_* constants (idle gate active at the
        # documented default; full-autonomy off).
        self._user_idle_ms_fn: UserIdleMsFn | None = (
            user_idle_ms_fn or _user_idle_ms_fn
        )
        self._full_autonomy_fn: FullAutonomyFn | None = (
            full_autonomy_fn or _full_autonomy_fn
        )
        # S11 #605: isolated-session dispatch seam. Constructor arg wins over
        # the module-level global wired by main.py when a worker connects.
        self._session_dispatch_fn: SessionDispatchFn | None = (
            session_dispatch_fn or _session_dispatch_fn
        )
        # S16 #610: dedicated-path failure notification seam. Unwired = silent
        # on worker errors (pre-S16 behaviour -- the trace records the error).
        self._failure_notify_fn: FailureNotifyFn | None = (
            failure_notify_fn or _failure_notify_fn
        )
        self._thumbnail_ring: deque[bytes] = deque(maxlen=max(1, int(thumbnail_ring_size)))
        # asyncio.Event carries the abort signal across the (a) corner-failsafe,
        # (b) F11+F12 chord, and (c) Visualiser Stop legs. Created here so it
        # is safe to inspect before any tool call fires.
        self._abort_event = asyncio.Event()
        # S15 #609: "Take over" pause. asyncio.Event flipped by pause()/resume():
        # SET = worker allowed to act (default), CLEAR = paused. Input-emitting
        # primitives (click/type/press_key) await this before dispatch so the
        # user-owned RDP window doesn't contend with worker input on session 2.
        # read_ui and capture keep flowing -- the thumbnail stream is passive
        # and the AC forbids INPUT contention, not observation.
        self._can_actuate = asyncio.Event()
        self._can_actuate.set()
        # F11+F12 registration -- once per plugin instance. Tests pass a fake
        # that captures the callback; production leaves this alone so the
        # module-level seam wired by main.py drives it.
        reg = hotkey_register_fn if hotkey_register_fn is not _UNSET else _hotkey_register_fn
        if reg is not None:
            try:
                # S12 #606: pass abort_current (not self.abort) so F11+F12 also
                # triggers _terminate_worker_fn when an in-session worker is live.
                # abort_current() calls _plugin_instance.abort() internally, so
                # existing tests that check _abort_event is set still pass.
                reg(abort_current)
            except Exception:
                logger.warning(
                    "[computer_use] hotkey_register_fn failed; "
                    "F11+F12 kill-switch leg disabled", exc_info=True,
                )
        # Register the module-level singleton so main.py's IPC handler can
        # reach abort() via ``abort_current()`` on a Visualiser Stop message.
        global _plugin_instance
        _plugin_instance = self

    def thumbnail_ring_snapshot(self) -> list[bytes]:
        """S5 #578: peek at the RAM-only frame ring (in-session debug). The
        ring is process-memory-only; a Cerebral restart clears it."""
        return list(self._thumbnail_ring)

    def abort(self) -> None:
        """Signal the observe-act loop to short-circuit before its next try.

        Called from the F11+F12 hotkey callback, the module-level
        ``abort_current()`` (Visualiser Stop IPC), or a caller that owns the
        plugin. Idempotent -- setting an already-set Event is a no-op."""
        self._abort_event.set()

    def pause(self) -> None:
        """S15 #609: soft-pause worker input dispatch (take-over). Idempotent."""
        self._can_actuate.clear()

    def resume(self) -> None:
        """S15 #609: release the take-over pause. Idempotent."""
        self._can_actuate.set()

    @property
    def is_paused(self) -> bool:
        """S15 #609: True while a take-over pause is in effect."""
        return not self._can_actuate.is_set()

    def _in_isolated_session(self) -> bool:
        """S16 #610: True when the session-dispatch seam is wired, i.e. the
        plugin is routing its primitives to a session-2 worker. Used to apply
        the relaxed-rules posture: no window-bounded check, no idle gate."""
        return self._session_dispatch_fn is not None

    async def _notify_dedicated_path_failure(
        self, mode: str, reason: str, fallback: str,
    ) -> None:
        """S16 #610: fire the failure-notification seam when a dedicated-path
        (worker) dispatch raises. Best-effort: a broken sink never propagates.
        Reads the instance seam first (constructor-injected), then the module
        global wired by main.py (same lookup pattern as _driving_fn)."""
        fn = self._failure_notify_fn or _failure_notify_fn
        if fn is None:
            return
        try:
            await fn(mode, reason, fallback)
        except Exception:
            logger.warning("[computer_use] failure_notify_fn raised", exc_info=True)

    async def _emit_driving(
        self,
        driving: bool,
        *,
        mode: str = "foreground",
        window_title: str = "",
        action: str = "",
    ) -> None:
        """Best-effort broadcast of the "Felix is driving" indicator. A broken
        sink must never break a computer_use call in progress.

        #594: the payload carries ``mode`` ("background" while a UIA control
        pattern is driving with no cursor, "foreground" once a pyautogui
        fallback is about to touch the mouse/keyboard or a soft trip has just
        flipped it) plus ``window_title``/``action`` so the Visualiser can
        render "Felix is acting in <window> (background)" vs the existing
        cursor-in-use urgency, driven entirely by this one broadcast."""
        if self._driving_fn is None:
            return
        payload = {
            "driving": driving,
            "mode": mode,
            "window_title": window_title,
            "action": action,
        }
        try:
            await self._driving_fn(payload)
        except Exception:
            logger.warning("[computer_use] driving_fn broadcast failed", exc_info=True)

    def _driving_mode(self) -> str:
        """Initial mode guess for a click/type call, before any try has run:
        "background" when background actuation is enabled (a control pattern
        is attempted first), else "foreground". Call sites flip this to
        "foreground" in real time the moment a try actually falls through to
        the pyautogui fallback or soft-trips (ADR-0016 amendment f)."""
        return "background" if self._background_actuation_enabled() else "foreground"

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
                name="browser_navigate",
                description=(
                    "Navigate a NORMAL, user-launched browser window to a URL "
                    "via OS-level input (address-bar type + Enter) -- the "
                    "stealth path with no Playwright/CDP fingerprint. Reads "
                    "the resulting page's UIA tree back. For benign public "
                    "reads (no login, no detection risk), the Browser plugin "
                    "(navigate / web_search / read_pdf) is faster."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "window_title": {
                            "type": "string",
                            "description": "Title (substring) of the open browser window.",
                        },
                        "url": {
                            "type": "string",
                            "description": "URL to navigate to.",
                        },
                        "address_bar": {
                            "type": "string",
                            "description": (
                                "Optional UIA name for the address bar element. "
                                "Defaults to common Chrome/Edge/Firefox names."
                            ),
                        },
                        "retries": {
                            "type": "integer",
                            "description": "Max failed tries (1-5, default 3).",
                        },
                    },
                    "required": ["window_title", "url"],
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
            return await self._read_ui(args)
        if tool_name == "click_element":
            return await self._click_element(args)
        if tool_name == "type_into":
            return await self._type_into(args)
        if tool_name == "browser_navigate":
            return await self._browser_navigate(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _window_bounds(self, window_title: str) -> list[int] | None:
        """Bounds via the backend if it exposes them; ``None`` disables the
        bounded-region check for that backend (backwards-compatible)."""
        fn = getattr(self._backend, "window_bounds", None)
        if fn is None:
            return None
        try:
            wb = fn(window_title)
        except Exception:
            return None
        return wb if isinstance(wb, list) and len(wb) == 4 else None

    # ── #592 background actuation (ADR-0016 amendment) ─────────────────────

    def _background_actuation_enabled(self) -> bool:
        fn = self._background_actuation_fn
        if fn is None:
            return DEFAULT_BACKGROUND_ACTUATION
        try:
            return bool(fn())
        except Exception:
            logger.warning(
                "[computer_use] background_actuation getter failed; "
                "defaulting to background-first", exc_info=True,
            )
            return DEFAULT_BACKGROUND_ACTUATION

    def _setvalue_roles(self) -> list[str]:
        fn = self._setvalue_roles_fn
        if fn is None:
            return list(DEFAULT_SETVALUE_ROLES)
        try:
            roles = fn()
        except Exception:
            logger.warning(
                "[computer_use] setvalue_roles getter failed; "
                "defaulting to %r", DEFAULT_SETVALUE_ROLES, exc_info=True,
            )
            return list(DEFAULT_SETVALUE_ROLES)
        if not isinstance(roles, list):
            return list(DEFAULT_SETVALUE_ROLES)
        return [str(r) for r in roles]

    def _is_webview_window(self, window_title: str) -> bool:
        """Any doubt (no backend support, lookup error, unknown class) counts
        as a webview -- forces foreground typing, never SetValue."""
        fn = getattr(self._backend, "window_class", None)
        if fn is None:
            return True
        try:
            return _is_webview_class(fn(window_title))
        except Exception:
            return True

    # ── #593 idle gate + focus-theft probe (ADR-0016 amendment d/g) ────────

    def _user_idle_ms_threshold(self) -> int:
        fn = self._user_idle_ms_fn
        if fn is None:
            return DEFAULT_USER_IDLE_MS
        try:
            return int(fn())
        except Exception:
            logger.warning(
                "[computer_use] user_idle_ms getter failed; defaulting to "
                "%dms", DEFAULT_USER_IDLE_MS, exc_info=True,
            )
            return DEFAULT_USER_IDLE_MS

    def _full_autonomy_enabled(self) -> bool:
        fn = self._full_autonomy_fn
        if fn is None:
            return DEFAULT_FULL_AUTONOMY
        try:
            return bool(fn())
        except Exception:
            logger.warning(
                "[computer_use] full_autonomy getter failed; defaulting to "
                "off", exc_info=True,
            )
            return DEFAULT_FULL_AUTONOMY

    def _last_input_ms(self) -> int | None:
        """None when the backend doesn't expose the probe -- callers treat
        that as "can't tell, don't block" (matches pre-#593 behaviour)."""
        fn = getattr(self._backend, "last_input_ms", None)
        if fn is None:
            return None
        try:
            return int(fn())
        except Exception:
            return None

    def _foreground_window_safe(self) -> str | None:
        fn = getattr(self._backend, "foreground_window", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    @staticmethod
    def _is_target_foreground(fg_title: str | None, window_title: str) -> bool:
        """Substring match, same convention as UIA window lookup elsewhere
        in this plugin (``WindowControl(SubName=window_title)``)."""
        if not fg_title or not window_title:
            return False
        return window_title.strip().lower() in fg_title.strip().lower()

    def _foregrounded(
        self, fg_before: str | None, fg_after: str | None, window_title: str,
    ) -> bool:
        """True when the target window BECAME the foreground window as a
        result of this actuation -- wasn't foreground before, is now. A
        backend without ``foreground_window`` always yields False on both
        sides, so this degrades to "unknown -> False" (pre-#593 behaviour)."""
        return (
            not self._is_target_foreground(fg_before, window_title)
            and self._is_target_foreground(fg_after, window_title)
        )

    def _idle_allows_foreground(self) -> tuple[bool, int | None, int]:
        """Core idle-gate check, path-label-agnostic. Returns
        ``(allowed, idle_ms, threshold)``. Full-autonomy bypasses the gate
        (mirrors sec 4's irreversible-floor bypass); an unknown idle time
        (backend doesn't expose the probe) also allows -- "can't tell,
        don't block" matches pre-#593 behaviour."""
        threshold = self._user_idle_ms_threshold()
        if self._full_autonomy_enabled():
            return True, None, threshold
        idle_ms = self._last_input_ms()
        return (idle_ms is None or idle_ms >= threshold), idle_ms, threshold

    def _foreground_gate(
        self, window_title: str, name: str, n: int, trace: dict,
        *, path: str = "uia_synthetic",
    ) -> bool:
        """ADR-0016 amendment (d): "what I'm doing takes priority". Called
        immediately before any pyautogui foreground action. True -> caller
        may proceed. False -> the user is present (idle time under the
        threshold) and full-autonomy is off; this try is recorded as
        waiting and the caller must ``continue``/return-False -- the
        existing observe-act-verify retry loop IS the wait, and its normal
        exhaustion already escalates to attended-handoff (S6), so no new
        escalation path is needed."""
        allowed, idle_ms, threshold = self._idle_allows_foreground()
        if allowed:
            return True
        trace["tries"].append({
            "n": n, "observed": True, "acted": False,
            "expected": f"foreground action for {name!r} in {window_title!r}",
            "actual": (
                f"user present (idle {idle_ms}ms < {threshold}ms) -- "
                "waiting for idle instead of stealing input"
            ),
            "ok": False, "path": path,
        })
        return False

    def _try_pattern_click(
        self, window_title: str, name: str, role: str | None,
    ) -> tuple[bool, list[int] | None]:
        """Resolve the live element fresh and attempt a background UIA
        pattern click. Returns ``(fired, live_bbox)`` -- ``live_bbox`` is the
        SAME re-resolved element's bbox, handed back so the caller's
        foreground fallback (when ``fired`` is False) targets the identical
        element rather than the possibly-stale bbox from the earlier
        ``read_ui`` snapshot. Never raises -- any backend error is "no usable
        pattern", which is exactly the documented fallback trigger."""
        resolve_fn = getattr(self._backend, "resolve_element", None)
        pattern_fn = getattr(self._backend, "pattern_click", None)
        if resolve_fn is None or pattern_fn is None:
            return False, None
        try:
            resolved = resolve_fn(window_title, name, role)
        except Exception:
            return False, None
        if resolved is None:
            return False, None
        live_element, live_bbox = resolved
        bbox = live_bbox if isinstance(live_bbox, list) and len(live_bbox) == 4 else None
        try:
            fired = bool(pattern_fn(live_element))
        except Exception:
            fired = False
        return fired, bbox

    def _try_pattern_set_value(
        self, window_title: str, name: str, role: str | None, text: str,
    ) -> tuple[bool, list[int] | None]:
        """Same shape as ``_try_pattern_click`` for the ``type_into`` path,
        gated by the SetValue role allowlist + the webview-surface check
        (ADR-0016 amendment c) BEFORE the backend is even asked -- the gate
        is by control class + surface, never by guessing reactivity."""
        role_name = (role or "").strip().lower()
        allowed = {r.strip().lower() for r in self._setvalue_roles()}
        if not allowed or role_name not in allowed:
            return False, None
        if self._is_webview_window(window_title):
            return False, None
        resolve_fn = getattr(self._backend, "resolve_element", None)
        setval_fn = getattr(self._backend, "pattern_set_value", None)
        if resolve_fn is None or setval_fn is None:
            return False, None
        try:
            resolved = resolve_fn(window_title, name, role)
        except Exception:
            return False, None
        if resolved is None:
            return False, None
        live_element, live_bbox = resolved
        bbox = live_bbox if isinstance(live_bbox, list) and len(live_bbox) == 4 else None
        try:
            filled = bool(setval_fn(live_element, text))
        except Exception:
            filled = False
        return filled, bbox

    async def _pixel_fallback_click(
        self, window_title: str, name: str, trace: dict,
    ) -> bool:
        """S5 #578: pixel-vision fallback after structured UIA exhausted.

        Runs at most one extra try: capture window frame -> escalate on
        DRM/black-capture -> ground via ``vision_ground_fn`` -> refuse a
        coord outside the window bounds -> actuate. Records each outcome as
        a ``path="pixel"`` try so the trace tells structured-vs-pixel apart.
        Opt-in: an unwired grounding seam (or a backend with no ``capture_frame``)
        makes this a no-op so pre-S5 callers keep structured-only behaviour."""
        if self._vision_ground_fn is None:
            return False
        # Capture-not-supported short-circuit: applies to both the local
        # backend (no capture_frame attr) and the worker path (S11 backend
        # without capture_frame). In the worker path we won't know until we
        # dispatch, so let _prim_capture handle it and treat a raised error
        # or None frame as "no capture" below.
        if (self._session_dispatch_fn is None
                and getattr(self._backend, "capture_frame", None) is None):
            return False
        n = len(trace["tries"]) + 1
        if self._abort_event.is_set():
            trace["tries"].append(
                {"n": n, "observed": False, "acted": False,
                 "expected": f"pixel fallback for {name!r}",
                 "actual": "aborted by kill switch",
                 "ok": False, "path": "pixel"}
            )
            return False
        try:
            # S15 #609: routed capture -- ring append + thumbnail emit happen
            # inside _prim_capture (isolated session or local backend, same
            # code path). Pre-S15 pixel fallback appended to the ring inline;
            # that's now factored into _prim_capture.
            frame = await self._prim_capture(window_title)
        except Exception as exc:
            trace["tries"].append(
                {"n": n, "observed": False, "acted": False,
                 "expected": "window frame bytes",
                 "actual": f"capture error: {exc}",
                 "ok": False, "path": "pixel"}
            )
            return False
        if _is_black_frame(frame):
            trace["tries"].append(
                {"n": n, "observed": False, "acted": False,
                 "expected": "non-black window frame",
                 "actual": "black/protected capture -- escalating",
                 "ok": False, "path": "pixel", "escalated": True}
            )
            return False
        try:
            coord = await self._vision_ground_fn(name, frame)
        except Exception as exc:
            trace["tries"].append(
                {"n": n, "observed": True, "acted": False,
                 "expected": f"grounded coords for {name!r}",
                 "actual": f"grounding error: {exc}",
                 "ok": False, "path": "pixel"}
            )
            return False
        if coord is None or not (isinstance(coord, (tuple, list)) and len(coord) == 2):
            trace["tries"].append(
                {"n": n, "observed": True, "acted": False,
                 "expected": f"grounded coords for {name!r}",
                 "actual": f"grounding returned {coord!r}",
                 "ok": False, "path": "pixel"}
            )
            return False
        try:
            x, y = int(coord[0]), int(coord[1])
        except (TypeError, ValueError):
            trace["tries"].append(
                {"n": n, "observed": True, "acted": False,
                 "expected": f"grounded coords for {name!r}",
                 "actual": f"non-integer coords {coord!r}",
                 "ok": False, "path": "pixel"}
            )
            return False
        wb = self._window_bounds(window_title)
        if wb is not None and not (wb[0] <= x <= wb[2] and wb[1] <= y <= wb[3]):
            trace["tries"].append(
                {"n": n, "observed": True, "acted": False,
                 "expected": f"({x},{y}) inside window {wb}",
                 "actual": "outside window bounds -- refused",
                 "ok": False, "path": "pixel"}
            )
            return False
        # #593: pixel actuation is always foreground (a coordinate has no
        # control-pattern handle) -- gate on user idle same as the
        # structured foreground fallback, before touching the mouse.
        if not self._foreground_gate(window_title, name, n, trace, path="pixel"):
            return False
        fg_before = self._foreground_window_safe()
        try:
            click_at = getattr(self._backend, "click_at", None)
            if click_at is not None:
                click_at(x, y)
            else:
                # Backwards-compatible: reuse the bbox click seam by passing
                # a 1x1 rect at the target coord.
                self._backend.click([x, y, x, y])  # type: ignore[union-attr]
        except CornerAbort:
            self._abort_event.set()
            trace["tries"].append(
                {"n": n, "observed": True, "acted": False,
                 "expected": f"pixel click at ({x},{y})",
                 "actual": "corner-failsafe abort",
                 "ok": False, "path": "pixel"}
            )
            return False
        except Exception as exc:
            trace["tries"].append(
                {"n": n, "observed": True, "acted": False,
                 "expected": f"pixel click at ({x},{y})",
                 "actual": f"error: {exc}",
                 "ok": False, "path": "pixel"}
            )
            return False
        fg_after = self._foreground_window_safe()
        trace["target"] = {"name": name, "role": None, "bbox": [x, y, x, y]}
        trace["tries"].append(
            {"n": n, "observed": True, "acted": True,
             "expected": f"pixel click at ({x},{y})",
             "actual": "clicked", "ok": True, "path": "pixel",
             "foregrounded": self._foregrounded(fg_before, fg_after, window_title)}
        )
        trace["ok"] = True
        return True

    async def _escalate_to_handoff(
        self, window_title: str, name: str, reason: str, trace: dict,
    ) -> bool:
        """S6 #579: attended-handoff on retry exhaustion / DRM-black. Surface
        the target window, invoke the handoff seam, and record the outcome as
        a ``path="handoff"`` try so the trace tells structured / pixel /
        handoff apart. Returns True when the human completed the step (caller
        flips ``trace.ok``); False otherwise (seam unwired, declined, or
        raised). Never fires when the loop was killed by the (a)/(b)/(c) kill
        switch -- the calling loop returns early before reaching this."""
        handoff_fn = self._attended_handoff_fn
        if handoff_fn is None:
            return False
        surface = getattr(self._backend, "surface_window", None)
        if surface is not None:
            try:
                surface(window_title)
            except Exception:
                logger.warning(
                    "[computer_use] surface_window failed", exc_info=True,
                )
        # Handing over to the human -- flip driving off so any UI shows the
        # correct "Felix paused" state. The tool's outer finally emits a
        # second False on return; harmless.
        await self._emit_driving(False)
        n = len(trace["tries"]) + 1
        try:
            completed = bool(await handoff_fn(window_title, reason))
        except Exception as exc:
            trace["tries"].append(
                {"n": n, "observed": False, "acted": False,
                 "expected": f"attended handoff for {name!r}",
                 "actual": f"handoff seam raised: {exc}",
                 "ok": False, "path": "handoff"}
            )
            return False
        if completed:
            trace["tries"].append(
                {"n": n, "observed": True, "acted": True,
                 "expected": f"human completes {name!r}",
                 "actual": "handoff completed by human",
                 "ok": True, "path": "handoff"}
            )
            return True
        trace["tries"].append(
            {"n": n, "observed": True, "acted": False,
             "expected": f"human completes {name!r}",
             "actual": "handoff declined by human",
             "ok": False, "path": "handoff"}
        )
        return False

    def _finish(self, trace: dict) -> ToolResult:
        if self._record_trace_fn is not None:
            try:
                self._record_trace_fn(trace)
            except Exception:
                # Trace persistence is best-effort; a failing sink must not
                # break the tool call itself.
                pass
        return ToolResult(content=json.dumps(trace), is_error=not trace.get("ok", False))

    # S11 #605: async primitive helpers. When _session_dispatch_fn is set they
    # route to the in-session worker over WS; otherwise call the local backend
    # synchronously (same behavior as before). The 3 core tool methods become
    # async to accommodate the await in both paths.

    async def _prim_read_ui(self, window_title: str) -> list[dict]:
        fn = self._session_dispatch_fn
        if fn is not None:
            try:
                r = await fn("read_ui", {"window_title": window_title})
                return r.get("elements", [])
            except Exception as exc:
                # S16 #610: never-silent failure -- notify then fall back to
                # the local backend (take_turns tier: session-1 read_ui, no
                # input theft, always safe).
                await self._notify_dedicated_path_failure(
                    "isolated_session", str(exc), "take_turns",
                )
        return self._backend.read_ui(window_title)  # type: ignore[union-attr]

    async def _prim_click(self, bbox: list[int]) -> None:
        # S15 #609: soft-pause gate. Blocks INPUT dispatch during take-over so
        # the user-owned RDP window has session 2's cursor to itself.
        await self._can_actuate.wait()
        fn = self._session_dispatch_fn
        if fn is not None:
            try:
                await fn("click", {"bbox": bbox})
                return
            except Exception as exc:
                # S16 #610: notify on worker failure, then fall back to local
                # pyautogui (take_turns tier -- user-session foreground click).
                await self._notify_dedicated_path_failure(
                    "isolated_session", str(exc), "take_turns",
                )
        self._backend.click(bbox)  # type: ignore[union-attr]

    async def _prim_type(self, text: str) -> None:
        await self._can_actuate.wait()  # S15 #609: take-over gate
        fn = self._session_dispatch_fn
        if fn is not None:
            try:
                await fn("type", {"text": text})
                return
            except Exception as exc:
                # S16 #610: notify on worker failure, fall back to local typing.
                await self._notify_dedicated_path_failure(
                    "isolated_session", str(exc), "take_turns",
                )
        self._backend.type_text(text)  # type: ignore[union-attr]

    async def _prim_capture(self, window_title: str) -> bytes | None:
        """S15 #609: capture a window frame, park it in the RAM thumbnail ring,
        and (best-effort) emit it to the passive thumbnail stream. Routes to
        the in-session worker when isolated-session mode is on; otherwise the
        local backend. Returns None when capture isn't supported."""
        fn = self._session_dispatch_fn
        frame: bytes | None
        if fn is not None:
            try:
                r = await fn("capture", {"window_title": window_title})
                b64 = r.get("frame_b64")
                if b64 is None:
                    frame = None
                else:
                    import base64 as _b64
                    frame = _b64.b64decode(b64)
            except Exception as exc:
                # S16 #610: notify on worker capture failure; fall back to
                # local backend capture (which returns None when unsupported).
                await self._notify_dedicated_path_failure(
                    "isolated_session", str(exc), "take_turns",
                )
                capture_fn = getattr(self._backend, "capture_frame", None)
                return capture_fn(window_title) if capture_fn is not None else None
        else:
            capture_fn = getattr(self._backend, "capture_frame", None)
            frame = capture_fn(window_title) if capture_fn is not None else None
        # Skip ring + emit on missing/black frames -- ADR-0016 sec 7 (only
        # useful pixels in the debug buffer), and DRM-black is a
        # pixel-fallback escalate signal, not a thumbnail.
        if frame is not None and not _is_black_frame(frame):
            self._thumbnail_ring.append(bytes(frame))
            if _thumbnail_emit_fn is not None:
                try:
                    await _thumbnail_emit_fn(bytes(frame))
                except Exception:
                    logger.warning("[computer_use] thumbnail emit failed",
                                   exc_info=True)
        return frame

    async def _read_ui(self, args: dict) -> ToolResult:
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
            elements = await self._prim_read_ui(window_title)
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

    async def _click_element(self, args: dict) -> ToolResult:
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
        # Fresh abort event for each call -- a Stop from a prior run must not
        # short-circuit a new tool call the user just asked for.
        self._abort_event.clear()
        await self._emit_driving(
            True, mode=self._driving_mode(), window_title=window_title, action="click_element",
        )
        try:
            for n in range(1, limit + 1):
                # Yield to the event loop between tries so input is never 100%
                # hijacked and the abort event can propagate mid-loop.
                await asyncio.sleep(0)
                if self._abort_event.is_set():
                    trace["tries"].append(
                        {"n": n, "observed": False, "acted": False,
                         "expected": f"click element {name!r}",
                         "actual": "aborted by kill switch", "ok": False}
                    )
                    return self._finish(trace)
                try:
                    elements = await self._prim_read_ui(window_title)
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
                # ADR-0016 amendment (b): flag a non-unique name/role match in
                # the trace instead of silently guessing. First-match
                # semantics (whichever _find_element picked) are unchanged.
                multi_match = _count_matches(elements, name, role) > 1
                bbox = match.get("bbox") or []
                if len(bbox) != 4:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": "bbox [l,t,r,b]",
                         "actual": f"bbox={bbox!r}", "ok": False}
                    )
                    continue
                # S16 #610: session 2 allows full-desktop actions (no window
                # bound restriction -- the whole session belongs to Felix).
                if not self._in_isolated_session():
                    wb = self._window_bounds(window_title)
                    if wb is not None and not _bbox_within(bbox, wb):
                        trace["tries"].append(
                            {"n": n, "observed": True, "acted": False,
                             "expected": f"bbox {bbox} inside window {wb}",
                             "actual": "outside window bounds -- refused",
                             "ok": False}
                        )
                        continue
                # #592: try the background UIA control pattern first -- no
                # cursor movement. Falls back to the existing foreground
                # pyautogui click, on the SAME re-resolved element's bbox,
                # when no usable pattern exists (or the master switch is off).
                path = "uia_synthetic"
                click_bbox = bbox
                if self._background_actuation_enabled():
                    fg_before = self._foreground_window_safe()
                    fired, live_bbox = self._try_pattern_click(window_title, name, role)
                    if live_bbox is not None:
                        click_bbox = live_bbox
                    if fired:
                        fg_after = self._foreground_window_safe()
                        stole_focus = self._foregrounded(fg_before, fg_after, window_title)
                        x, y = _bbox_center(click_bbox)
                        entry = {
                            "n": n, "observed": True, "acted": True,
                            "expected": f"click at ({x},{y})",
                            "actual": "clicked via uia_pattern", "ok": not stole_focus,
                            "path": "uia_pattern", "foregrounded": stole_focus,
                        }
                        if multi_match:
                            entry["multi_match"] = True
                        if stole_focus:
                            # #593 (ADR-0016 amendment d): a background
                            # pattern action that unexpectedly foregrounds
                            # the target is a SOFT TRIP -- stop here, never
                            # fall through to the input-stealing foreground
                            # fallback. #594: flip the live indicator to the
                            # foreground/urgent style in real time.
                            entry["actual"] = (
                                "soft trip: background click unexpectedly "
                                "foregrounded the target window"
                            )
                            await self._emit_driving(
                                True, mode="foreground", window_title=window_title,
                                action="click_element",
                            )
                        trace["tries"].append(entry)
                        trace["ok"] = not stole_focus
                        return self._finish(trace)
                # ADR-0016 amendment (d): "what I'm doing takes priority" --
                # gate the foreground fallback on user idle before grabbing
                # the mouse. S16: dropped inside session 2 (no user cursor
                # to yield to in Felix's dedicated session).
                if not self._in_isolated_session() and not self._foreground_gate(
                    window_title, name, n, trace
                ):
                    continue
                # #594: no usable pattern -- this try is actually going to
                # steal the cursor, so flip the indicator to foreground now.
                await self._emit_driving(
                    True, mode="foreground", window_title=window_title, action="click_element",
                )
                x, y = _bbox_center(click_bbox)
                fg_before = self._foreground_window_safe()
                try:
                    await self._prim_click(click_bbox)
                except CornerAbort:
                    self._abort_event.set()
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": f"click at ({x},{y})",
                         "actual": "corner-failsafe abort",
                         "ok": False, "path": "uia_synthetic"}
                    )
                    return self._finish(trace)
                except Exception as exc:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": f"click at ({x},{y})",
                         "actual": f"error: {exc}", "ok": False,
                         "path": "uia_synthetic"}
                    )
                    continue
                fg_after = self._foreground_window_safe()
                entry = {
                    "n": n, "observed": True, "acted": True,
                    "expected": f"click at ({x},{y})",
                    "actual": "clicked", "ok": True, "path": path,
                    "foregrounded": self._foregrounded(fg_before, fg_after, window_title),
                }
                if multi_match:
                    entry["multi_match"] = True
                trace["tries"].append(entry)
                trace["ok"] = True
                return self._finish(trace)
            # S5 #578: structured retries exhausted. Try the pixel-vision
            # fallback once. No-op when the grounding seam isn't wired, so
            # structured-only remains the default posture.
            await self._pixel_fallback_click(window_title, name, trace)
            # S6 #579: still failing after structured + pixel? Escalate to
            # attended-handoff (notify + surface window + await human). DRM-
            # black also reaches here because _pixel_fallback_click returns
            # False after recording the escalated try.
            if not trace.get("ok") and not self._abort_event.is_set():
                if await self._escalate_to_handoff(
                    window_title, name,
                    f"structured + pixel retries exhausted on {name!r}",
                    trace,
                ):
                    trace["ok"] = True
            return self._finish(trace)
        finally:
            await self._emit_driving(False)

    async def _type_into(self, args: dict) -> ToolResult:
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
        self._abort_event.clear()
        await self._emit_driving(
            True, mode=self._driving_mode(), window_title=window_title, action="type_into",
        )
        try:
            for n in range(1, limit + 1):
                await asyncio.sleep(0)
                if self._abort_event.is_set():
                    trace["tries"].append(
                        {"n": n, "observed": False, "acted": False,
                         "expected": f"type into {name!r}",
                         "actual": "aborted by kill switch", "ok": False}
                    )
                    return self._finish(trace)
                try:
                    elements = await self._prim_read_ui(window_title)
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
                multi_match = _count_matches(elements, name, role) > 1
                bbox = match.get("bbox") or []
                if len(bbox) != 4:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": "bbox [l,t,r,b]",
                         "actual": f"bbox={bbox!r}", "ok": False}
                    )
                    continue
                # S16 #610: session 2 allows full-desktop type actions.
                if not self._in_isolated_session():
                    wb = self._window_bounds(window_title)
                    if wb is not None and not _bbox_within(bbox, wb):
                        trace["tries"].append(
                            {"n": n, "observed": True, "acted": False,
                             "expected": f"bbox {bbox} inside window {wb}",
                             "actual": "outside window bounds -- refused",
                             "ok": False}
                        )
                        continue
                # #592: SetValue fills, never submits -- only on an
                # allowlisted role outside any browser/Electron surface
                # (checked inside _try_pattern_set_value). Falls back to the
                # existing foreground click+type on the SAME re-resolved
                # element's bbox when the gate refuses or no pattern exists.
                path = "uia_synthetic"
                type_bbox = bbox
                filled = False
                foregrounded_now = False
                if self._background_actuation_enabled():
                    fg_before = self._foreground_window_safe()
                    filled, live_bbox = self._try_pattern_set_value(
                        window_title, name, match.get("role"), text,
                    )
                    if live_bbox is not None:
                        type_bbox = live_bbox
                    if filled:
                        fg_after = self._foreground_window_safe()
                        stole_focus = self._foregrounded(fg_before, fg_after, window_title)
                        if stole_focus:
                            # #593 (ADR-0016 amendment d): soft trip -- a
                            # background SetValue unexpectedly foregrounded
                            # the target. Stop here, never fall through to
                            # the input-stealing foreground fallback. #594:
                            # flip the live indicator in real time.
                            entry = {
                                "n": n, "observed": True, "acted": True,
                                "expected": f"SetValue {text!r}",
                                "actual": (
                                    "soft trip: background SetValue "
                                    "unexpectedly foregrounded the target "
                                    "window"
                                ),
                                "ok": False, "path": "uia_pattern",
                                "foregrounded": True,
                            }
                            if multi_match:
                                entry["multi_match"] = True
                            await self._emit_driving(
                                True, mode="foreground", window_title=window_title,
                                action="type_into",
                            )
                            trace["tries"].append(entry)
                            trace["ok"] = False
                            return self._finish(trace)
                        path = "uia_pattern"
                if not filled:
                    # ADR-0016 amendment (d): idle-gate the foreground
                    # click+type fallback. S16: dropped inside session 2 (no
                    # user cursor in Felix's dedicated session).
                    if not self._in_isolated_session() and not self._foreground_gate(
                        window_title, name, n, trace
                    ):
                        continue
                    # #594: no usable pattern -- flip the indicator to
                    # foreground before the pyautogui click+type actually
                    # steals the cursor.
                    await self._emit_driving(
                        True, mode="foreground", window_title=window_title, action="type_into",
                    )
                    fg_before = self._foreground_window_safe()
                    try:
                        await self._prim_click(type_bbox)
                        await self._prim_type(text)
                    except CornerAbort:
                        self._abort_event.set()
                        trace["tries"].append(
                            {"n": n, "observed": True, "acted": False,
                             "expected": f"type {text!r}",
                             "actual": "corner-failsafe abort",
                             "ok": False, "path": "uia_synthetic"}
                        )
                        return self._finish(trace)
                    except Exception as exc:
                        trace["tries"].append(
                            {"n": n, "observed": True, "acted": False,
                             "expected": f"type {text!r}",
                             "actual": f"error: {exc}", "ok": False,
                             "path": "uia_synthetic"}
                        )
                        continue
                    fg_after = self._foreground_window_safe()
                    foregrounded_now = self._foregrounded(fg_before, fg_after, window_title)
                # Verify: re-read UIA and check the target's value contains the
                # text (falls back to "acted" when the backend doesn't expose
                # values).
                try:
                    after = await self._prim_read_ui(window_title)
                except Exception as exc:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": True,
                         "expected": f"post-type value contains {text!r}",
                         "actual": f"re-read error: {exc}", "ok": False,
                         "path": path, "foregrounded": foregrounded_now}
                    )
                    continue
                after_match = _find_element(after, name, role) or {}
                value = after_match.get("value")
                if isinstance(value, str) and text in value:
                    entry = {
                        "n": n, "observed": True, "acted": True,
                        "expected": f"value contains {text!r}",
                        "actual": f"value={value!r}", "ok": True, "path": path,
                        "foregrounded": foregrounded_now,
                    }
                    if multi_match:
                        entry["multi_match"] = True
                    trace["tries"].append(entry)
                    trace["ok"] = True
                    return self._finish(trace)
                if value is None:
                    entry = {
                        "n": n, "observed": True, "acted": True,
                        "expected": f"typed {text!r}",
                        "actual": "no value read; assumed ok", "ok": True,
                        "path": path, "foregrounded": foregrounded_now,
                    }
                    if multi_match:
                        entry["multi_match"] = True
                    trace["tries"].append(entry)
                    trace["ok"] = True
                    return self._finish(trace)
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": True,
                     "expected": f"value contains {text!r}",
                     "actual": f"value={value!r}", "ok": False, "path": path,
                     "foregrounded": foregrounded_now}
                )
            # S6 #579: type_into has no pixel fallback (text input can't be
            # coordinate-clicked into a field the tree can't see). On UIA
            # exhaustion, hand over to the human directly.
            if not self._abort_event.is_set():
                if await self._escalate_to_handoff(
                    window_title, name,
                    f"could not type into {name!r} after {limit} tries",
                    trace,
                ):
                    trace["ok"] = True
            return self._finish(trace)
        finally:
            await self._emit_driving(False)

    def _find_address_bar(
        self, elements: list[dict], override_name: str | None,
    ) -> dict | None:
        """Return the browser's address-bar element, preferring an explicit
        name override, then walking the built-in candidate list. Substring
        match on ``name`` -- the OS localises "Address and search bar" so
        an exact-match is too brittle."""
        if override_name:
            hit = _find_element(elements, override_name, None)
            if hit is not None:
                return hit
        for el in elements:
            name = (el.get("name") or "").strip().lower()
            if not name:
                continue
            for cand in _ADDRESS_BAR_CANDIDATES:
                if cand.lower() in name:
                    return el
        return None

    async def _browser_navigate(self, args: dict) -> ToolResult:
        """S7 #580: type ``url`` into the target browser window's address bar
        via OS-level input, submit, and read the resulting page's UIA tree.
        No Playwright / CDP -- deliberately the stealth path. Composes the S1
        seams (read_ui + click + type_text) plus one new one (press_key /
        typewriter-newline fallback). On UIA exhaustion, hands off to the
        human via the S6 attended-handoff seam."""
        window_title = args["window_title"]
        url = args.get("url") or ""
        override = args.get("address_bar") or None
        limit = _clamp_retries(args.get("retries"))
        trace = {
            "tool": "browser_navigate",
            "window_title": window_title,
            "target": None,
            "action": "navigate",
            "url": url,
            "tries": [],
            "ok": False,
        }
        if not url:
            trace["tries"].append(
                {"n": 1, "observed": False, "acted": False,
                 "expected": "non-empty url", "actual": "empty", "ok": False}
            )
            return self._finish(trace)
        self._abort_event.clear()
        # #594: browser_navigate has no control-pattern equivalent (address
        # bar type + Enter only) -- it is always foreground, regardless of
        # the background_actuation setting.
        await self._emit_driving(
            True, mode="foreground", window_title=window_title, action="browser_navigate",
        )
        try:
            for n in range(1, limit + 1):
                await asyncio.sleep(0)
                if self._abort_event.is_set():
                    trace["tries"].append(
                        {"n": n, "observed": False, "acted": False,
                         "expected": f"navigate to {url!r}",
                         "actual": "aborted by kill switch", "ok": False}
                    )
                    return self._finish(trace)
                try:
                    elements = self._backend.read_ui(window_title)  # type: ignore[union-attr]
                except Exception as exc:
                    trace["tries"].append(
                        {"n": n, "observed": False, "acted": False,
                         "expected": "address bar",
                         "actual": f"read_ui error: {exc}", "ok": False}
                    )
                    continue
                bar = self._find_address_bar(elements, override)
                if bar is None:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": "address bar element",
                         "actual": "not found in UIA tree", "ok": False}
                    )
                    continue
                bbox = bar.get("bbox") or []
                if len(bbox) != 4:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": "address bar bbox [l,t,r,b]",
                         "actual": f"bbox={bbox!r}", "ok": False}
                    )
                    continue
                wb = self._window_bounds(window_title)
                if wb is not None and not _bbox_within(bbox, wb):
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": f"bbox {bbox} inside window {wb}",
                         "actual": "outside window bounds -- refused",
                         "ok": False}
                    )
                    continue
                trace["target"] = {
                    "name": bar.get("name"),
                    "role": bar.get("role"),
                    "bbox": bbox,
                }
                # ADR-0016 amendment (d): browser_navigate is entirely
                # foreground (address-bar type + Enter, no control-pattern
                # equivalent) -- idle-gate it exactly like the other
                # foreground fallbacks before touching the mouse/keyboard.
                if not self._foreground_gate(window_title, "address bar", n, trace):
                    continue
                fg_before = self._foreground_window_safe()
                try:
                    self._backend.click(bbox)  # type: ignore[union-attr]
                    self._backend.type_text(url)  # type: ignore[union-attr]
                    press_key = getattr(self._backend, "press_key", None)
                    if press_key is not None:
                        press_key("enter")
                    else:
                        # Fallback for backends without press_key: typewriter
                        # newline is treated as Enter by most edit controls.
                        self._backend.type_text("\n")  # type: ignore[union-attr]
                except CornerAbort:
                    self._abort_event.set()
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": f"navigate to {url!r}",
                         "actual": "corner-failsafe abort", "ok": False}
                    )
                    return self._finish(trace)
                except Exception as exc:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": f"navigate to {url!r}",
                         "actual": f"error: {exc}", "ok": False}
                    )
                    continue
                fg_after = self._foreground_window_safe()
                foregrounded = self._foregrounded(fg_before, fg_after, window_title)
                # Post-navigate: re-read UIA so the caller can see the new
                # page. A read failure doesn't fail the call -- the address-
                # bar submit already fired.
                try:
                    after = self._backend.read_ui(window_title)  # type: ignore[union-attr]
                except Exception as exc:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": True,
                         "expected": f"post-navigate elements for {url!r}",
                         "actual": f"re-read error: {exc}", "ok": True,
                         "foregrounded": foregrounded}
                    )
                    trace["ok"] = True
                    return self._finish(trace)
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": True,
                     "expected": f"navigate to {url!r}",
                     "actual": f"submitted; {len(after)} elements after",
                     "ok": True, "foregrounded": foregrounded}
                )
                trace["ok"] = True
                trace["elements"] = after
                return self._finish(trace)
            # UIA exhausted with no address bar found -- attended handoff.
            if not self._abort_event.is_set():
                if await self._escalate_to_handoff(
                    window_title, "address bar",
                    f"could not navigate to {url!r} after {limit} tries",
                    trace,
                ):
                    trace["ok"] = True
            return self._finish(trace)
        finally:
            await self._emit_driving(False)


# --- Windows backend (lazily imported; never touched in tests) --------------

class _WindowsBackend:
    """UIA read + pyautogui actuation + mss capture. Constructed on Windows only."""

    def __init__(self) -> None:
        import pyautogui
        import uiautomation as uia
        # (a) Corner-failsafe: slam mouse to a screen corner mid-action -> a
        # pyautogui.FailSafeException we re-raise as CornerAbort so the plugin
        # loop can treat it as first-class abort without depending on pyautogui.
        pyautogui.FAILSAFE = True
        self._pyautogui = pyautogui
        self._uia = uia
        self._mss = None  # capture is not called in S1; wired for S5.

    def _window(self, window_title: str):
        return self._uia.WindowControl(searchDepth=1, SubName=window_title)

    def read_ui(self, window_title: str) -> list[dict]:
        win = self._window(window_title)
        if not win.Exists(1):
            return []
        # Iterative subtree walk via GetChildren. The prior `uia.WalkTree(win)`
        # call was broken against uiautomation 2.0.29: with no traversal
        # callback it yields nothing, and it yields (control, depth, remaining)
        # 3-tuples the old 2-var unpack couldn't take. GetChildren is stable
        # across uiautomation versions -- no signature to drift under us.
        out: list[dict] = []
        stack = [(win, 0)]
        while stack:
            ctrl, depth = stack.pop()
            try:
                rect = ctrl.BoundingRectangle
                out.append({
                    "name": ctrl.Name or "",
                    "role": ctrl.ControlTypeName or "",
                    "bbox": [rect.left, rect.top, rect.right, rect.bottom],
                })
            except Exception:
                pass
            if depth < _UIA_MAX_DEPTH:
                try:
                    for child in ctrl.GetChildren():
                        stack.append((child, depth + 1))
                except Exception:
                    continue
        return out

    def click(self, bbox: list[int]) -> None:
        l, t, r, b = bbox
        x, y = (l + r) // 2, (t + b) // 2
        try:
            self._pyautogui.click(x, y)
        except self._pyautogui.FailSafeException as exc:
            raise CornerAbort(str(exc)) from exc

    def click_at(self, x: int, y: int) -> None:
        """S5 #578: single-coordinate click for the pixel-vision fallback
        path (grounded coord -> click). Same corner-failsafe translation as
        ``click``."""
        try:
            self._pyautogui.click(int(x), int(y))
        except self._pyautogui.FailSafeException as exc:
            raise CornerAbort(str(exc)) from exc

    def type_text(self, text: str) -> None:
        try:
            self._pyautogui.typewrite(text, interval=0.01)
        except self._pyautogui.FailSafeException as exc:
            raise CornerAbort(str(exc)) from exc

    def press_key(self, key: str) -> None:
        """S7 #580: single named-key press (Enter after typing a URL, etc.)."""
        try:
            self._pyautogui.press(key)
        except self._pyautogui.FailSafeException as exc:
            raise CornerAbort(str(exc)) from exc

    def capture_frame(self, window_title: str) -> bytes | None:
        """S5 #578: RAM-only window capture for pixel-vision grounding.

        Uses ``mss`` to grab the target window's outer rect and returns raw
        BGRA bytes. Bytes never touch disk -- the plugin holds them in a
        capped ring buffer + hands them to the vision seam, then drops the
        reference (ADR-0016 sec 7 audio-buffer rule). Returns None when the
        window is gone, mss is missing, or grab fails (fallback then skips
        this attempt rather than clicking blind)."""
        if self._mss is None:
            try:
                import mss  # type: ignore[import-not-found]
                self._mss = mss.mss()
            except Exception:
                return None
        wb = self.window_bounds(window_title)
        if wb is None:
            return None
        l, t, r, b = wb
        if r <= l or b <= t:
            return None
        monitor = {"left": l, "top": t, "width": r - l, "height": b - t}
        try:
            shot = self._mss.grab(monitor)
        except Exception:
            return None
        return bytes(shot.rgb)

    def window_bounds(self, window_title: str) -> list[int] | None:
        """Bounds of the target window's outer rect, in screen coords.
        Returns None when the window isn't currently open -- the plugin then
        skips the bounded-region check for that iteration (retry surface will
        record ``element not present`` on the next observation)."""
        win = self._window(window_title)
        if not win.Exists(0):
            return None
        try:
            rect = win.BoundingRectangle
        except Exception:
            return None
        return [rect.left, rect.top, rect.right, rect.bottom]

    def surface_window(self, window_title: str) -> None:
        """S6 #579: bring the target window to the foreground so the human can
        take over during an attended handoff. Best-effort -- silently ignores
        a missing window or an OS refusal (Windows sometimes denies
        SetForegroundWindow to a background process)."""
        win = self._window(window_title)
        if not win.Exists(0):
            return
        try:
            win.SetActive()
        except Exception:
            logger.warning(
                "[computer_use] SetActive failed for %r", window_title, exc_info=True,
            )

    def resolve_element(
        self, window_title: str, name: str, role: str | None,
    ) -> tuple[object, list[int]] | None:
        """#592: fresh re-walk of the live UIA tree, same traversal order (and
        therefore same first-match result) as ``read_ui`` -- returns the live
        control + its current bbox, never held past this call."""
        win = self._window(window_title)
        if not win.Exists(0):
            return None
        n = (name or "").strip().lower()
        r = (role or "").strip().lower() or None
        stack = [(win, 0)]
        while stack:
            ctrl, depth = stack.pop()
            try:
                if (ctrl.Name or "").strip().lower() == n and (
                    r is None or (ctrl.ControlTypeName or "").strip().lower() == r
                ):
                    rect = ctrl.BoundingRectangle
                    return ctrl, [rect.left, rect.top, rect.right, rect.bottom]
            except Exception:
                pass
            if depth < _UIA_MAX_DEPTH:
                try:
                    for child in ctrl.GetChildren():
                        stack.append((child, depth + 1))
                except Exception:
                    continue
        return None

    def pattern_click(self, element) -> bool:
        """#592: background UIA control-pattern click -- Invoke, then Toggle,
        then SelectionItem.Select, then ExpandCollapse.Expand, in that
        preference order. No cursor movement. False when none apply."""
        try:
            pattern = element.GetInvokePattern()
            if pattern:
                pattern.Invoke()
                return True
        except Exception:
            pass
        try:
            pattern = element.GetTogglePattern()
            if pattern:
                pattern.Toggle()
                return True
        except Exception:
            pass
        try:
            pattern = element.GetSelectionItemPattern()
            if pattern:
                pattern.Select()
                return True
        except Exception:
            pass
        try:
            pattern = element.GetExpandCollapsePattern()
            if pattern:
                pattern.Expand()
                return True
        except Exception:
            pass
        return False

    def pattern_set_value(self, element, text: str) -> bool:
        """#592: ValuePattern.SetValue -- atomic fill, no keystrokes fired, no
        submit. False (never raises) when unavailable or read-only."""
        try:
            pattern = element.GetValuePattern()
            if not pattern or pattern.IsReadOnly:
                return False
            pattern.SetValue(text)
            return True
        except Exception:
            return False

    def window_class(self, window_title: str) -> str | None:
        """#592: native window class (e.g. ``"Chrome_WidgetWin_1"``) of the
        target window, used to keep SetValue out of any browser/Electron
        surface. None when the window is missing or unreadable."""
        win = self._window(window_title)
        if not win.Exists(0):
            return None
        try:
            return win.ClassName or None
        except Exception:
            return None

    def last_input_ms(self) -> int:
        """#593: ms since the user's last physical input, via
        ``GetLastInputInfo`` -- one syscall, no cursor moved. The idle gate
        for the foreground (pyautogui) fallback (ADR-0016 amendment d)."""
        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        tick_count = ctypes.windll.kernel32.GetTickCount()
        return max(0, tick_count - lii.dwTime)

    def foreground_window(self) -> str | None:
        """#593: the current OS foreground window's title, via
        ``GetForegroundWindow`` + ``GetWindowText`` -- the focus-theft probe
        (ADR-0016 amendment d/g). None when there is no foreground window or
        its title can't be read."""
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or None


def _default_hotkey_register(abort: Callable[[], None]) -> Callable[[], None] | None:
    """Default (b) F11+F12 chord registrar on Windows using the ``keyboard``
    package. Failures fall through silently -- the plugin still works, just
    without this leg of the kill switch (the corner-failsafe + Visualiser Stop
    legs remain). No-op on non-Windows."""
    if sys.platform != "win32":
        return None
    try:
        import keyboard  # type: ignore[import-not-found]
    except Exception:
        logger.warning(
            "[computer_use] `keyboard` package not installed -- F11+F12 "
            "kill-switch leg disabled",
        )
        return None
    try:
        keyboard.add_hotkey("f11+f12", abort, suppress=False)
    except Exception:
        logger.warning(
            "[computer_use] failed to register F11+F12 hotkey", exc_info=True,
        )
        return None
    return lambda: keyboard.remove_hotkey("f11+f12")


def create() -> ComputerUsePlugin:
    # Wire the default F11+F12 registrar so a production plugin gets the
    # kill-switch leg even without main.py explicitly setting the seam.
    if _hotkey_register_fn is None:
        set_hotkey_register_fn(_default_hotkey_register)
    return ComputerUsePlugin()
