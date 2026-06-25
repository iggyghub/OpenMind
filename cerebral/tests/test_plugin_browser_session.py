"""
browser_session plugin tests — in-session page driving (ADR-0005 amendment
2026-06-25).

A FakeSession stands in for BrowserSession so the plugin's orchestration
(open → read/fill/click, the not-open guards, the re-open-closes-prior rule,
error vocabulary) is exercised without a real Playwright context. The plugin
is constructed with a factory returning the fake, mirroring how main.py wires
set_session_factory in production.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.browser.session import LoginResult, LoginState, PageView  # noqa: E402

import importlib.util  # noqa: E402

# Load the plugin the same way discovery does (spec_from_file_location), so the
# module-level seam is the one the tests poke if needed.
_PLUGIN_PATH = Path(__file__).resolve().parent.parent.parent / "plugins" / "browser_session.py"
_spec = importlib.util.spec_from_file_location("openmind_plugin_browser_session", _PLUGIN_PATH)
bsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bsp)


# ── FakeSession ────────────────────────────────────────────────────────────────

class FakeSession:
    def __init__(self, *, login=None, page=None, click_url="https://x/after"):
        self._login = login or LoginResult(state=LoginState.REUSED,
                                            email="bot@gmail.com")
        self._page = page or PageView(url="https://x/", title="T", text="body")
        self._click_url = click_url
        self.calls: list[str] = []
        self.closed = 0
        self.read_url = None
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.unattended = None

    async def ensure_logged_in(self, *, unattended=False):
        self.calls.append("ensure_logged_in")
        self.unattended = unattended
        return self._login

    async def read_page(self, url=None):
        self.calls.append("read_page")
        self.read_url = url
        return self._page

    async def fill_fields(self, pairs):
        self.calls.append("fill_fields")
        self.filled.extend(pairs)

    async def click(self, selector):
        self.calls.append("click")
        self.clicked.append(selector)
        return self._click_url

    async def close(self):
        self.calls.append("close")
        self.closed += 1


def _plugin(*sessions):
    """Plugin whose factory yields the given sessions in order (one per
    browser_open_session call)."""
    queue = list(sessions)

    def factory():
        return queue.pop(0) if queue else None

    return bsp.BrowserSessionPlugin(session_factory=factory)


async def _open(plugin):
    return await plugin.call_tool("browser_open_session", {})


# ── declaration ────────────────────────────────────────────────────────────────

def test_required_capabilities():
    assert bsp.REQUIRED_CAPABILITIES == frozenset(
        {"secrets_read", "network_egress_cloud"}
    )


def test_lists_four_tools():
    names = {t.name for t in _plugin().list_tools()}
    assert names == {"browser_open_session", "read_page", "fill_form", "click"}
    for t in _plugin().list_tools():
        assert t.plugin == "browser_session"


# ── browser_open_session ────────────────────────────────────────────────────────

async def test_open_reuse_returns_state_and_email():
    sess = FakeSession(login=LoginResult(state=LoginState.REUSED,
                                         email="bot@gmail.com"))
    plugin = _plugin(sess)
    res = await _open(plugin)
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload == {"state": "reused", "email": "bot@gmail.com"}
    assert sess.unattended is True  # tool path never opens a visible window
    assert plugin._open_session is sess


async def test_open_reauthenticated_is_ok():
    sess = FakeSession(login=LoginResult(state=LoginState.REAUTHENTICATED,
                                         email="bot@gmail.com"))
    res = await _open(_plugin(sess))
    assert not res.is_error
    assert json.loads(res.content)["state"] == "reauthenticated"


async def test_open_failed_returns_seed_hint_and_does_not_store():
    sess = FakeSession(login=LoginResult(state=LoginState.FAILED,
                                         reason="bot-wall"))
    plugin = _plugin(sess)
    res = await _open(plugin)
    assert res.is_error
    assert "seed_browser_login" in res.content or "Log in now" in res.content
    assert plugin._open_session is None
    assert sess.closed == 1  # failed session is torn down


async def test_open_factory_not_wired():
    plugin = bsp.BrowserSessionPlugin(session_factory=None)
    # Ensure the module-level seam is also unset for this test.
    bsp._session_factory = None
    res = await plugin.call_tool("browser_open_session", {})
    assert res.is_error
    assert "factory not wired" in res.content


async def test_open_no_active_profile():
    plugin = bsp.BrowserSessionPlugin(session_factory=lambda: None)
    res = await plugin.call_tool("browser_open_session", {})
    assert res.is_error
    assert "no active profile" in res.content


async def test_reopen_closes_prior_session():
    first = FakeSession()
    second = FakeSession()
    plugin = _plugin(first, second)
    await _open(plugin)
    assert plugin._open_session is first
    await _open(plugin)
    assert first.closed == 1          # prior context torn down before re-open
    assert plugin._open_session is second


# ── read_page ────────────────────────────────────────────────────────────────────

async def test_read_page_requires_open_session():
    res = await _plugin().call_tool("read_page", {})
    assert res.is_error
    assert "browser_open_session" in res.content


async def test_read_page_navigates_and_returns_snapshot():
    sess = FakeSession(page=PageView(url="https://x/p", title="Page", text="hello"))
    plugin = _plugin(sess)
    await _open(plugin)
    res = await plugin.call_tool("read_page", {"url": "https://x/p"})
    assert not res.is_error
    payload = json.loads(res.content)
    assert payload["url"] == "https://x/p"
    assert payload["title"] == "Page"
    assert payload["text"] == "hello"
    assert payload["truncated"] is False
    assert sess.read_url == "https://x/p"


async def test_read_page_without_url_passes_none():
    sess = FakeSession()
    plugin = _plugin(sess)
    await _open(plugin)
    await plugin.call_tool("read_page", {})
    assert sess.read_url is None


async def test_read_page_truncates_long_text():
    long_text = "z" * (bsp._MAX_TEXT_CHARS + 100)
    sess = FakeSession(page=PageView(url="u", title="t", text=long_text))
    plugin = _plugin(sess)
    await _open(plugin)
    res = await plugin.call_tool("read_page", {})
    payload = json.loads(res.content)
    assert len(payload["text"]) == bsp._MAX_TEXT_CHARS
    assert payload["truncated"] is True


# ── fill_form ────────────────────────────────────────────────────────────────────

async def test_fill_form_requires_open_session():
    res = await _plugin().call_tool("fill_form", {"fields": [{"selector": "#a", "value": "b"}]})
    assert res.is_error
    assert "browser_open_session" in res.content


async def test_fill_form_fills_each_field():
    sess = FakeSession()
    plugin = _plugin(sess)
    await _open(plugin)
    res = await plugin.call_tool("fill_form", {"fields": [
        {"selector": "#email", "value": "a@b.c"},
        {"selector": "#q", "value": "hi"},
    ]})
    assert not res.is_error
    assert json.loads(res.content) == {"filled": 2}
    assert sess.filled == [("#email", "a@b.c"), ("#q", "hi")]


async def test_fill_form_rejects_empty_or_malformed():
    plugin = _plugin(FakeSession())
    await _open(plugin)
    for bad in ([], "nope", [{"selector": "#a"}], [{"value": "v"}], [{"selector": "", "value": "v"}]):
        res = await plugin.call_tool("fill_form", {"fields": bad})
        assert res.is_error


# ── click ────────────────────────────────────────────────────────────────────────

async def test_click_requires_open_session():
    res = await _plugin().call_tool("click", {"selector": "#go"})
    assert res.is_error
    assert "browser_open_session" in res.content


async def test_click_returns_resulting_url():
    sess = FakeSession(click_url="https://x/done")
    plugin = _plugin(sess)
    await _open(plugin)
    res = await plugin.call_tool("click", {"selector": "#go"})
    assert not res.is_error
    assert json.loads(res.content) == {"url": "https://x/done"}
    assert sess.clicked == ["#go"]


async def test_click_requires_selector():
    plugin = _plugin(FakeSession())
    await _open(plugin)
    res = await plugin.call_tool("click", {"selector": ""})
    assert res.is_error


# ── dispatch + error containment ─────────────────────────────────────────────────

async def test_unknown_tool():
    res = await _plugin().call_tool("nope", {})
    assert res.is_error
    assert "Unknown tool" in res.content


async def test_read_page_swallows_driver_exception():
    class Boom(FakeSession):
        async def read_page(self, url=None):
            raise RuntimeError("selector blew up")
    sess = Boom()
    plugin = _plugin(sess)
    await _open(plugin)
    res = await plugin.call_tool("read_page", {})
    assert res.is_error
    # Internal exception text is not leaked.
    assert "selector blew up" not in res.content
