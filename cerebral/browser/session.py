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
_DATA_ROOT = Path(__file__).parent.parent / "data" / "browser"

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
    FAILED = "failed"                  # could not establish a session


@dataclass
class LoginResult:
    state: LoginState
    email: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state is not LoginState.FAILED


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

    async def login_with_password(self, email: str, password: str) -> bool:
        """Drive the provider login form. Return True iff it ended logged in."""
        ...

    async def wait_for_manual_login(self, *, timeout: float) -> bool:
        """Poll until logged in or ``timeout`` seconds elapse. Return success."""
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

        if await self._driver.is_logged_in():
            logger.info(
                "[browser] session reused profile=%d provider=%s",
                self.profile_id, self.provider,
            )
            return LoginResult(state=LoginState.REUSED, email=email)

        if unattended:
            return await self._login_unattended(email)
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

    async def is_logged_in(self) -> bool:
        assert self._page is not None, "open() must be called first"
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        await self._page.goto(_ACCOUNT_URL, wait_until="domcontentloaded")
        # An UNauthenticated visit gets bounced OFF the myaccount host (to a
        # public landing page or the signin host); that bounce can land after
        # domcontentloaded, so wait briefly for the URL to leave myaccount. If
        # it never leaves, the session is authenticated.
        try:
            await self._page.wait_for_url(
                lambda url: _ACCOUNT_HOST not in url, timeout=self._settle_ms
            )
        except PlaywrightTimeoutError:
            pass
        return _ACCOUNT_HOST in self._page.url

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

        await self._page.goto(_LOGIN_URL, wait_until="domcontentloaded")
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self.is_logged_in():
                return True
            await asyncio.sleep(self._poll_interval)
        return False

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
