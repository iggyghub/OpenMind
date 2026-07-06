"""Jobs seams must land on the orchestrator-loaded module — Issue #153 pattern.

``import plugins.job_search`` from main.py creates a SECOND module instance;
seam setters wired there never reach the ``openmind_plugin_job_search``
module the orchestrator dispatches tool calls against. That shipped as the
live "No active profile" ``jobs_store_resume`` failure (2026-07-05): the
profile-id seam was seeded at import time on the wrong instance while every
real tool call read ``_active_profile_id = None``.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import cerebral.main as main

_ROOT = Path(__file__).resolve().parents[2]

_JOBS_SEAMS = (
    "set_store", "set_navigate_fn", "set_extract_fn", "set_score_fn",
    "set_apply_driver_fn", "set_apply_submit_fn", "set_recall_fn",
    "set_index_answer_fn", "set_get_jobs_email_fn",
    "set_create_ats_account_fn", "set_store_ats_password_fn",
    "set_read_verify_link_fn", "set_click_verify_link_fn",
    "set_active_profile_id", "set_pending_resume_path",
)


def _recorder_module():
    """A stand-in for the orchestrator-loaded plugin module that records
    every seam invocation."""
    mod = types.SimpleNamespace()
    calls: dict[str, list[tuple]] = {}
    for seam in _JOBS_SEAMS:
        setattr(
            mod, seam,
            (lambda s: lambda *a: calls.setdefault(s, []).append(a))(seam),
        )
    mod._calls = calls
    return mod


def test_wire_plugin_seams_seeds_profile_on_orchestrator_module(monkeypatch):
    """The regression: _wire_plugin_seams must seed the active profile id
    into the module the orchestrator returns — not a fresh import."""
    mod = _recorder_module()
    monkeypatch.setattr(main._orc, "get_plugin_module", lambda name: mod)
    monkeypatch.setattr(main, "_active_profile", types.SimpleNamespace(id=7))

    main._wire_plugin_seams()

    assert mod._calls["set_active_profile_id"] == [(7,)]
    # Spot-check a couple of table-wired seams reached the same module.
    assert mod._calls["set_extract_fn"]
    assert mod._calls["set_navigate_fn"]


def test_js_seam_targets_orchestrator_module(monkeypatch):
    mod = _recorder_module()
    monkeypatch.setattr(main._orc, "get_plugin_module", lambda name: mod)

    main._js_seam("set_active_profile_id", 4)

    assert mod._calls["set_active_profile_id"] == [(4,)]


def test_js_seam_noop_before_discovery(monkeypatch):
    """Before plugin discovery get_plugin_module raises KeyError; the seam
    helper must warn-and-skip, not crash the caller (e.g. switch_profile)."""
    def missing(name):
        raise KeyError(name)
    monkeypatch.setattr(main._orc, "get_plugin_module", missing)

    main._js_seam("set_active_profile_id", 4)  # must not raise


def test_main_does_not_import_jobs_seam_setters():
    """Guard: seam setters must never be imported from plugins.job_search —
    only module-identity-free names (the store class, the pure gate fn)."""
    src = (_ROOT / "cerebral" / "main.py").read_text(encoding="utf-8")
    block = re.search(r"from plugins\.job_search import \((.*?)\)", src, re.DOTALL)
    assert block, "job_search import block not found"
    imported = block.group(1)
    assert "set_" not in imported, (
        "seam setter imported from plugins.job_search — that is a second "
        "module instance; wire it via _wire_plugin_seams/_js_seam instead"
    )
