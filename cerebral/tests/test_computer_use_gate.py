"""Computer-use gate wiring -- ADR-0016 sec 3-4 (S3 #577).

Covers the consequence-gate (a committing click is flagged irreversible) and
the badged full-autonomy switch (the one documented exception to ADR-0005's
non-bypassable-irreversible rule -- scoped to computer_use ONLY).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

import cerebral.main as main_mod


def _load_computer_use():
    p = Path(__file__).resolve().parents[2] / "plugins" / "computer_use.py"
    spec = importlib.util.spec_from_file_location("openmind_plugin_computer_use", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


CU = _load_computer_use()
_CU_TOOLS = {"read_ui", "click_element", "type_into", "browser_navigate"}


@pytest.fixture
def fake_orc(monkeypatch):
    """Stub _orc so plugin_for_tool/get_plugin_module report the real
    computer_use module; reset full-autonomy off."""
    def plugin_for_tool(name):
        return "computer_use" if name in _CU_TOOLS else "other"

    def get_plugin_module(name):
        if name == "computer_use":
            return CU
        raise KeyError(name)

    monkeypatch.setattr(
        main_mod, "_orc",
        SimpleNamespace(plugin_for_tool=plugin_for_tool, get_plugin_module=get_plugin_module),
    )
    monkeypatch.setattr(main_mod, "_computer_use_full_autonomy", False)


# ── consequence classifier (pure) ────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Send", True), ("Submit", True), ("Delete", True), ("Post", True),
    ("Pay now", True), ("Confirm order", True), ("Discard", True),
    ("Two", False), ("Plus", False), ("Cancel", False), ("Equals", False),
    ("Sender name", False), ("", False),
])
def test_is_committing_action_click(name, expected):
    assert CU.is_committing_action("click_element", {"name": name}) is expected


def test_is_committing_action_only_click_commits():
    assert CU.is_committing_action("type_into", {"name": "Send"}) is False
    assert CU.is_committing_action("read_ui", {}) is False
    assert CU.is_committing_action("browser_navigate", {"url": "http://x/send"}) is False


# ── _gate_flags_for (consequence-gate) ───────────────────────────────────────

def test_committing_click_flagged_irreversible(fake_orc):
    assert main_mod._gate_flags_for("click_element", {"name": "Send"}).irreversible is True


def test_benign_click_not_irreversible(fake_orc):
    assert main_mod._gate_flags_for("click_element", {"name": "Two"}).irreversible is False


def test_non_computer_use_tool_never_flagged(fake_orc):
    # gmail_send resolves to plugin "other" -> computer-use gate never applies.
    assert main_mod._gate_flags_for("gmail_send", {"name": "Send"}).irreversible is False


# ── full-autonomy switch (the ADR-0005 exception, scoped to computer_use) ─────

def test_full_auto_gate_off_by_default(fake_orc):
    assert main_mod._computer_use_full_auto_gate("click_element", {}) is False


def test_full_auto_gate_on_only_bypasses_computer_use(fake_orc, monkeypatch):
    monkeypatch.setattr(main_mod, "_computer_use_full_autonomy", True)
    assert main_mod._computer_use_full_auto_gate("click_element", {}) is True
    # Every OTHER plugin's irreversible modal stays enforced even when the
    # computer-use switch is on -- the exception is scoped, not global.
    assert main_mod._computer_use_full_auto_gate("gmail_send", {}) is False


def test_modal_auto_gate_composes_scoped(fake_orc, monkeypatch):
    # Off: nothing is auto-approved (jobs_apply_submit isn't this tool).
    assert main_mod._modal_auto_gate("click_element", {}) is False
    monkeypatch.setattr(main_mod, "_computer_use_full_autonomy", True)
    assert main_mod._modal_auto_gate("click_element", {}) is True
    # gmail_send is NOT bypassed by the computer-use switch.
    assert main_mod._modal_auto_gate("gmail_send", {}) is False


# ── IPC set + broadcast ──────────────────────────────────────────────────────

async def test_ipc_sets_and_broadcasts_full_autonomy(monkeypatch):
    events = []

    async def fake_broadcast(e):
        events.append(e)

    monkeypatch.setattr(main_mod, "_broadcast", fake_broadcast)
    monkeypatch.setattr(main_mod, "_computer_use_full_autonomy", False)

    await main_mod._handle_message(
        {"type": "set_computer_use_full_autonomy", "data": {"enabled": True}}
    )
    assert main_mod._computer_use_full_autonomy is True
    assert any(
        e["type"] == "computer_use:full_autonomy" and e["data"]["enabled"] is True
        for e in events
    )

    await main_mod._handle_message(
        {"type": "set_computer_use_full_autonomy", "data": {"enabled": False}}
    )
    assert main_mod._computer_use_full_autonomy is False
