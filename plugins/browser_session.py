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
from typing import Awaitable, Callable, Optional

from cerebral.browser import BrowserSession, LoginState
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
Notifier = Callable[[str, str], Awaitable[None]]
PauseCheck = Callable[[], bool]

# Module-level seams set by cerebral/main.py at startup. Tests bypass these by
# passing them directly to the plugin constructor.
_session_factory: Optional[SessionFactory] = None
_notifier: Optional[Notifier] = None
_pause_check: Optional[PauseCheck] = None
# Singleton plugin instance set by create(); lets other plugins (job_search)
# read the currently-open session without an orchestrator reference.
_plugin_instance: Optional["BrowserSessionPlugin"] = None


def get_open_session() -> Optional["BrowserSession"]:
    """Return the plugin's currently-open BrowserSession, or None."""
    return _plugin_instance._open_session if _plugin_instance else None


def set_session_factory(fn: SessionFactory) -> None:
    """Wire main.py's ``_get_browser_session()`` — called once at startup.

    The factory must return ``BrowserSession | None``: ``None`` when no profile
    is loaded, a (not-yet-opened) ``BrowserSession`` for the active profile
    otherwise. It re-resolves each call so a profile switch is picked up by the
    next browser_open_session.
    """
    global _session_factory
    _session_factory = fn


def set_notifier(fn: Notifier) -> None:
    """Wire main.py's ``_notify_user`` so the verification escalation can raise
    an OS notification asking the user to clear a "verify it's you" wall."""
    global _notifier
    _notifier = fn


def set_pause_on_verification(fn: PauseCheck) -> None:
    """Wire a getter for the ``browser_pause_on_verification`` setting. When it
    returns True (the default), a verification wall escalates to a visible
    attended window; when False, the tool just returns the seed-hint error."""
    global _pause_check
    _pause_check = fn


class BrowserSessionPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        session_factory: Optional[SessionFactory] = None,
        *,
        notifier: Optional[Notifier] = None,
        pause_check: Optional[PauseCheck] = None,
    ) -> None:
        # Constructor-injected seams win over the module-level setters (tests
        # pass directly; production leaves these None and main.py wires them).
        self._factory = session_factory
        self._notifier = notifier
        self._pause_check = pause_check
        # The currently-open authenticated session, or None. Holds the live
        # Playwright context across read/fill/click calls.
        self._open_session: Optional[BrowserSession] = None

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="upload_file",
                description=(
                    "Upload a local file to a file input on the current page. "
                    "Provide the CSS selector for the file input and the absolute "
                    "local path to the file. Requires an open session "
                    "(browser_open_session)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector for the file input element.",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Absolute local path to the file to upload.",
                        },
                    },
                    "required": ["selector", "file_path"],
                },
            ),
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
        if tool_name == "upload_file":
            return await self._upload_file(args)
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

    def _should_pause_on_verification(self) -> bool:
        """Whether to escalate a verification wall to a visible window. Defaults
        to True (the setting's default) when no seam is wired."""
        check = self._pause_check or _pause_check
        if check is None:
            return True
        try:
            return bool(check())
        except Exception:
            logger.warning("[browser_session] pause-check failed", exc_info=True)
            return True

    async def _notify(self, title: str, body: str) -> None:
        """Best-effort OS notification (no-op when no notifier is wired)."""
        notifier = self._notifier or _notifier
        if notifier is None:
            return
        try:
            await notifier(title, body)
        except Exception:
            logger.warning("[browser_session] notify failed", exc_info=True)

    @staticmethod
    async def _safe_close(session: BrowserSession) -> None:
        try:
            await session.close()
        except Exception:
            logger.warning("[browser_session] close failed", exc_info=True)

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
            await self._safe_close(self._open_session)
            self._open_session = None

        try:
            result = await session.ensure_logged_in(unattended=True)
        except Exception:
            logger.warning("[browser_session] open failed", exc_info=True)
            await self._safe_close(session)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)

        if result.ok:
            self._open_session = session
            return ToolResult(content=json.dumps({
                "state": result.state.value,
                "email": result.email,
            }))

        # Hit a human-verification ("verify it's you") wall? If the user opted
        # in (browser_pause_on_verification, default ON), escalate: notify them
        # and open a VISIBLE window so they can clear it by hand, then resume.
        # Cerebral runs in the user's interactive session, so it CAN open that
        # window (unlike the agent's headless subprocess).
        if (result.state is LoginState.NEEDS_VERIFICATION
                and self._should_pause_on_verification()):
            # Release the headless context's dir lock before re-opening headed.
            await self._safe_close(session)
            await self._notify(
                "Felix needs you to sign in to the browser account",
                f"The saved Google sign-in for "
                f"{result.email or 'the browser account'} needs you to finish "
                "it. Click to open the window and complete the sign-in.",
            )
            session2, err2 = self._resolve_session()
            if err2 is not None:
                return ToolResult(content=err2, is_error=True)
            try:
                # unattended=False opens a visible window + waits for the human.
                escalated = await session2.ensure_logged_in(unattended=False)
            except Exception:
                logger.warning("[browser_session] escalation failed", exc_info=True)
                await self._safe_close(session2)
                return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
            if escalated.ok:
                self._open_session = session2
                return ToolResult(content=json.dumps({
                    "state": escalated.state.value,
                    "email": escalated.email,
                    "verified": True,
                }))
            await self._safe_close(session2)
            return ToolResult(
                content="verification was not completed in the window — "
                        "open the browser login and finish 'verify it's you'.",
                is_error=True,
            )

        # NEEDS_VERIFICATION with pause OFF, or a plain FAILED.
        await self._safe_close(session)
        if result.state is LoginState.NEEDS_VERIFICATION:
            return ToolResult(
                content="human verification needed — finish 'verify it's you' "
                        "via the tray 'Log in now', then retry.",
                is_error=True,
            )
        return ToolResult(content=f"{_SEED_HINT}.", is_error=True)

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

    async def _upload_file(self, args: dict) -> ToolResult:
        if self._open_session is None:
            return ToolResult(content=_NOT_OPEN_MSG, is_error=True)
        selector = args.get("selector")
        file_path = args.get("file_path")
        if not isinstance(selector, str) or not selector:
            return ToolResult(content=_BLANK_SELECTOR_MSG, is_error=True)
        if not isinstance(file_path, str) or not file_path:
            return ToolResult(content="'file_path' is required", is_error=True)
        try:
            await self._open_session.upload_file(selector, file_path)
        except Exception:
            logger.warning("[browser_session] upload_file failed", exc_info=True)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
        return ToolResult(content=json.dumps({"uploaded": file_path}))


def create() -> BrowserSessionPlugin:
    global _plugin_instance
    _plugin_instance = BrowserSessionPlugin()
    return _plugin_instance
