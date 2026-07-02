"""
BrowserSession orchestration tests — logged-in session manager for the
browser-automation harness (ADR-0005 amendment 2026-06-25).

A FakeDriver stands in for Playwright so every login branch is exercised
without a real browser:
  - REUSED             : on-disk session still valid; no credentials touched
  - MANUAL             : attended human login completes / times out
  - NEEDS_VERIFICATION : unattended reuse failed (dead session or step-up wall)
                         -> escalate to a human; the password is never read

The real PlaywrightDriver's Google specifics are intentionally NOT unit-tested
(selectors / bot-wall behaviour are a live-verification concern).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cerebral.browser.session import (  # noqa: E402
    BrowserSession,
    LoginState,
    PageView,
    PlaywrightDriver,
    is_verification_wall,
    _ACCOUNT_URL,
    _LOGIN_URL,
    _ACCOUNT_HOST,
)
from cerebral.db.credentials import CredentialStore  # noqa: E402


# ── dict-backed keyring stub (duck-types keyring.get/set/delete_password) ──────

class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


def _store() -> CredentialStore:
    return CredentialStore(db_path=":memory:", keyring_backend=FakeKeyring())


def _seed_creds(store, *, email="bot@gmail.com", password="secret-pw",
                provider="google_web", profile_id=1):
    if email:
        store.set_credential(profile_id, provider, email=email, status="connected")
    if password:
        store.set_secret(profile_id, provider, "password", password)


# ── FakeDriver ────────────────────────────────────────────────────────────────

class FakeDriver:
    """Records calls; returns configured login-state signals."""

    def __init__(self, *, logged_in=False, manual_ok=False, password_ok=False,
                 needs_verification=False):
        self._logged_in = logged_in
        self._manual_ok = manual_ok
        self._password_ok = password_ok
        self._needs_verification = needs_verification
        self.calls: list[str] = []
        self.opened_headless: bool | None = None
        self.opened_dir: Path | None = None
        self.password_seen: tuple[str, str] | None = None
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.last_url: str | None = None

    async def open(self, user_data_dir, *, headless):
        self.calls.append("open")
        self.opened_dir = user_data_dir
        self.opened_headless = headless

    async def is_logged_in(self):
        self.calls.append("is_logged_in")
        return self._logged_in

    async def needs_verification(self):
        self.calls.append("needs_verification")
        return self._needs_verification

    async def login_with_password(self, email, password):
        self.calls.append("login_with_password")
        self.password_seen = (email, password)
        self._logged_in = self._password_ok
        return self._password_ok

    async def wait_for_manual_login(self, *, timeout):
        self.calls.append("wait_for_manual_login")
        self._logged_in = self._manual_ok
        return self._manual_ok

    async def goto(self, url):
        self.calls.append("goto")
        self.last_url = url
        self._url = url
        return url

    async def current_page(self):
        self.calls.append("current_page")
        return PageView(url=getattr(self, "_url", "about:blank"),
                        title="Fake Title", text="fake body text")

    async def fill(self, selector, value):
        self.calls.append("fill")
        self.filled.append((selector, value))

    async def click(self, selector):
        self.calls.append("click")
        self.clicked.append(selector)
        return getattr(self, "_url", "about:blank")

    async def close(self):
        self.calls.append("close")


def _session(driver, store, tmp_path, **kw):
    return BrowserSession(
        1, driver=driver, store=store, data_root=tmp_path, **kw
    )


# ── REUSED ────────────────────────────────────────────────────────────────────

async def test_reuses_valid_session_without_touching_credentials(tmp_path):
    store = _store()
    _seed_creds(store)
    driver = FakeDriver(logged_in=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)

    assert result.state is LoginState.REUSED
    assert result.ok
    assert result.email == "bot@gmail.com"
    # Reuse must NOT drive any login path.
    assert "login_with_password" not in driver.calls
    assert "wait_for_manual_login" not in driver.calls
    assert driver.password_seen is None


# ── MANUAL (attended) ───────────────────────────────────────────────────────────

async def test_attended_waits_for_manual_login_and_never_reads_password(tmp_path):
    store = _store()
    _seed_creds(store)
    driver = FakeDriver(logged_in=False, manual_ok=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=False)

    assert result.state is LoginState.MANUAL
    assert result.ok
    assert "wait_for_manual_login" in driver.calls
    # The attended path must never read or use the stored password.
    assert "login_with_password" not in driver.calls
    assert driver.password_seen is None


async def test_attended_opens_visible_window(tmp_path):
    store = _store()
    _seed_creds(store)
    driver = FakeDriver(logged_in=False, manual_ok=True)
    sess = _session(driver, store, tmp_path)

    await sess.ensure_logged_in(unattended=False)
    assert driver.opened_headless is False  # visible so the human can log in


async def test_attended_timeout_fails(tmp_path):
    store = _store()
    _seed_creds(store)
    driver = FakeDriver(logged_in=False, manual_ok=False)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=False)
    assert result.state is LoginState.FAILED
    assert not result.ok
    assert "timeout" in result.reason


# ── NEEDS_VERIFICATION (unattended reuse-failure escalates to a human) ───────────
# Google can't be re-logged unattended (headless password login trips its bot
# wall), so ANY unattended reuse failure -- a dead session OR a step-up wall --
# returns NEEDS_VERIFICATION for the plugin to escalate to a visible attended
# window. The stored password is never read on an unattended run.

async def test_unattended_dead_session_needs_verification(tmp_path):
    store = _store()
    _seed_creds(store, email="bot@gmail.com", password="hunter2")
    driver = FakeDriver(logged_in=False)  # dead session, no wall detected
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)

    assert result.state is LoginState.NEEDS_VERIFICATION
    assert not result.ok
    assert driver.opened_headless is True  # the reuse probe runs headless
    # The stored password must never be touched on an unattended run.
    assert "login_with_password" not in driver.calls
    assert driver.password_seen is None


async def test_unattended_never_reads_password_even_without_creds(tmp_path):
    # No stored creds at all still just escalates -- it no longer fails-closed
    # on a password read, because no password read happens.
    store = _store()
    driver = FakeDriver(logged_in=False)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)
    assert result.state is LoginState.NEEDS_VERIFICATION
    assert "login_with_password" not in driver.calls
    assert driver.password_seen is None


# ── paths + lifecycle ───────────────────────────────────────────────────────────

async def test_user_data_dir_is_profile_and_provider_scoped(tmp_path):
    store = _store()
    driver = FakeDriver(logged_in=True)
    sess = BrowserSession(
        7, provider="google_web", driver=driver, store=store, data_root=tmp_path,
    )
    assert sess.user_data_dir == tmp_path / "profile_7" / "google_web"


async def test_ensure_logged_in_creates_the_data_dir(tmp_path):
    store = _store()
    _seed_creds(store)
    driver = FakeDriver(logged_in=True)
    sess = _session(driver, store, tmp_path)

    assert not sess.user_data_dir.exists()
    await sess.ensure_logged_in()
    assert sess.user_data_dir.exists()


async def test_close_delegates_to_driver(tmp_path):
    store = _store()
    driver = FakeDriver(logged_in=True)
    sess = _session(driver, store, tmp_path)
    await sess.close()
    assert "close" in driver.calls


# ── page driving (S1 — the in-session MCP tool primitives) ──────────────────────

async def test_read_page_with_url_navigates_then_snapshots(tmp_path):
    store = _store()
    driver = FakeDriver(logged_in=True)
    sess = _session(driver, store, tmp_path)

    view = await sess.read_page("https://example.com/x")

    assert driver.calls == ["goto", "current_page"]
    assert driver.last_url == "https://example.com/x"
    assert isinstance(view, PageView)
    assert view.url == "https://example.com/x"
    assert view.title == "Fake Title"
    assert view.text == "fake body text"


async def test_read_page_without_url_snapshots_current_only(tmp_path):
    store = _store()
    driver = FakeDriver(logged_in=True)
    sess = _session(driver, store, tmp_path)

    await sess.read_page()

    # No navigation when no url is given.
    assert "goto" not in driver.calls
    assert driver.calls == ["current_page"]


async def test_fill_fields_fills_each_in_order(tmp_path):
    store = _store()
    driver = FakeDriver(logged_in=True)
    sess = _session(driver, store, tmp_path)

    await sess.fill_fields([("#email", "a@b.c"), ("#q", "hello")])

    assert driver.filled == [("#email", "a@b.c"), ("#q", "hello")]
    assert driver.calls == ["fill", "fill"]


async def test_click_returns_resulting_url(tmp_path):
    store = _store()
    driver = FakeDriver(logged_in=True)
    sess = _session(driver, store, tmp_path)
    await driver.goto("https://example.com/after")

    url = await sess.click("#submit")

    assert driver.clicked == ["#submit"]
    assert url == "https://example.com/after"


# ── PlaywrightDriver.wait_for_manual_login — non-destructive poll ────────────────
# Regression for the "redirects to an about/help page mid-login" bug: the poll
# must NOT navigate the user's sign-in page; it probes login state on a separate
# page in the same (cookie-sharing) context.

class _FakePwPage:
    """Minimal stand-in for a Playwright page for the manual-login poll."""

    def __init__(self, *, login_after=0):
        self.url = "about:blank"
        self.goto_calls: list[str] = []
        self.closed = False
        self._account_visits = 0
        self._login_after = login_after

    async def goto(self, url, wait_until=None):
        self.goto_calls.append(url)
        if url == _ACCOUNT_URL:
            self._account_visits += 1
            # Becomes "logged in" (stays on myaccount) only after N visits.
            self.url = (_ACCOUNT_URL if self._account_visits > self._login_after
                        else "https://www.google.com/account/about/")
        else:
            self.url = url

    async def wait_for_url(self, predicate, timeout=None):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        if predicate(self.url):
            return
        raise PlaywrightTimeoutError("stayed on myaccount")

    async def bring_to_front(self):
        self.brought_to_front = getattr(self, "brought_to_front", 0) + 1

    async def close(self):
        self.closed = True


class _FakePwContext:
    def __init__(self, probe):
        self._probe = probe
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        return self._probe


async def test_manual_login_polls_probe_page_not_user_page():
    drv = PlaywrightDriver(poll_interval=0.001, settle_ms=5)
    user = _FakePwPage()
    probe = _FakePwPage(login_after=1)   # logs in on the 2nd myaccount visit
    drv._page = user
    drv._context = _FakePwContext(probe)

    ok = await drv.wait_for_manual_login(timeout=5)

    assert ok is True
    # The user's page is navigated exactly once — to the sign-in form — and
    # NEVER to the myaccount URL that caused the mid-login bounce.
    assert user.goto_calls == [_LOGIN_URL]
    assert _ACCOUNT_URL not in user.goto_calls
    # The probe page is the one doing the myaccount polling, then is closed.
    assert _ACCOUNT_URL in probe.goto_calls
    assert probe.closed is True
    # The sign-in tab is kept in front so the probe never steals focus.
    assert getattr(user, "brought_to_front", 0) >= 1


async def test_manual_login_times_out_without_touching_user_page():
    drv = PlaywrightDriver(poll_interval=0.001, settle_ms=5)
    user = _FakePwPage()
    probe = _FakePwPage(login_after=10_000)  # never logs in
    drv._page = user
    drv._context = _FakePwContext(probe)

    ok = await drv.wait_for_manual_login(timeout=0.05)

    assert ok is False
    assert user.goto_calls == [_LOGIN_URL]
    assert probe.closed is True


# ── S5: human-verification wall detection ───────────────────────────────────────

def test_is_verification_wall_matches_confirmidentifier_url():
    assert is_verification_wall(
        "https://accounts.google.com/v3/signin/confirmidentifier?authuser=0", ""
    )


def test_is_verification_wall_matches_challenge_url():
    assert is_verification_wall("https://accounts.google.com/signin/v2/challenge/pwd", "")


def test_is_verification_wall_matches_verify_text_straight_and_curly():
    assert is_verification_wall("https://mail.google.com/", "Verify it's you to continue")
    assert is_verification_wall("https://mail.google.com/", "Verify it’s you to continue")


def test_is_verification_wall_matches_couldnt_sign_in_text():
    assert is_verification_wall("https://x/", "Couldn't sign you in right now")


def test_is_verification_wall_false_on_normal_page():
    assert not is_verification_wall("https://myaccount.google.com/", "Welcome, Felix")
    assert not is_verification_wall("https://www.youtube.com/", "Home - YouTube")


def test_is_verification_wall_tolerates_empty():
    assert not is_verification_wall("", "")
    assert not is_verification_wall(None, None)


async def test_session_needs_verification_delegates_to_driver(tmp_path):
    store = _store()
    driver = FakeDriver(logged_in=True, needs_verification=True)
    sess = _session(driver, store, tmp_path)
    assert await sess.needs_verification() is True
    assert "needs_verification" in driver.calls


async def test_unattended_verification_wall_short_circuits_without_password(tmp_path):
    store = _store()
    _seed_creds(store)  # email + password present
    driver = FakeDriver(logged_in=False, needs_verification=True, password_ok=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)

    assert result.state is LoginState.NEEDS_VERIFICATION
    assert not result.ok
    # A step-up wall can't be cleared by a password — must NOT attempt one.
    assert "login_with_password" not in driver.calls
    assert driver.password_seen is None


async def test_unattended_wall_wins_over_brief_logged_in(tmp_path):
    # The live race: is_logged_in briefly True (myaccount loaded) but a step-up
    # redirect lands right after, so needs_verification is also True. The wall
    # must win — NEEDS_VERIFICATION, not a premature REUSED.
    store = _store()
    _seed_creds(store)
    driver = FakeDriver(logged_in=True, needs_verification=True, password_ok=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)

    assert result.state is LoginState.NEEDS_VERIFICATION
    assert "login_with_password" not in driver.calls


async def test_attended_verification_wall_falls_through_to_manual(tmp_path):
    store = _store()
    _seed_creds(store)
    # Wall present, but attended: the human at the keyboard clears it via the
    # manual-login wait.
    driver = FakeDriver(logged_in=False, needs_verification=True, manual_ok=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=False)

    assert result.state is LoginState.MANUAL
    assert "wait_for_manual_login" in driver.calls
