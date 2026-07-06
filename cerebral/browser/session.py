"""
Logged-in browser session manager for the browser-automation harness.

Drives a **dedicated secondary web account** (provider ``google_web``, see the
2026-06-25 ADR-0005 amendment) inside a Playwright **persistent context**, so
the authenticated session (cookies / localStorage) survives across runs on
disk and the password is needed only when the session has actually expired.

Login strategy (``BrowserSession.ensure_logged_in``):

  1. Open the per-profile persistent context. If it is already authenticated
     (cookies still valid) -> reuse, no credentials touched.  [LoginState.REUSED]
  2. Not authenticated, *attended* run (``unattended=False``): open a VISIBLE
     window on the provider's login page and wait for the human to complete
     login + 2FA by hand. The persistent context captures the session.
     The password is never read.                              [LoginState.MANUAL]
  3. Not authenticated, *unattended* run (``unattended=True``): read the stored
     password from the keyring (``secrets_read``) and drive the login form.
     This is the fragile fallback the ADR amendment admits the password for.
     [LoginState.REAUTHENTICATED]  /  [LoginState.FAILED]

All Playwright side-effects are injected via a ``BrowserDriver`` so the
orchestration above is unit-testable without a real browser. The real
Playwright + Google specifics live in ``PlaywrightDriver`` (this module),
which is exercised by live verification, not unit tests — Google's login
selectors and bot-wall behaviour cannot be asserted offline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from cerebral.db.credentials import CredentialStore

logger = logging.getLogger(__name__)

# Persistent-context profiles live under cerebral/data/ which is already
# gitignored — the on-disk session (cookies) must never be committed.
from cerebral.paths import data_dir

_DATA_ROOT = data_dir() / "browser"

# The dedicated browser-automation provider (distinct from the OAuth
# Workspace "google" provider) per the 2026-06-25 ADR-0005 amendment.
DEFAULT_PROVIDER = "google_web"

# Default seconds to wait for a human to finish an attended login.
DEFAULT_MANUAL_LOGIN_TIMEOUT = 180.0


class LoginState(str, Enum):
    """Outcome of ``ensure_logged_in``."""

    REUSED = "reused"                  # session on disk was still valid
    MANUAL = "manual"                  # human completed an attended login
    REAUTHENTICATED = "reauthenticated"  # password fallback succeeded
    NEEDS_VERIFICATION = "needs_verification"  # logged in, but a human step-up
                                       # wall ("verify it's you") blocks reuse
    FAILED = "failed"                  # could not establish a session


@dataclass
class LoginResult:
    state: LoginState
    email: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        # Both FAILED and NEEDS_VERIFICATION lack a usable, drivable session.
        return self.state not in (
            LoginState.FAILED, LoginState.NEEDS_VERIFICATION,
        )


@dataclass
class PageView:
    """A read-only snapshot of the current page for the page-driving tools."""

    url: str = ""
    title: str = ""
    text: str = ""


@runtime_checkable
class BrowserDriver(Protocol):
    """The browser side-effects ``BrowserSession`` orchestrates.

    The real implementation (``PlaywrightDriver``) carries every Google /
    Playwright specific; a fake implementation makes the orchestration above
    fully unit-testable. All methods are async.
    """

    async def open(self, user_data_dir: Path, *, headless: bool) -> None:
        """Launch (or attach to) the persistent context at ``user_data_dir``."""
        ...

    async def is_logged_in(self) -> bool:
        """True iff the current session is authenticated to the provider."""
        ...

    async def needs_verification(self) -> bool:
        """True iff the current page is a human-verification (step-up) wall."""
        ...

    async def login_with_password(self, email: str, password: str) -> bool:
        """Drive the provider login form. Return True iff it ended logged in."""
        ...

    async def wait_for_manual_login(self, *, timeout: float) -> bool:
        """Poll until logged in or ``timeout`` seconds elapse. Return success."""
        ...

    # ── page driving (in-session MCP tools) ──────────────────────────────────
    # These operate on the already-open authenticated context. They carry no
    # credential logic — the session must already be logged in.

    async def goto(self, url: str) -> str:
        """Navigate to ``url``; return the final URL after any redirect."""
        ...

    async def current_page(self) -> "PageView":
        """Snapshot the current page (url / title / visible text)."""
        ...

    async def fill(self, selector: str, value: str) -> None:
        """Type ``value`` into the element matched by ``selector``."""
        ...

    async def click(self, selector: str) -> str:
        """Click the element matched by ``selector``; return the resulting URL."""
        ...

    async def upload_file(self, selector: str, file_path: str) -> None:
        """Set ``file_path`` on the file input matched by ``selector``."""
        ...

    async def close(self) -> None:
        """Tear down the context, flushing the session to disk."""
        ...


class BrowserSession:
    """Credential-aware logged-in session manager for one (profile, provider).

    The ``driver`` and ``store`` are injectable seams; production passes a
    ``PlaywrightDriver`` and a real ``CredentialStore``.
    """

    def __init__(
        self,
        profile_id: int,
        *,
        provider: str = DEFAULT_PROVIDER,
        driver: BrowserDriver,
        store: CredentialStore | None = None,
        data_root: Path = _DATA_ROOT,
        manual_login_timeout: float = DEFAULT_MANUAL_LOGIN_TIMEOUT,
    ) -> None:
        self.profile_id = profile_id
        self.provider = provider
        self._driver = driver
        self._store = store or CredentialStore()
        self._data_root = data_root
        self._manual_login_timeout = manual_login_timeout

    @property
    def user_data_dir(self) -> Path:
        """Per-(profile, provider) persistent-context directory."""
        return self._data_root / f"profile_{self.profile_id}" / self.provider

    # ── credential resolution ────────────────────────────────────────────────

    def _email(self) -> str:
        cred = self._store.get_credential(self.profile_id, self.provider)
        return (cred or {}).get("email", "")

    def _password(self) -> str | None:
        # secrets_read — keyring-backed; returns None when unset or keyring
        # is unavailable (the env-fallback / fail-closed path from #157).
        return self._store.get_secret(self.profile_id, self.provider, "password")

    # ── orchestration ────────────────────────────────────────────────────────

    async def ensure_logged_in(self, *, unattended: bool = False) -> LoginResult:
        """Guarantee an authenticated session, reusing the on-disk one if valid.

        ``unattended=False`` (default): a human completes any needed login by
        hand in a visible window — the password is never read.
        ``unattended=True``: fall back to the stored password to re-login with
        nobody at the keyboard.
        """
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        email = self._email()

        # Attended runs open a visible window so the human can act; unattended
        # runs go headless.
        await self._driver.open(self.user_data_dir, headless=unattended)

        reused = await self._driver.is_logged_in()
        # A "verify it's you" step-up can redirect in JUST AFTER myaccount
        # loads, so a brief is_logged_in=True is not conclusive — check for a
        # wall too. A wall means the session is NOT actually reusable: a
        # password cannot clear a step-up, so on an UNATTENDED run surface it as
        # NEEDS_VERIFICATION for the caller to escalate to a human rather than
        # burning a doomed (and bot-wall-tripping) password attempt. On an
        # ATTENDED run fall through — the human at the keyboard clears it.
        wall = await self._driver.needs_verification()

        if reused and not wall:
            logger.info(
                "[browser] session reused profile=%d provider=%s",
                self.profile_id, self.provider,
            )
            return LoginResult(state=LoginState.REUSED, email=email)

        # Reuse failed: the on-disk session is dead and/or Google is showing a
        # "verify it's you" step-up wall. Either way a Google account cannot be
        # re-logged unattended -- a headless stored-password login trips its bot
        # wall (live-verified 2026-06-25). So rather than burn a doomed password
        # attempt, signal that a human must finish the sign-in: the
        # browser_session plugin escalates to an OS notification + a VISIBLE
        # attended window. ADR-0005's stored-password fallback is retained in
        # _login_unattended but is no longer auto-driven.
        # ponytail: password path kept dormant, not deleted; removing it
        # cascades into keyring + tray UI + ADR-0005 -- a deliberate follow-up.
        if unattended:
            logger.info(
                "[browser] session needs human sign-in profile=%d provider=%s "
                "(wall=%s)", self.profile_id, self.provider, wall,
            )
            return LoginResult(
                state=LoginState.NEEDS_VERIFICATION, email=email,
                reason="human sign-in required",
            )
        return await self._login_attended(email)

    async def _login_attended(self, email: str) -> LoginResult:
        logger.info(
            "[browser] awaiting manual login profile=%d provider=%s (%.0fs)",
            self.profile_id, self.provider, self._manual_login_timeout,
        )
        ok = await self._driver.wait_for_manual_login(
            timeout=self._manual_login_timeout
        )
        if ok:
            return LoginResult(state=LoginState.MANUAL, email=email)
        return LoginResult(
            state=LoginState.FAILED, email=email,
            reason="manual login not completed within timeout",
        )

    async def _login_unattended(self, email: str) -> LoginResult:
        # DORMANT (2026-07-02): no longer called by ensure_logged_in -- Google
        # can't be re-logged unattended (bot wall), so reuse-failure escalates
        # to an attended window instead. Retained pending an ADR-0005 revisit on
        # whether to drop stored-password support entirely.
        password = self._password()
        if not email or not password:
            # Fail closed rather than half-driving a login with no creds.
            missing = "email" if not email else "password"
            logger.warning(
                "[browser] unattended login blocked profile=%d provider=%s: "
                "no stored %s", self.profile_id, self.provider, missing,
            )
            return LoginResult(
                state=LoginState.FAILED, email=email,
                reason=f"no stored {missing} for unattended login",
            )
        # password is never logged.
        ok = await self._driver.login_with_password(email, password)
        if ok:
            logger.info(
                "[browser] unattended re-login succeeded profile=%d provider=%s",
                self.profile_id, self.provider,
            )
            return LoginResult(state=LoginState.REAUTHENTICATED, email=email)
        logger.warning(
            "[browser] unattended re-login failed profile=%d provider=%s",
            self.profile_id, self.provider,
        )
        return LoginResult(
            state=LoginState.FAILED, email=email,
            reason="password login did not end authenticated "
                   "(2FA / bot-wall / wrong password)",
        )

    # ── page driving ─────────────────────────────────────────────────────────
    # Thin pass-throughs to the driver so the page-driving MCP tools stay
    # unit-testable against a fake driver (same seam the login orchestration
    # uses). Callers must have established a session via ensure_logged_in.

    async def read_page(self, url: str | None = None) -> PageView:
        """Optionally navigate to ``url``, then snapshot the current page."""
        if url:
            await self._driver.goto(url)
        return await self._driver.current_page()

    async def fill_fields(self, fields: list[tuple[str, str]]) -> None:
        """Fill each ``(selector, value)`` in order on the current page."""
        for selector, value in fields:
            await self._driver.fill(selector, value)

    async def click(self, selector: str) -> str:
        """Click ``selector``; return the resulting URL."""
        return await self._driver.click(selector)

    async def upload_file(self, selector: str, file_path: str) -> None:
        """Set ``file_path`` on the file input matched by ``selector``."""
        await self._driver.upload_file(selector, file_path)

    async def needs_verification(self) -> bool:
        """True iff the current page is a human-verification (step-up) wall."""
        return await self._driver.needs_verification()

    async def close(self) -> None:
        await self._driver.close()


# ───────────────────────────────────────────────────────────────────────────
# Real Playwright driver — Google specifics. NOT unit-tested: selectors and
# bot-wall behaviour are asserted by live verification only. Kept thin so the
# orchestration above carries the testable logic.
# ───────────────────────────────────────────────────────────────────────────

# Logged-in check: an UNauthenticated visit to myaccount.google.com is bounced
# away — to the public landing page (www.google.com/account/about) or the
# signin host — so *staying on the myaccount host* is the authoritative signal
# that the session is authenticated. (The naive "signin host not in url" check
# was a false positive: the unauth bounce lands on www.google.com, not the
# signin host — caught in live verification 2026-06-25.)
_ACCOUNT_URL = "https://myaccount.google.com/"
_ACCOUNT_HOST = "myaccount.google.com"
_LOGIN_URL = "https://accounts.google.com/signin/v2/identifier"

# Human-verification ("verify it's you" / step-up) wall detection. When an
# otherwise-authenticated session hits one of these, automation cannot proceed
# without a human — the escalation path (pause + notify) keys off this.
_VERIFICATION_URL_MARKERS = (
    "confirmidentifier",
    "/signin/v2/challenge",
    "/signin/challenge",
    "/challenge/",
    "/signin/rejected",
    "/deniedsigninrejected",
)
_VERIFICATION_TEXT_MARKERS = (
    "verify it's you",
    "verify it’s you",      # curly apostrophe
    "confirm it's you",
    "confirm it’s you",
    "verify your identity",
    "couldn't sign you in",
    "couldn’t sign you in",
)


def is_verification_wall(url: str, text: str) -> bool:
    """True iff ``url``/``text`` look like a Google human-verification wall.

    Pure (no browser) so the heuristic is unit-testable; ``PlaywrightDriver.
    needs_verification`` feeds it the live page's URL + body text."""
    u = (url or "").lower()
    if any(marker in u for marker in _VERIFICATION_URL_MARKERS):
        return True
    t = (text or "").lower()
    return any(marker in t for marker in _VERIFICATION_TEXT_MARKERS)


class PlaywrightDriver:
    """``BrowserDriver`` backed by a Playwright persistent context + Chromium.

    Reads/writes the on-disk session at ``user_data_dir``. The Google login
    selectors here are best-effort and the live-verification target — Google
    changes them and may show the "this browser may not be secure" wall on a
    headless/automated password login, which is exactly why attended manual
    login is the default path.
    """

    def __init__(self, *, poll_interval: float = 2.0, settle_ms: int = 5000) -> None:
        self._pw = None
        self._context = None
        self._page = None
        self._poll_interval = poll_interval
        # How long is_logged_in waits for the unauthenticated redirect to the
        # signin host to land before concluding the session is authenticated.
        self._settle_ms = settle_ms

    async def open(self, user_data_dir: Path, *, headless: bool) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()

    async def _is_logged_in_on(self, page) -> bool:
        """Logged-in check that navigates ``page`` to myaccount and back.

        Factored out so the manual-login poll can run it on a SEPARATE probe
        page — running it on the user's login page would navigate them off the
        form mid-login (see ``wait_for_manual_login``)."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        await page.goto(_ACCOUNT_URL, wait_until="domcontentloaded")
        # An UNauthenticated visit gets bounced OFF the myaccount host (to a
        # public landing page or the signin host); that bounce can land after
        # domcontentloaded, so wait briefly for the URL to leave myaccount. If
        # it never leaves, the session is authenticated.
        try:
            await page.wait_for_url(
                lambda url: _ACCOUNT_HOST not in url, timeout=self._settle_ms
            )
        except PlaywrightTimeoutError:
            pass
        return _ACCOUNT_HOST in page.url

    async def is_logged_in(self) -> bool:
        assert self._page is not None, "open() must be called first"
        return await self._is_logged_in_on(self._page)

    async def needs_verification(self) -> bool:
        assert self._page is not None, "open() must be called first"
        try:
            text = await self._page.inner_text("body")
        except Exception:
            text = ""
        return is_verification_wall(self._page.url, text)

    async def login_with_password(self, email: str, password: str) -> bool:
        assert self._page is not None, "open() must be called first"
        page = self._page
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        await page.fill('input[type="email"]', email)
        await page.click('#identifierNext, button:has-text("Next")')
        await page.wait_for_selector('input[type="password"]', timeout=15000)
        await page.fill('input[type="password"]', password)
        await page.click('#passwordNext, button:has-text("Next")')
        # Give any redirect / 2FA challenge a moment to settle, then re-check.
        await page.wait_for_load_state("networkidle")
        return await self.is_logged_in()

    async def wait_for_manual_login(self, *, timeout: float) -> bool:
        import asyncio

        assert self._page is not None and self._context is not None, (
            "open() must be called first"
        )
        # Show the human the sign-in form ONCE, then NEVER navigate this page
        # again. Polling is_logged_in() on it would call page.goto(myaccount)
        # every poll, which — while the user is still signing in — bounces to
        # the public account/about page and yanks them off the form, making
        # login impossible. Poll on a SEPARATE probe page instead; the
        # persistent context shares cookies, so the probe sees the login the
        # instant it completes without touching the user's page.
        await self._page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        probe = await self._context.new_page()
        # Keep the sign-in tab in front so the background probe never steals
        # focus while the human is typing credentials / a 2FA code.
        await self._page.bring_to_front()
        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                logged_in = await self._is_logged_in_on(probe)
                await self._page.bring_to_front()
                if logged_in:
                    return True
                await asyncio.sleep(self._poll_interval)
            return False
        finally:
            await probe.close()

    async def goto(self, url: str) -> str:
        assert self._page is not None, "open() must be called first"
        await self._page.goto(url, wait_until="domcontentloaded")
        return self._page.url

    async def current_page(self) -> PageView:
        assert self._page is not None, "open() must be called first"
        title = await self._page.title()
        try:
            # Visible body text — the page-driving tools want readable content,
            # not raw HTML. inner_text skips script/style and hidden nodes.
            text = await self._page.inner_text("body")
        except Exception:
            text = ""
        return PageView(url=self._page.url, title=title, text=text)

    async def fill(self, selector: str, value: str) -> None:
        assert self._page is not None, "open() must be called first"
        await self._page.fill(selector, value)

    async def click(self, selector: str) -> str:
        assert self._page is not None, "open() must be called first"
        await self._page.click(selector)
        # Let any navigation the click triggered settle before reporting URL.
        await self._page.wait_for_load_state("domcontentloaded")
        return self._page.url

    async def upload_file(self, selector: str, file_path: str) -> None:
        assert self._page is not None, "open() must be called first"
        await self._page.set_input_files(selector, file_path)

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
