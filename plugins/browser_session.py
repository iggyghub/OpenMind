"""
Browser-session plugin — in-session page driving for Felix.

Drives the **logged-in** ``google_web`` Playwright persistent context built in
``cerebral/browser/session.py`` (ADR-0005 amendment 2026-06-25). Unlike the
``browser`` plugin (OpenClaw headless HTTP, anonymous), these tools act inside
the dedicated secondary account's authenticated session, so they can read and
fill pages that require login.

Tools:
  - browser_open_session : reuse the on-disk session, else the unattended
        password fallback (this is why secrets_read + network_egress_cloud are
        required). A first-time login can only be seeded by a human via the
        tray "Log in now" button or scripts/seed_browser_login.py — never from
        a tool call, which has no one to complete 2FA.
  - read_page  : (optionally navigate, then) snapshot url / title / visible text
  - fill_form  : fill one or more (selector, value) fields on the current page
  - click      : click a selector, return the resulting URL

The plugin is STATEFUL: browser_open_session opens a persistent context and
keeps it in ``self._open_session`` so read_page / fill_form / click act on the
same live page. The orchestrator creates the plugin once and dispatches every
call to that instance, so the session survives across calls. Re-opening closes
the prior context first (a persistent-context dir is locked by its running
Chromium).

All browser side-effects go through ``BrowserSession`` (the same fake-driver
seam the harness's unit tests use), so this orchestration is unit-testable
without a real browser. The active-profile ``BrowserSession`` is supplied by
``set_session_factory`` (wired from cerebral/main.py), mirroring the memory
plugin's ``set_memory_factory``.
"""

import json
import logging
from typing import Callable, Optional

from cerebral.browser import BrowserSession
from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "browser_session"

# ADR-0005: browser_open_session may read the stored account password
# (secrets_read) and drive a login that egresses to Google (network_egress_
# cloud). read_page/fill_form/click inherit both — a deliberate per-plugin
# over-declaration in the calendar.py / clockify.py house style; capabilities
# are declared per module, not per tool.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "network_egress_cloud",
})

# Canonical error vocabulary.
_FACTORY_NOT_WIRED_MSG = "Browser session is not available — factory not wired"
_NO_ACTIVE_PROFILE_MSG = "Browser session is not available — no active profile"
_NOT_OPEN_MSG = (
    "No open browser session — call browser_open_session first"
)
_SEED_HINT = (
    "could not establish a logged-in session. Seed one first via the tray "
    "'Log in now' button or `python scripts/seed_browser_login.py`"
)
_BLANK_SELECTOR_MSG = "'selector' is required"
_BLANK_FIELDS_MSG = "'fields' must be a non-empty list of {selector, value}"
_GENERIC_ERROR_MSG = "Browser operation failed"

# Page text can be huge; cap what we hand back to the model.
_MAX_TEXT_CHARS = 5000

SessionFactory = Callable[[], Optional[BrowserSession]]

# Module-level factory set by cerebral/main.py via set_session_factory(). Tests
# bypass this by passing a factory directly to the plugin constructor.
_session_factory: Optional[SessionFactory] = None


def set_session_factory(fn: SessionFactory) -> None:
    """Wire main.py's ``_get_browser_session()`` — called once at startup.

    The factory must return ``BrowserSession | None``: ``None`` when no profile
    is loaded, a (not-yet-opened) ``BrowserSession`` for the active profile
    otherwise. It re-resolves each call so a profile switch is picked up by the
    next browser_open_session.
    """
    global _session_factory
    _session_factory = fn


class BrowserSessionPlugin:
    name = PLUGIN_NAME

    def __init__(self, session_factory: Optional[SessionFactory] = None) -> None:
        # Constructor-injected factory wins over the module-level setter (tests
        # pass directly; production leaves this None and main.py wires it).
        self._factory = session_factory
        # The currently-open authenticated session, or None. Holds the live
        # Playwright context across read/fill/click calls.
        self._open_session: Optional[BrowserSession] = None

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="browser_open_session",
                description=(
                    "Open (or reuse) the logged-in browser session for the "
                    "dedicated web account. Reuses the saved session when "
                    "still valid; otherwise re-logs in with the stored "
                    "password. Returns the login state and account email. Call "
                    "this once before read_page / fill_form / click. If it "
                    "reports it could not log in, the account must be logged in "
                    "by hand first (tray 'Log in now')."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="read_page",
                description=(
                    "Read the current page in the open browser session: returns "
                    "its URL, title, and visible text. Optionally navigate to a "
                    "URL first. Requires an open session (browser_open_session)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": (
                                "Optional URL to navigate to before reading. "
                                "Omit to read the page already loaded."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="fill_form",
                description=(
                    "Fill one or more form fields on the current page. Each "
                    "field is a CSS selector and the value to type into it. "
                    "Requires an open session (browser_open_session)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "array",
                            "description": "Fields to fill, in order.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "selector": {
                                        "type": "string",
                                        "description": "CSS selector for the input.",
                                    },
                                    "value": {
                                        "type": "string",
                                        "description": "Text to type into it.",
                                    },
                                },
                                "required": ["selector", "value"],
                            },
                        },
                    },
                    "required": ["fields"],
                },
            ),
            Tool(
                name="click",
                description=(
                    "Click an element on the current page by CSS selector. "
                    "Returns the resulting URL after any navigation. Requires "
                    "an open session (browser_open_session)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector for the element to click.",
                        },
                    },
                    "required": ["selector"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "browser_open_session":
            return await self._open_session_tool(args)
        if tool_name == "read_page":
            return await self._read_page(args)
        if tool_name == "fill_form":
            return await self._fill_form(args)
        if tool_name == "click":
            return await self._click(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_session(self) -> tuple[Optional[BrowserSession], Optional[str]]:
        """Return ``(session, error_string)`` — exactly one is non-None."""
        factory = self._factory or _session_factory
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        session = factory()
        if session is None:
            return None, _NO_ACTIVE_PROFILE_MSG
        return session, None

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    async def _open_session_tool(self, args: dict) -> ToolResult:
        session, err = self._resolve_session()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        # Close any prior context first — its persistent-context dir is locked
        # while Chromium runs, so a second open() on the same dir would fail.
        if self._open_session is not None:
            try:
                await self._open_session.close()
            except Exception:
                logger.warning("[browser_session] prior close failed", exc_info=True)
            self._open_session = None

        try:
            result = await session.ensure_logged_in(unattended=True)
        except Exception:
            logger.warning("[browser_session] open failed", exc_info=True)
            try:
                await session.close()
            except Exception:
                pass
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)

        if not result.ok:
            try:
                await session.close()
            except Exception:
                pass
            return ToolResult(content=f"{_SEED_HINT}.", is_error=True)

        self._open_session = session
        return ToolResult(content=json.dumps({
            "state": result.state.value,
            "email": result.email,
        }))

    async def _read_page(self, args: dict) -> ToolResult:
        if self._open_session is None:
            return ToolResult(content=_NOT_OPEN_MSG, is_error=True)
        url = args.get("url")
        if url is not None and not isinstance(url, str):
            return ToolResult(content="'url' must be a string", is_error=True)
        try:
            view = await self._open_session.read_page(url or None)
        except Exception:
            logger.warning("[browser_session] read_page failed", exc_info=True)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
        text = view.text or ""
        truncated = len(text) > _MAX_TEXT_CHARS
        return ToolResult(content=json.dumps({
            "url": view.url,
            "title": view.title,
            "text": text[:_MAX_TEXT_CHARS],
            "truncated": truncated,
        }))

    async def _fill_form(self, args: dict) -> ToolResult:
        if self._open_session is None:
            return ToolResult(content=_NOT_OPEN_MSG, is_error=True)
        fields = args.get("fields")
        if not isinstance(fields, list) or not fields:
            return ToolResult(content=_BLANK_FIELDS_MSG, is_error=True)
        pairs: list[tuple[str, str]] = []
        for f in fields:
            if not isinstance(f, dict):
                return ToolResult(content=_BLANK_FIELDS_MSG, is_error=True)
            selector = f.get("selector")
            value = f.get("value")
            if not isinstance(selector, str) or not selector:
                return ToolResult(content=_BLANK_FIELDS_MSG, is_error=True)
            if not isinstance(value, str):
                return ToolResult(content=_BLANK_FIELDS_MSG, is_error=True)
            pairs.append((selector, value))
        try:
            await self._open_session.fill_fields(pairs)
        except Exception:
            logger.warning("[browser_session] fill_form failed", exc_info=True)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
        return ToolResult(content=json.dumps({"filled": len(pairs)}))

    async def _click(self, args: dict) -> ToolResult:
        if self._open_session is None:
            return ToolResult(content=_NOT_OPEN_MSG, is_error=True)
        selector = args.get("selector")
        if not isinstance(selector, str) or not selector:
            return ToolResult(content=_BLANK_SELECTOR_MSG, is_error=True)
        try:
            url = await self._open_session.click(selector)
        except Exception:
            logger.warning("[browser_session] click failed", exc_info=True)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
        return ToolResult(content=json.dumps({"url": url}))


def create() -> BrowserSessionPlugin:
    return BrowserSessionPlugin()
