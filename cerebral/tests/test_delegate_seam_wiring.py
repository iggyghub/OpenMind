"""Delegate seam wiring guard -- ADR-0020 S4 (#730), Issue #153 pattern.

Verifies that _wire_plugin_seams reaches the orchestrator-loaded delegate
module (not a second import instance) with a factory that supplies the four
keys run_subagent needs, and that main.py never directly imports the seam
setter from plugins.delegate. Mirrors cerebral/tests/test_video_seam_wiring.py.
"""
from __future__ import annotations

import types
from pathlib import Path

import cerebral.main as main

_ROOT = Path(__file__).resolve().parents[2]


def _recorder_module():
    mod = types.SimpleNamespace()
    calls: dict[str, list] = {}
    mod.set_subagent_context = lambda *a: calls.setdefault("set_subagent_context", []).append(a)
    mod._calls = calls
    return mod


def test_wire_plugin_seams_reaches_delegate_module(monkeypatch):
    """_wire_plugin_seams must call set_subagent_context on the delegate module."""
    mod = _recorder_module()
    monkeypatch.setattr(main._orc, "get_plugin_module", lambda name: mod)
    monkeypatch.setattr(main, "_active_profile", None)

    main._wire_plugin_seams()

    assert "set_subagent_context" in mod._calls, "set_subagent_context not wired"


def test_wired_factory_returns_the_four_expected_keys(monkeypatch):
    """The factory passed to set_subagent_context, once called, must supply
    exactly what plugins/delegate.py's _delegate() reads: router, gate_fn,
    execute_fn, all_tools (main.py's live _router / _gate_tool / _orc.call_tool
    / _orc.tools_for_llm)."""
    mod = _recorder_module()
    monkeypatch.setattr(main._orc, "get_plugin_module", lambda name: mod)
    monkeypatch.setattr(main, "_active_profile", None)

    main._wire_plugin_seams()

    (factory,) = mod._calls["set_subagent_context"][0]
    ctx = factory()
    assert set(ctx) == {"router", "gate_fn", "execute_fn", "all_tools"}
    assert ctx["router"] is main._router
    assert ctx["gate_fn"] is main._gate_tool
    assert ctx["execute_fn"] == main._orc.call_tool
    assert ctx["all_tools"] == main._orc.tools_for_llm


def test_main_does_not_directly_import_delegate_seam_setter():
    """Guard: the seam setter must not be imported directly from
    plugins.delegate (that creates a second module instance, bypassing the
    orchestrator's copy -- Issue #153)."""
    src = (_ROOT / "cerebral" / "main.py").read_text(encoding="utf-8")
    assert "from plugins.delegate import" not in src, (
        "plugins.delegate imported directly in main.py -- wire via _wire_plugin_seams"
    )
    assert "import plugins.delegate" not in src, (
        "plugins.delegate imported directly in main.py -- wire via _wire_plugin_seams"
    )
