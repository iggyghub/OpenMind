"""Tests for plugins/documents.py -- S2 #453 + S3 #454.

SAFETY: never invokes a real soffice.exe.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import plugins.documents as doc
from plugins.documents import DocumentStore


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


# ── S3: DocumentStore unit tests ──────────────────────────────────────────────

def _store(tmp_path) -> DocumentStore:
    return DocumentStore(
        db_path=tmp_path / "test.db",
        store_root=tmp_path / "lib",
    )


def test_doc_store_and_list(tmp_path):
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"docx content")

    stored = store.store_doc(1, "My Resume", str(src))

    assert stored["name"] == "My Resume"
    assert stored["kind"] == "docx"
    assert stored["profile_id"] == 1
    assert Path(stored["path"]).exists()
    assert Path(stored["path"]).read_bytes() == b"docx content"

    docs = store.list_docs(1)
    assert len(docs) == 1
    assert docs[0]["id"] == stored["id"]


def test_doc_list_scoped_to_profile(tmp_path):
    store = _store(tmp_path)
    src = tmp_path / "file.txt"
    src.write_bytes(b"x")

    store.store_doc(1, "Profile-1 Doc", str(src))
    store.store_doc(2, "Profile-2 Doc", str(src))

    assert len(store.list_docs(1)) == 1
    assert len(store.list_docs(2)) == 1


def test_snapshot_creates_v0_on_first_overwrite(tmp_path):
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"original")

    stored = store.store_doc(1, "Resume", str(src))
    doc_path = stored["path"]

    store._snapshot(doc_path)
    versions = store.list_versions(stored["id"])
    assert len(versions) == 1
    assert versions[0].startswith("v0_")


def test_snapshot_fifo_keeps_v0_plus_5(tmp_path):
    """Store 8 overwrites -> versions/ holds v0 + last 5 timestamped."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"v0")

    stored = store.store_doc(1, "Resume", str(src))
    doc_path = stored["path"]

    # 8 snapshot calls simulate 8 pre-overwrite snapshots.
    # Call 1 creates v0; calls 2-8 create 7 timestamped (pruned to 5).
    for _ in range(8):
        time.sleep(0.002)  # ensure unique microsecond timestamps
        store._snapshot(doc_path)
        # Overwrite the main file so snapshots have different content.
        Path(doc_path).write_bytes(b"newer content")

    versions = store.list_versions(stored["id"])
    # v0 + 5 most-recent = 6
    assert len(versions) == 6
    assert any(v.startswith("v0_") for v in versions)
    # All non-v0 versions should be the 5 most recent (sorted).
    non_v0 = sorted(v for v in versions if not v.startswith("v0_"))
    assert len(non_v0) == 5


def test_revert_restores_content(tmp_path):
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"original")

    stored = store.store_doc(1, "Resume", str(src))
    doc_path = stored["path"]

    # First overwrite: creates v0 from "original"
    store._snapshot(doc_path)
    Path(doc_path).write_bytes(b"modified")

    versions = store.list_versions(stored["id"])
    assert len(versions) == 1  # just v0

    reverted = store.revert_doc(stored["id"], versions[0])
    assert Path(reverted["path"]).read_bytes() == b"original"

    # A snapshot of "modified" should now be in versions/
    new_versions = store.list_versions(stored["id"])
    assert len(new_versions) == 2  # v0 (original) + ts (modified)


def test_revert_nonexistent_version_raises(tmp_path):
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"x")
    stored = store.store_doc(1, "Doc", str(src))

    with pytest.raises(FileNotFoundError):
        store.revert_doc(stored["id"], "v0_nonexistent.docx")


def test_revert_nonexistent_doc_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        store.revert_doc(9999, "v0_something.docx")


# ── S3: doc_store tool via plugin seams ──────────────────────────────────────

async def test_doc_store_tool_round_trip(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)

    src = tmp_path / "cv.docx"
    src.write_bytes(b"cv bytes")

    plugin = doc.create()
    result = await plugin.call_tool("doc_store", {"name": "CV", "source_path": str(src)})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["doc"]["name"] == "CV"
    assert data["doc"]["kind"] == "docx"

    list_result = await plugin.call_tool("doc_list", {})
    assert not list_result.is_error
    docs = json.loads(list_result.content)["docs"]
    assert len(docs) == 1


async def test_doc_convert_tool_stubbed(tmp_path, monkeypatch):
    """doc_convert with a stubbed converter seam -- no real soffice."""
    store = _store(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)

    src = tmp_path / "resume.docx"
    src.write_bytes(b"docx")
    stored = store.store_doc(1, "Resume", str(src))

    # Stub converter: writes a fake PDF next to the source and returns its path.
    async def fake_converter(source_path, fmt, out_dir):
        out = Path(out_dir) / f"{Path(source_path).stem}.{fmt}"
        out.write_bytes(b"fake pdf")
        return str(out)

    monkeypatch.setattr(doc, "_converter_fn", fake_converter)

    plugin = doc.create()
    result = await plugin.call_tool("doc_convert", {"doc_id": stored["id"], "format": "pdf"})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["doc"]["kind"] == "pdf"
    assert Path(data["doc"]["path"]).exists()


async def test_doc_convert_no_converter_seam(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_converter_fn", None)

    src = tmp_path / "resume.docx"
    src.write_bytes(b"docx")
    stored = store.store_doc(1, "Resume", str(src))

    result = await doc.create().call_tool("doc_convert", {"doc_id": stored["id"], "format": "pdf"})
    assert result.is_error
    assert "converter seam not wired" in result.content


async def test_doc_revert_tool(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)

    src = tmp_path / "resume.docx"
    src.write_bytes(b"original")
    stored = store.store_doc(1, "Resume", str(src))

    # Create v0 snapshot then overwrite
    store._snapshot(stored["path"])
    Path(stored["path"]).write_bytes(b"modified")

    versions = store.list_versions(stored["id"])
    assert versions  # v0 exists

    plugin = doc.create()
    result = await plugin.call_tool(
        "doc_revert", {"doc_id": stored["id"], "version": versions[0]}
    )
    assert not result.is_error
    data = json.loads(result.content)
    assert Path(data["doc"]["path"]).read_bytes() == b"original"


# ── S3: seam wiring (no main.py import needed -- guard test) ─────────────────

def test_set_store_wires_seam(tmp_path):
    store = _store(tmp_path)
    doc.set_store(store)
    assert doc._store is store
    doc.set_store(None)


def test_set_active_profile_id_wires_seam():
    doc.set_active_profile_id(42)
    assert doc._active_profile_id == 42
    doc.set_active_profile_id(None)


def test_set_converter_fn_wires_seam():
    fn = object()
    doc.set_converter_fn(fn)
    assert doc._converter_fn is fn
    doc.set_converter_fn(None)


def test_set_broadcast_fn_wires_seam():
    fn = object()
    doc.set_broadcast_fn(fn)
    assert doc._broadcast_fn is fn
    doc.set_broadcast_fn(None)


# ── S4: _check_doc_change ──────────────────────────────────────────────────────

async def test_check_doc_change_fires_snapshot_broadcast_hook(tmp_path, monkeypatch):
    """_check_doc_change snapshots, bumps updated_at, broadcasts, fires hook."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"original")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)

    broadcast_calls = []
    async def fake_broadcast():
        broadcast_calls.append(1)
    monkeypatch.setattr(doc, "_broadcast_fn", fake_broadcast)

    hook_calls = []
    async def fake_hook(doc_id):
        hook_calls.append(doc_id)
    monkeypatch.setattr(doc, "_change_hook_fn", fake_hook)

    doc_id = stored["id"]
    # Simulate watcher having a stale mtime.
    monkeypatch.setattr(doc, "_watched", {doc_id: Path(stored["path"]).stat().st_mtime - 1.0})

    await doc._check_doc_change(doc_id, stored["path"])

    assert broadcast_calls == [1]
    assert hook_calls == [doc_id]
    assert doc._watched[doc_id] == pytest.approx(Path(stored["path"]).stat().st_mtime)
    assert store.list_versions(doc_id) != []
    updated = store.get_doc(doc_id)
    assert updated["updated_at"] >= stored["updated_at"]


async def test_check_doc_change_noop_on_same_mtime(tmp_path, monkeypatch):
    """No re-ingest when mtime has not advanced."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"content")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    broadcast_calls = []
    async def fake_broadcast():
        broadcast_calls.append(1)
    monkeypatch.setattr(doc, "_broadcast_fn", fake_broadcast)

    doc_id = stored["id"]
    current_mtime = Path(stored["path"]).stat().st_mtime
    monkeypatch.setattr(doc, "_watched", {doc_id: current_mtime})

    await doc._check_doc_change(doc_id, stored["path"])

    assert broadcast_calls == []
    assert store.get_doc(doc_id)["updated_at"] == stored["updated_at"]


# ── S4: lazy re-ingest on doc_list ────────────────────────────────────────────

async def test_lazy_reingest_on_doc_list(tmp_path, monkeypatch):
    """doc_list fires re-ingest for watched docs whose mtime advanced."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"original")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)
    monkeypatch.setattr(doc, "_change_hook_fn", None)

    doc_id = stored["id"]
    monkeypatch.setattr(doc, "_watched", {doc_id: Path(stored["path"]).stat().st_mtime - 1.0})

    plugin = doc.create()
    result = await plugin.call_tool("doc_list", {})
    assert not result.is_error

    updated = store.get_doc(doc_id)
    assert updated["updated_at"] >= stored["updated_at"]


# ── S4: doc_open tool ─────────────────────────────────────────────────────────

async def test_doc_open_launches_and_watches(tmp_path, monkeypatch):
    """doc_open calls launcher, creates watcher task, creates v0."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"content")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)
    monkeypatch.setattr(doc, "_watched", {})
    monkeypatch.setattr(doc, "_watcher_tasks", {})

    launched = []
    async def fake_launcher(path):
        launched.append(path)
    monkeypatch.setattr(doc, "_launcher_fn", fake_launcher)

    doc_id = stored["id"]
    plugin = doc.create()
    result = await plugin.call_tool("doc_open", {"doc_id": doc_id})

    assert not result.is_error
    data = json.loads(result.content)
    assert data["opened"] is True
    assert launched == [stored["path"]]
    assert doc_id in doc._watcher_tasks
    assert not doc._watcher_tasks[doc_id].done()
    assert store.list_versions(doc_id) != []

    doc._watcher_tasks[doc_id].cancel()


async def test_doc_open_no_launcher_seam_returns_error(tmp_path, monkeypatch):
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"content")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_launcher_fn", None)

    result = await doc.create().call_tool("doc_open", {"doc_id": stored["id"]})
    assert result.is_error
    assert "launcher seam not wired" in result.content


def test_set_launcher_fn_wires_seam():
    fn = object()
    doc.set_launcher_fn(fn)
    assert doc._launcher_fn is fn
    doc.set_launcher_fn(None)


def test_set_change_hook_fn_wires_seam():
    fn = object()
    doc.set_change_hook_fn(fn)
    assert doc._change_hook_fn is fn
    doc.set_change_hook_fn(None)


# ── S5: doc_edit ──────────────────────────────────────────────────────────────

async def test_doc_edit_snapshots_broadcast_hook(tmp_path, monkeypatch):
    """doc_edit: snapshot before edit, broadcast + hook after success."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"original")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)

    broadcast_calls = []
    async def fake_broadcast():
        broadcast_calls.append(1)
    monkeypatch.setattr(doc, "_broadcast_fn", fake_broadcast)

    hook_calls = []
    async def fake_hook(doc_id):
        hook_calls.append(doc_id)
    monkeypatch.setattr(doc, "_change_hook_fn", fake_hook)

    snapshot_before_edit = []
    async def fake_editor(doc_path, edits):
        # By the time editor runs, v0 snapshot must already exist.
        snapshot_before_edit.extend(store.list_versions(stored["id"]))
    monkeypatch.setattr(doc, "_editor_fn", fake_editor)

    edits = [{"op": "find_replace", "find": "old@example.com", "replace": "new@example.com"}]
    result = await doc.create().call_tool("doc_edit", {"doc_id": stored["id"], "edits": edits})

    assert not result.is_error
    data = json.loads(result.content)
    assert data["edited"] is True
    assert broadcast_calls == [1]
    assert hook_calls == [stored["id"]]
    assert any(v.startswith("v0_") for v in snapshot_before_edit), "v0 must exist before editor runs"


async def test_doc_edit_timeout_returns_error(tmp_path, monkeypatch):
    """doc_edit returns is_error when the editor raises TimeoutError."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"x")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)
    monkeypatch.setattr(doc, "_change_hook_fn", None)

    async def timed_out_editor(doc_path, edits):
        raise TimeoutError("timed out")
    monkeypatch.setattr(doc, "_editor_fn", timed_out_editor)

    result = await doc.create().call_tool(
        "doc_edit", {"doc_id": stored["id"], "edits": [{"op": "find_replace", "find": "x", "replace": "y"}]}
    )
    assert result.is_error
    assert "timed out" in result.content


async def test_doc_edit_editor_failure_returns_error(tmp_path, monkeypatch):
    """doc_edit returns is_error when the editor raises a generic exception."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"x")
    stored = store.store_doc(1, "Resume", str(src))

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)
    monkeypatch.setattr(doc, "_change_hook_fn", None)

    async def failing_editor(doc_path, edits):
        raise RuntimeError("UNO script failed (rc=1): some stderr")
    monkeypatch.setattr(doc, "_editor_fn", failing_editor)

    result = await doc.create().call_tool(
        "doc_edit", {"doc_id": stored["id"], "edits": [{"op": "find_replace", "find": "x", "replace": "y"}]}
    )
    assert result.is_error
    assert "doc_edit failed" in result.content


async def test_doc_edit_missing_doc_returns_error(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)

    result = await doc.create().call_tool(
        "doc_edit", {"doc_id": 9999, "edits": [{"op": "find_replace", "find": "x", "replace": "y"}]}
    )
    assert result.is_error
    assert "not found" in result.content


async def test_doc_edit_missing_args_returns_error(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)

    result = await doc.create().call_tool("doc_edit", {"doc_id": 1})
    assert result.is_error

    result2 = await doc.create().call_tool("doc_edit", {"edits": []})
    assert result2.is_error


def test_set_editor_fn_wires_seam():
    fn = object()
    doc.set_editor_fn(fn)
    assert doc._editor_fn is fn
    doc.set_editor_fn(None)


# ── S6: _documents_update_event augmentation (version_count + versions) ───────

def test_documents_update_event_includes_version_count(tmp_path, monkeypatch):
    """_documents_update_event augments each doc with version_count and versions."""
    import cerebral.main as main_mod

    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"content")
    stored = store.store_doc(1, "Resume", str(src))

    # Create a v0 snapshot
    store._snapshot(stored["path"])

    class FakeProfile:
        id = 1

    saved_profile = main_mod._active_profile
    saved_store = main_mod._document_store
    try:
        main_mod._active_profile = FakeProfile()
        main_mod._document_store = store
        event = main_mod._documents_update_event()
    finally:
        main_mod._active_profile = saved_profile
        main_mod._document_store = saved_store

    assert event["type"] == "documents_update"
    docs = event["data"]["docs"]
    assert len(docs) == 1
    d = docs[0]
    assert "version_count" in d
    assert "versions" in d
    assert isinstance(d["versions"], list)
    assert d["version_count"] == len(d["versions"])
    assert d["version_count"] == 1  # just v0


# ── S6: list_documents IPC handler round-trip ─────────────────────────────────

async def test_list_documents_ipc_broadcasts_documents_update(tmp_path, monkeypatch):
    """'list_documents' IPC message triggers a documents_update broadcast."""
    import cerebral.main as main_mod

    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"content")
    store.store_doc(1, "Resume", str(src))

    class FakeProfile:
        id = 1

    broadcasts = []

    async def fake_broadcast(event):
        broadcasts.append(event)

    saved = {
        "_active_profile": main_mod._active_profile,
        "_document_store": main_mod._document_store,
        "_broadcast": main_mod._broadcast,
    }
    try:
        main_mod._active_profile = FakeProfile()
        main_mod._document_store = store
        main_mod._broadcast = fake_broadcast

        await main_mod._handle_message({"type": "list_documents"})
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)

    assert len(broadcasts) == 1
    evt = broadcasts[0]
    assert evt["type"] == "documents_update"
    assert "docs" in evt["data"]


# ── S7 #448: change hook ──────────────────────────────────────────────────────

async def test_change_hook_fn_fired_after_mtime_change(tmp_path, monkeypatch):
    """set_change_hook_fn is called with the doc_id when a watched doc's mtime advances."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"v1")
    stored = store.store_doc(1, "Resume", str(src))
    doc_id = stored["id"]
    doc_path = stored["path"]

    hook_calls = []

    async def fake_hook(did):
        hook_calls.append(did)

    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)
    monkeypatch.setattr(doc, "_change_hook_fn", fake_hook)
    monkeypatch.setitem(doc._watched, doc_id, 0.0)

    # Simulate mtime advance by touching the file
    import time
    time.sleep(0.01)
    Path(doc_path).write_bytes(b"v2")

    await doc._check_doc_change(doc_id, doc_path)

    assert hook_calls == [doc_id], "change hook not called with correct doc_id"


async def test_change_hook_fn_not_fired_when_mtime_unchanged(tmp_path, monkeypatch):
    """Change hook must NOT fire when the file has not been modified."""
    store = _store(tmp_path)
    src = tmp_path / "resume.docx"
    src.write_bytes(b"content")
    stored = store.store_doc(1, "Resume", str(src))
    doc_id = stored["id"]
    doc_path = stored["path"]

    hook_calls = []

    async def fake_hook(did):
        hook_calls.append(did)

    # Set _watched mtime to current mtime so no change is detected
    current_mtime = Path(doc_path).stat().st_mtime
    monkeypatch.setattr(doc, "_store", store)
    monkeypatch.setattr(doc, "_active_profile_id", 1)
    monkeypatch.setattr(doc, "_broadcast_fn", None)
    monkeypatch.setattr(doc, "_change_hook_fn", fake_hook)
    monkeypatch.setitem(doc._watched, doc_id, current_mtime)

    await doc._check_doc_change(doc_id, doc_path)

    assert hook_calls == [], "change hook fired when file was not changed"
