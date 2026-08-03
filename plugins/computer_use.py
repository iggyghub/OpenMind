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
  Idle-gating the foreground fallback (#593), the driving-indicator's
  background/foreground mode (#594), and live-verify docs (#595) are follow-on
  slices -- not built here.
"""
from __future__ import annotations

import asyncio
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
DrivingFn = Callable[[bool], Awaitable[None]]
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

_driving_fn: Optional[DrivingFn] = None
_hotkey_register_fn: Optional[HotkeyRegisterFn] = None
_vision_ground_fn: Optional[VisionGroundFn] = None
_attended_handoff_fn: Optional[AttendedHandoffFn] = None
_background_actuation_fn: Optional[BackgroundActuationFn] = None
_setvalue_roles_fn: Optional[SetValueRolesFn] = None
_plugin_instance: Optional["ComputerUsePlugin"] = None

# ADR-0016 amendment defaults -- used whenever no getter is wired (tests,
# or a host that hasn't started main.py's settings wiring yet). Match
# felix-settings.json's shipped defaults in cerebral/settings.py.
DEFAULT_BACKGROUND_ACTUATION = True
DEFAULT_SETVALUE_ROLES: tuple[str, ...] = ("Edit", "Document")


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


def abort_current() -> None:
    """Module-level abort: signals the (c) Visualiser Stop leg. IPC in main.py
    calls this on a ``computer_use_stop`` message from the tray."""
    if _plugin_instance is not None:
        _plugin_instance.abort()


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
        self._thumbnail_ring: deque[bytes] = deque(maxlen=max(1, int(thumbnail_ring_size)))
        # asyncio.Event carries the abort signal across the (a) corner-failsafe,
        # (b) F11+F12 chord, and (c) Visualiser Stop legs. Created here so it
        # is safe to inspect before any tool call fires.
        self._abort_event = asyncio.Event()
        # F11+F12 registration -- once per plugin instance. Tests pass a fake
        # that captures the callback; production leaves this alone so the
        # module-level seam wired by main.py drives it.
        reg = hotkey_register_fn if hotkey_register_fn is not _UNSET else _hotkey_register_fn
        if reg is not None:
            try:
                reg(self.abort)
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

    async def _emit_driving(self, driving: bool) -> None:
        """Best-effort broadcast of the "Felix is driving" indicator. A broken
        sink must never break a computer_use call in progress."""
        if self._driving_fn is None:
            return
        try:
            await self._driving_fn(driving)
        except Exception:
            logger.warning("[computer_use] driving_fn broadcast failed", exc_info=True)

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
            return self._read_ui(args)
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
        capture_fn = getattr(self._backend, "capture_frame", None)
        if capture_fn is None:
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
            frame = capture_fn(window_title)
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
        # Non-black frame -- park it in the RAM ring for in-session debug.
        # ADR-0016 sec 7: bytes never leave process memory.
        self._thumbnail_ring.append(bytes(frame))
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
        trace["target"] = {"name": name, "role": None, "bbox": [x, y, x, y]}
        trace["tries"].append(
            {"n": n, "observed": True, "acted": True,
             "expected": f"pixel click at ({x},{y})",
             "actual": "clicked", "ok": True, "path": "pixel"}
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
        await self._emit_driving(True)
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
                    fired, live_bbox = self._try_pattern_click(window_title, name, role)
                    if live_bbox is not None:
                        click_bbox = live_bbox
                    if fired:
                        x, y = _bbox_center(click_bbox)
                        entry = {
                            "n": n, "observed": True, "acted": True,
                            "expected": f"click at ({x},{y})",
                            "actual": "clicked via uia_pattern", "ok": True,
                            "path": "uia_pattern",
                        }
                        if multi_match:
                            entry["multi_match"] = True
                        trace["tries"].append(entry)
                        trace["ok"] = True
                        return self._finish(trace)
                x, y = _bbox_center(click_bbox)
                try:
                    self._backend.click(click_bbox)  # type: ignore[union-attr]
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
                entry = {
                    "n": n, "observed": True, "acted": True,
                    "expected": f"click at ({x},{y})",
                    "actual": "clicked", "ok": True, "path": path,
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
        await self._emit_driving(True)
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
                multi_match = _count_matches(elements, name, role) > 1
                bbox = match.get("bbox") or []
                if len(bbox) != 4:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": False,
                         "expected": "bbox [l,t,r,b]",
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
                # #592: SetValue fills, never submits -- only on an
                # allowlisted role outside any browser/Electron surface
                # (checked inside _try_pattern_set_value). Falls back to the
                # existing foreground click+type on the SAME re-resolved
                # element's bbox when the gate refuses or no pattern exists.
                path = "uia_synthetic"
                type_bbox = bbox
                filled = False
                if self._background_actuation_enabled():
                    filled, live_bbox = self._try_pattern_set_value(
                        window_title, name, match.get("role"), text,
                    )
                    if live_bbox is not None:
                        type_bbox = live_bbox
                if filled:
                    path = "uia_pattern"
                else:
                    try:
                        self._backend.click(type_bbox)  # type: ignore[union-attr]
                        self._backend.type_text(text)  # type: ignore[union-attr]
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
                # Verify: re-read UIA and check the target's value contains the
                # text (falls back to "acted" when the backend doesn't expose
                # values).
                try:
                    after = self._backend.read_ui(window_title)  # type: ignore[union-attr]
                except Exception as exc:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": True,
                         "expected": f"post-type value contains {text!r}",
                         "actual": f"re-read error: {exc}", "ok": False,
                         "path": path}
                    )
                    continue
                after_match = _find_element(after, name, role) or {}
                value = after_match.get("value")
                if isinstance(value, str) and text in value:
                    entry = {
                        "n": n, "observed": True, "acted": True,
                        "expected": f"value contains {text!r}",
                        "actual": f"value={value!r}", "ok": True, "path": path,
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
                        "path": path,
                    }
                    if multi_match:
                        entry["multi_match"] = True
                    trace["tries"].append(entry)
                    trace["ok"] = True
                    return self._finish(trace)
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": True,
                     "expected": f"value contains {text!r}",
                     "actual": f"value={value!r}", "ok": False, "path": path}
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
        await self._emit_driving(True)
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
                # Post-navigate: re-read UIA so the caller can see the new
                # page. A read failure doesn't fail the call -- the address-
                # bar submit already fired.
                try:
                    after = self._backend.read_ui(window_title)  # type: ignore[union-attr]
                except Exception as exc:
                    trace["tries"].append(
                        {"n": n, "observed": True, "acted": True,
                         "expected": f"post-navigate elements for {url!r}",
                         "actual": f"re-read error: {exc}", "ok": True}
                    )
                    trace["ok"] = True
                    return self._finish(trace)
                trace["tries"].append(
                    {"n": n, "observed": True, "acted": True,
                     "expected": f"navigate to {url!r}",
                     "actual": f"submitted; {len(after)} elements after",
                     "ok": True}
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
