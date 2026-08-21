"""AttachmentStore tests -- S14 (#297).

Locks the per-profile attachment contract:

  * classify() picks kind by suffix-then-mime so a mis-reported
    ``application/octet-stream`` doesn't lose a .md file.
  * extract_text() round-trips utf-8 text and caps at MAX_EXTRACTED_CHARS.
  * save_file copies the bytes into the per-profile store root, never
    escapes it via traversal, and records public dict shape (no
    ``stored_path`` / ``extracted_text`` leakage to the renderer).
  * bind_to_turn flips ``turn_id`` only on unbound rows.
  * drop_unbound removes file + row for pending uploads only; bound
    rows are immutable.
  * serialise_for_prompt always emits at least a stub per attachment
    (#792), even one whose text extraction was skipped or failed.
"""
from __future__ import annotations

import sqlite3

import pytest

from cerebral.db.attachments import (
    KIND_BINARY,
    KIND_IMAGE,
    KIND_PDF,
    KIND_TEXT,
    MAX_EXTRACTED_CHARS,
    Attachment,
    AttachmentStore,
    attach_to_turn_content,
    attachments_payload,
    classify,
    extract_text,
    serialise_for_prompt,
)


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "openmind.db"
    # The store FKs reference profiles + conversation_turns; seed a
    # minimal schema so the FK targets resolve. We don't need the full
    # profile shape -- just the id column.
    con = sqlite3.connect(str(db))
    con.executescript(
        "PRAGMA foreign_keys=ON;"
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        "INSERT INTO profiles (id) VALUES (1);"
        "INSERT INTO profiles (id) VALUES (2);"
        "CREATE TABLE conversation_turns ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  profile_id INTEGER NOT NULL"
        ");"
        "INSERT INTO conversation_turns (id, profile_id) VALUES (10, 1);"
        "INSERT INTO conversation_turns (id, profile_id) VALUES (11, 1);"
    )
    con.commit()
    con.close()
    return AttachmentStore(db_path=db, store_root=tmp_path / "store")


# -- classify -----------------------------------------------------------------

def test_classify_picks_text_for_text_suffix_even_when_mime_is_octet_stream():
    assert classify("notes.md", "application/octet-stream") == KIND_TEXT


def test_classify_picks_pdf_for_pdf_suffix():
    assert classify("report.pdf", "") == KIND_PDF


def test_classify_picks_image_for_image_suffix():
    assert classify("photo.JPG", "") == KIND_IMAGE


def test_classify_falls_back_to_binary_for_unknown():
    assert classify("blob.dat", "application/octet-stream") == KIND_BINARY


def test_classify_picks_image_for_image_mime_with_unknown_suffix():
    assert classify("blob.bin", "image/png") == KIND_IMAGE


# -- extract_text -------------------------------------------------------------

def test_extract_text_utf8_text():
    assert extract_text("héllo".encode("utf-8"), KIND_TEXT) == "héllo"


def test_extract_text_caps_at_max_chars():
    big = ("a" * (MAX_EXTRACTED_CHARS + 1024)).encode("utf-8")
    out = extract_text(big, KIND_TEXT)
    assert len(out) == MAX_EXTRACTED_CHARS


def test_extract_text_binary_returns_empty():
    assert extract_text(b"\x00\x01\xff\x02", KIND_BINARY) == ""


def test_extract_text_image_returns_empty():
    assert extract_text(b"\x89PNG\r\n\x1a\n", KIND_IMAGE) == ""


def test_extract_text_pdf_without_pypdf_returns_empty(monkeypatch):
    # Force the optional pypdf import path to fail so the test runs even
    # if pypdf isn't installed in the dev env. The fallback contract is
    # "no extraction, no crash".
    import builtins

    real_import = builtins.__import__

    def deny(name, *a, **k):
        if name in ("pypdf", "PyPDF2"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    assert extract_text(b"%PDF-1.4 fake", KIND_PDF) == ""


# -- save_file ----------------------------------------------------------------

def test_save_file_persists_bytes_and_metadata(store, tmp_path):
    att = store.save_file(1, "notes.md", b"# hi", "text/markdown")
    assert att.id > 0
    assert att.profile_id == 1
    assert att.filename == "notes.md"
    assert att.mime == "text/markdown"
    assert att.size == 4
    assert att.kind == KIND_TEXT
    assert att.extracted_text == "# hi"
    # Bytes ended up on disk
    from pathlib import Path
    assert Path(att.stored_path).read_bytes() == b"# hi"


def test_save_file_strips_path_traversal(store):
    att = store.save_file(1, "../../etc/passwd", b"x", "")
    # Filename column is sanitised
    assert "/" not in att.filename and "\\" not in att.filename
    # And the on-disk path lives under the configured store root, not
    # somewhere up the tree.
    from pathlib import Path
    assert Path(att.stored_path).is_file()
    # Path must be inside the store_root passed to the fixture.
    assert str(att.stored_path).startswith(str(store._store_root))


def test_save_file_to_dict_excludes_stored_path_and_text(store):
    att = store.save_file(1, "x.txt", b"abc", "text/plain")
    payload = att.to_dict()
    assert "stored_path"    not in payload
    assert "extracted_text" not in payload
    assert payload["has_text"] is True
    assert payload["kind"]     == KIND_TEXT
    assert payload["filename"] == "x.txt"


def test_save_file_image_keeps_binary_kind_and_no_text(store):
    att = store.save_file(1, "pic.png", b"\x89PNG\r\n\x1a\nfake", "image/png")
    assert att.kind == KIND_IMAGE
    assert att.extracted_text == ""
    assert att.to_dict()["has_text"] is False


# -- bind_to_turn -------------------------------------------------------------

def test_bind_to_turn_sets_turn_id_for_unbound_rows(store):
    a = store.save_file(1, "a.txt", b"a", "text/plain")
    b = store.save_file(1, "b.txt", b"b", "text/plain")
    bound = store.bind_to_turn([a.id, b.id], turn_id=10)
    assert bound == 2
    assert {x.id for x in store.list_for_turn(10)} == {a.id, b.id}


def test_bind_to_turn_skips_already_bound(store):
    a = store.save_file(1, "a.txt", b"a", "text/plain")
    store.bind_to_turn([a.id], turn_id=10)
    # Second bind to a different turn must NOT move it.
    bound = store.bind_to_turn([a.id], turn_id=11)
    assert bound == 0
    [row] = store.list_for_turn(10)
    assert row.id == a.id


def test_bind_to_turn_with_empty_list_is_noop(store):
    assert store.bind_to_turn([], turn_id=10) == 0


# -- list_pending / drop_unbound ----------------------------------------------

def test_list_pending_returns_only_unbound_rows(store):
    a = store.save_file(1, "a.txt", b"a", "text/plain")
    b = store.save_file(1, "b.txt", b"b", "text/plain")
    store.bind_to_turn([a.id], turn_id=10)
    pending = store.list_pending(1)
    assert [x.id for x in pending] == [b.id]


def test_list_pending_is_profile_scoped(store):
    a = store.save_file(1, "a.txt", b"a", "text/plain")
    b = store.save_file(2, "b.txt", b"b", "text/plain")
    assert [x.id for x in store.list_pending(1)] == [a.id]
    assert [x.id for x in store.list_pending(2)] == [b.id]


def test_drop_unbound_removes_pending_only(store):
    from pathlib import Path

    a = store.save_file(1, "a.txt", b"a", "text/plain")
    b = store.save_file(1, "b.txt", b"b", "text/plain")
    store.bind_to_turn([a.id], turn_id=10)

    dropped = store.drop_unbound([a.id, b.id])
    assert dropped == 1
    # The bound row's file is untouched.
    assert Path(store.get(a.id).stored_path).exists()
    # The unbound row's file is gone.
    assert store.get(b.id) is None


def test_drop_unbound_with_empty_list_is_noop(store):
    assert store.drop_unbound([]) == 0


# -- serialise_for_prompt -----------------------------------------------------

def test_serialise_for_prompt_returns_empty_when_no_text(store):
    a = store.save_file(1, "pic.png", b"\x89PNG", "image/png")
    # Image attachment carries no extracted text but DOES produce a
    # header line so the LLM at least knows a file was sent. That keeps
    # "an image is described" reachable when the model is vision-capable.
    out = serialise_for_prompt([a])
    assert "pic.png" in out


def test_serialise_for_prompt_includes_text_payload(store):
    a = store.save_file(1, "notes.md", b"# hello world", "text/markdown")
    out = serialise_for_prompt([a])
    assert "notes.md" in out
    assert "# hello world" in out


def test_serialise_for_prompt_skips_binary_with_no_text(store):
    a = store.save_file(1, "blob.dat", b"\x00\x01", "application/octet-stream")
    # Binary chips DO mention the file path so a Files plugin can act on it.
    out = serialise_for_prompt([a])
    assert "blob.dat" in out


def test_serialise_for_prompt_stubs_text_kind_with_failed_extraction(store):
    """#792: a KIND_TEXT/KIND_PDF attachment whose extraction was skipped
    (over MAX_INLINE_BYTES) or failed (bad PDF, missing pypdf) previously
    vanished from the prompt entirely -- only KIND_IMAGE/KIND_BINARY got a
    stub. It must now surface a "(no text extracted...)" line like any
    other empty-text attachment, not disappear silently."""
    a = Attachment(
        id=1, profile_id=1, turn_id=None, filename="huge.txt", mime="text/plain",
        size=99_000_000, kind=KIND_TEXT, stored_path="/tmp/huge.txt",
        extracted_text="", created_at="",
    )
    out = serialise_for_prompt([a])
    assert "huge.txt" in out
    assert "no text extracted" in out


def test_serialise_for_prompt_empty_for_empty_list():
    assert serialise_for_prompt([]) == ""


# -- attach_to_turn_content ----------------------------------------------------

def test_attach_to_turn_content_injects_dicts(store):
    a = store.save_file(1, "a.txt", b"a", "text/plain")
    enriched = attach_to_turn_content({"text": "hi"}, [a])
    assert enriched["text"] == "hi"
    assert isinstance(enriched["attachments"], list)
    assert enriched["attachments"][0]["filename"] == "a.txt"
    # No path leakage even via the merged content.
    assert "stored_path" not in enriched["attachments"][0]


def test_attachments_payload_strips_internals(store):
    a = store.save_file(1, "a.txt", b"a", "text/plain")
    [d] = attachments_payload([a])
    assert "stored_path" not in d
    assert "extracted_text" not in d
