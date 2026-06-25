"""
BrowserSession orchestration tests — logged-in session manager for the
browser-automation harness (ADR-0005 amendment 2026-06-25).

A FakeDriver stands in for Playwright so every login branch is exercised
without a real browser:
  - REUSED            : on-disk session still valid; no credentials touched
  - MANUAL            : attended human login completes / times out
  - REAUTHENTICATED   : unattended password fallback succeeds / fails
  - FAILED            : missing email or password short-circuits, fail-closed

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

    def __init__(self, *, logged_in=False, manual_ok=False, password_ok=False):
        self._logged_in = logged_in
        self._manual_ok = manual_ok
        self._password_ok = password_ok
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


# ── REAUTHENTICATED / FAILED (unattended) ───────────────────────────────────────

async def test_unattended_password_relogin_succeeds(tmp_path):
    store = _store()
    _seed_creds(store, email="bot@gmail.com", password="hunter2")
    driver = FakeDriver(logged_in=False, password_ok=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)

    assert result.state is LoginState.REAUTHENTICATED
    assert result.ok
    assert driver.password_seen == ("bot@gmail.com", "hunter2")
    assert driver.opened_headless is True  # unattended runs headless


async def test_unattended_password_relogin_failure_reports_botwall(tmp_path):
    store = _store()
    _seed_creds(store, password="wrong")
    driver = FakeDriver(logged_in=False, password_ok=False)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)
    assert result.state is LoginState.FAILED
    assert "2FA" in result.reason or "bot-wall" in result.reason


async def test_unattended_without_stored_password_fails_closed(tmp_path):
    store = _store()
    _seed_creds(store, email="bot@gmail.com", password=None)  # email only
    driver = FakeDriver(logged_in=False, password_ok=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)
    assert result.state is LoginState.FAILED
    assert "password" in result.reason
    # Must not have attempted a login with an empty password.
    assert "login_with_password" not in driver.calls


async def test_unattended_without_email_fails_closed(tmp_path):
    store = _store()
    _seed_creds(store, email=None, password="pw")  # password only, no metadata
    driver = FakeDriver(logged_in=False, password_ok=True)
    sess = _session(driver, store, tmp_path)

    result = await sess.ensure_logged_in(unattended=True)
    assert result.state is LoginState.FAILED
    assert "email" in result.reason
    assert "login_with_password" not in driver.calls


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
