"""Video seam wiring guard — VIDEO.md SAFETY + Issue #153 pattern.

Verifies that _wire_plugin_seams reaches the orchestrator-loaded video module
(not a second import instance) and that main.py never directly imports the
seam setters from plugins.video.
"""
from __future__ import annotations

import types
from pathlib import Path

import cerebral.main as main

_ROOT = Path(__file__).resolve().parents[2]

_VIDEO_SEAMS = (
    "set_store",
    "set_download_fn",
    "set_transcribe_fn",
)


def _recorder_module():
    mod = types.SimpleNamespace()
    calls: dict[str, list] = {}
    for seam in _VIDEO_SEAMS:
        setattr(
            mod, seam,
            (lambda s: lambda *a: calls.setdefault(s, []).append(a))(seam),
        )
    mod._calls = calls
    return mod


def test_wire_plugin_seams_reaches_video_module(monkeypatch):
    """_wire_plugin_seams must call video seams on the orchestrator module."""
    mod = _recorder_module()
    monkeypatch.setattr(main._orc, "get_plugin_module", lambda name: mod)
    monkeypatch.setattr(main, "_active_profile", None)

    main._wire_plugin_seams()

    assert "set_download_fn" in mod._calls, "set_download_fn not wired"
    assert "set_transcribe_fn" in mod._calls, "set_transcribe_fn not wired"


def test_main_does_not_directly_import_video_seam_setters():
    """Guard: seam setters must not be imported directly from plugins.video
    (that creates a second module instance, bypassing the orchestrator's copy)."""
    src = (_ROOT / "cerebral" / "main.py").read_text(encoding="utf-8")
    assert "from plugins.video import" not in src, (
        "plugins.video imported directly in main.py — wire via _wire_plugin_seams"
    )
    assert "import plugins.video" not in src, (
        "plugins.video imported directly in main.py — wire via _wire_plugin_seams"
    )
