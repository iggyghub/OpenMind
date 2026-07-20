"""Tests for plugins/documents.py -- S2 #453.

SAFETY: never invokes a real soffice.exe.
"""
from __future__ import annotations

import json
from pathlib import Path

import plugins.documents as doc


# ── find_soffice unit tests ───────────────────────────────────────────────────

def test_find_soffice_finds_in_custom_dir(tmp_path):
    prog_dir = tmp_path / "LibreOffice" / "program"
    prog_dir.mkdir(parents=True)
    soffice = prog_dir / "soffice.exe"
    soffice.touch()
    assert doc.find_soffice(_dirs=[prog_dir]) == soffice


def test_find_soffice_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(doc.shutil, "which", lambda _: None)
    result = doc.find_soffice(_dirs=[tmp_path / "nonexistent"])
    assert result is None


def test_find_soffice_prefers_first_matching_dir(tmp_path):
    first = tmp_path / "first" / "program"
    second = tmp_path / "second" / "program"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "soffice.exe").touch()
    (second / "soffice.exe").touch()
    assert doc.find_soffice(_dirs=[first, second]) == first / "soffice.exe"


# ── doc_status via seam ───────────────────────────────────────────────────────

async def test_doc_status_available(monkeypatch):
    fake_path = Path("/fake/program/soffice.exe")
    monkeypatch.setattr(doc, "_find_soffice_fn", lambda: fake_path)
    result = await doc.create().call_tool("doc_status", {})
    data = json.loads(result.content)
    assert data["available"] is True
    assert data["soffice_path"] == str(fake_path)
    assert not result.is_error


async def test_doc_status_missing(monkeypatch):
    monkeypatch.setattr(doc, "_find_soffice_fn", lambda: None)
    result = await doc.create().call_tool("doc_status", {})
    data = json.loads(result.content)
    assert data["available"] is False
    assert "setup-libreoffice" in data["message"]
    assert not result.is_error


async def test_doc_status_unknown_tool():
    result = await doc.create().call_tool("no_such_tool", {})
    assert result.is_error


# ── set_find_soffice_fn seam wiring ──────────────────────────────────────────

def test_set_find_soffice_fn_wires_seam(monkeypatch):
    called = []
    monkeypatch.setattr(doc, "_find_soffice_fn", None)
    doc.set_find_soffice_fn(lambda: called.append(1) or None)
    doc.create()._doc_status()
    assert called == [1]
    # reset
    doc.set_find_soffice_fn(None)
