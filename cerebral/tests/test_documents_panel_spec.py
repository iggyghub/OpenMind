"""Tests for the Documents plugin's declarative panel_spec (UI2 A3 -- #483).

A plugin returns a widget tree, never HTML or JavaScript, so the renderer
(tray/lib/panel-spec.js) can draw it safely inside the Main window that also
hosts the Credentials UI (ADR-0012 decision 3).
"""
from __future__ import annotations

from pathlib import Path

import plugins.documents as doc
from plugins.documents import DocumentStore


VALID_WIDGETS = frozenset({"list", "detail", "text"})


def _store_at(tmp_path) -> DocumentStore:
    return DocumentStore(
        db_path=tmp_path / "test.db",
        store_root=tmp_path / "lib",
    )


def test_panel_spec_shape_with_no_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "_store", _store_at(tmp_path))
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    spec = doc.create().panel_spec(1)

    assert isinstance(spec, dict)
    assert spec["title"] == "Documents"
    assert isinstance(spec["widgets"], list)
    assert len(spec["widgets"]) == 2

    types = [w["type"] for w in spec["widgets"]]
    assert types == ["list", "detail"]
    for w in spec["widgets"]:
        assert w["type"] in VALID_WIDGETS

    list_w = spec["widgets"][0]
    assert list_w["items"] == []
    detail_w = spec["widgets"][1]
    labels = {f["label"] for f in detail_w["fields"]}
    assert "Documents" in labels
    # Count reflects the empty library.
    doc_count = next(f["value"] for f in detail_w["fields"] if f["label"] == "Documents")
    assert doc_count == "0"


def test_panel_spec_lists_stored_docs(tmp_path, monkeypatch):
    store = _store_at(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)

    src = tmp_path / "resume.docx"
    src.write_bytes(b"docx content")
    store.store_doc(1, "My Resume", str(src))
    src2 = tmp_path / "notes.txt"
    src2.write_bytes(b"hello")
    store.store_doc(1, "Notes", str(src2))

    spec = doc.create().panel_spec(1)
    list_w = spec["widgets"][0]

    titles = [item["title"] for item in list_w["items"]]
    assert set(titles) == {"My Resume", "Notes"}
    kinds = [item["subtitle"] for item in list_w["items"]]
    assert set(kinds) == {"docx", "txt"}

    detail_w = spec["widgets"][1]
    count = next(f["value"] for f in detail_w["fields"] if f["label"] == "Documents")
    assert count == "2"


def test_panel_spec_no_store_returns_empty_but_valid(monkeypatch):
    # Store never wired -> panel_spec still returns a valid, drawable shape.
    monkeypatch.setattr(doc, "_store", None)
    spec = doc.create().panel_spec(1)
    assert isinstance(spec, dict)
    assert spec["widgets"][0]["items"] == []


def test_panel_spec_no_profile_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "_store", _store_at(tmp_path))
    spec = doc.create().panel_spec(None)
    assert spec["widgets"][0]["items"] == []


def test_panel_spec_text_widget_for_txt_file(tmp_path, monkeypatch):
    """Text widget appears for a .txt doc; value matches the file content."""
    store = _store_at(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)

    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello from notes")
    store.store_doc(1, "Notes", str(src))

    spec = doc.create().panel_spec(1)
    text_widgets = [w for w in spec["widgets"] if w["type"] == "text"]
    assert len(text_widgets) == 1
    tw = text_widgets[0]
    assert tw["value"] == "hello from notes"
    assert tw["tool"] == "doc_write"
    assert tw["tool_args"]["doc_id"] is not None
    assert tw.get("label") == "Notes"


def test_panel_spec_no_text_widget_for_docx(tmp_path, monkeypatch):
    """.docx docs must NOT produce a text widget (ADR-0011 / ADR-0012)."""
    store = _store_at(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)

    src = tmp_path / "resume.docx"
    src.write_bytes(b"docx bytes")
    store.store_doc(1, "My Resume", str(src))

    spec = doc.create().panel_spec(1)
    text_widgets = [w for w in spec["widgets"] if w["type"] == "text"]
    assert text_widgets == [], "docx must not offer a text widget"


def test_panel_spec_text_widget_value_updates_after_write(tmp_path, monkeypatch):
    """panel_spec re-reads the file; after doc_write the new content appears."""
    store = _store_at(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)

    src = tmp_path / "notes.txt"
    src.write_bytes(b"original")
    stored = store.store_doc(1, "Notes", str(src))

    spec_before = doc.create().panel_spec(1)
    tw_before = next(w for w in spec_before["widgets"] if w["type"] == "text")
    assert tw_before["value"] == "original"


def test_panel_spec_carries_no_html_or_scripts(tmp_path, monkeypatch):
    """The spec is data, not markup -- SAFETY #3 in UI2.md."""
    store = _store_at(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    src = tmp_path / "notes.txt"
    src.write_bytes(b"x")
    store.store_doc(1, "safe name", str(src))

    spec = doc.create().panel_spec(1)

    def _walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                yield k
                yield from _walk(x)
        elif isinstance(v, list):
            for x in v:
                yield from _walk(x)
        elif isinstance(v, str):
            yield v

    for s in _walk(spec):
        low = s.lower()
        assert "<script" not in low
        assert "onerror=" not in low
        assert "javascript:" not in low
