"""
browser_session wiring tests (S3) — discovery + the main.py session factory.

Proves the real plugins/browser_session.py registers cleanly through the
orchestrator (static scan + REQUIRED_CAPABILITIES validation + create), that it
exposes the set_session_factory seam main.py wires, and that
main._get_browser_session satisfies that seam's contract (None with no profile,
a per-profile BrowserSession otherwise) without opening a real browser.
"""
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cerebral.main as main  # noqa: E402
from cerebral.browser import BrowserSession  # noqa: E402
from cerebral.mcp.orchestrator import MCPOrchestrator  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent.parent
_PLUGIN_SRC = _ROOT / "plugins" / "browser_session.py"


def _discover_isolated(tmp_path) -> MCPOrchestrator:
    """Discover ONLY browser_session.py via a one-file tmp plugins dir."""
    shutil.copy(_PLUGIN_SRC, tmp_path / "browser_session.py")
    orc = MCPOrchestrator()
    orc.discover_plugins(tmp_path)
    return orc


def test_plugin_registers_with_expected_capabilities(tmp_path):
    orc = _discover_isolated(tmp_path)
    assert orc.registration_errors == []
    assert orc.required_capabilities_for("browser_session") == frozenset(
        {"secrets_read", "network_egress_cloud"}
    )


def test_plugin_registers_four_tools(tmp_path):
    orc = _discover_isolated(tmp_path)
    names = {t.name for t in orc.list_tools()}
    assert {"browser_open_session", "read_page", "fill_form", "click"} <= names


def test_loaded_module_exposes_session_factory_seam(tmp_path):
    orc = _discover_isolated(tmp_path)
    module = orc.get_plugin_module("browser_session")
    assert hasattr(module, "set_session_factory")


# ── main._get_browser_session contract ────────────────────────────────────────

def test_get_browser_session_none_without_profile(monkeypatch):
    monkeypatch.setattr(main, "_active_profile", None)
    assert main._get_browser_session() is None


def test_get_browser_session_returns_profile_scoped_session(monkeypatch):
    class _P:
        id = 4
    monkeypatch.setattr(main, "_active_profile", _P())
    sess = main._get_browser_session()
    assert isinstance(sess, BrowserSession)
    assert sess.profile_id == 4
    assert sess.provider == "google_web"
    # Constructed lazily — no browser opened (the data dir is not touched yet).
    assert not sess.user_data_dir.exists() or sess.user_data_dir.is_dir()


def test_get_browser_session_satisfies_plugin_factory_seam(tmp_path, monkeypatch):
    """The real _get_browser_session, wired as the plugin's factory, resolves
    a session for the active profile and None otherwise — the exact contract
    set_session_factory depends on, without opening a browser."""
    orc = _discover_isolated(tmp_path)
    module = orc.get_plugin_module("browser_session")
    plugin = module.create()
    module.set_session_factory(main._get_browser_session)

    class _P:
        id = 4
    monkeypatch.setattr(main, "_active_profile", _P())
    session, err = plugin._resolve_session()
    assert err is None
    assert isinstance(session, BrowserSession)
    assert session.profile_id == 4

    monkeypatch.setattr(main, "_active_profile", None)
    session, err = plugin._resolve_session()
    assert session is None
    assert "no active profile" in err


def test_wiring_entry_present_in_main_source():
    """Guard the one-line seam registration in _wire_plugin_seams."""
    src = (_ROOT / "cerebral" / "main.py").read_text(encoding="utf-8")
    assert re.search(
        r'"browser_session"\s*,\s*"set_session_factory"\s*,\s*_get_browser_session',
        src,
    )
