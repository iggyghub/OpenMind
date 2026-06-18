"""Conversation attachment store -- S14 (#297).

Per-profile local file store for files dragged/uploaded into the composer.
Files are copied into ``cerebral/data/attachments/<profile_id>/<uuid>/``
and a row is written into ``conversation_attachments`` carrying the
original filename, mime, size, the stored path, and (when extractable)
the file's text contents.

No cloud upload -- everything is on disk next to the SQLite DB. The
stored_path is held alongside the conversation_turn so a future Files
plugin can act on it without a second copy.

Two-phase write so the UI feels instant:

    1. Renderer reads the file, sends ``attach_files`` with base64 bytes.
    2. Backend writes the file, runs cheap text extraction, returns an
       ``attachment_id`` + metadata (label, kind, size).
    3. Renderer renders a chip with the id pending against the next send.
    4. On ``user_text_command`` with ``attachment_ids``, Cerebral binds
       the rows to the user turn and folds extracted text into the prompt.

Extraction is best-effort and synchronous: text-like files decode to
UTF-8 (capped at ``MAX_EXTRACTED_CHARS``); PDFs attempt ``pypdf`` if
installed; images and other binaries are stored unmodified with no
extracted text (the renderer reads ``kind`` to decide chip styling, and
a future vision-capable model will receive the file via the
``stored_path``).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

DB_PATH        = Path(__file__).parent.parent / "data" / "openmind.db"
DEFAULT_STORE_ROOT = Path(__file__).parent.parent / "data" / "attachments"

# Cap on per-file extracted text folded into the LLM prompt. PDF/text
# decode is bounded so a 50MB log doesn't blow up the model's context.
MAX_EXTRACTED_CHARS = 32_000

# Hard ceiling on per-file size accepted over IPC. Beyond this we still
# write the file (so a Files plugin can act on it) but skip extraction.
MAX_INLINE_BYTES = 25 * 1024 * 1024  # 25 MiB

# Extensions/mimes that round-trip cleanly as UTF-8 text. Anything else
# falls back to the binary path (still stored, no extracted_text).
_TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
    ".html", ".htm", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".toml",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".sh", ".rb", ".go",
    ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".sql",
})
_IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg",
})

KIND_TEXT   = "text"    # extracted text is the file content
KIND_PDF    = "pdf"     # extracted text via pypdf (best effort)
KIND_IMAGE  = "image"   # no text; renderer shows image-style chip
KIND_BINARY = "binary"  # path-reference only; no extracted text


@dataclass
class Attachment:
    id: int
    profile_id: int
    turn_id: int | None
    filename: str
    mime: str
    size: int
    kind: str
    stored_path: str
    extracted_text: str
    created_at: str

    def to_dict(self) -> dict:
        # ``stored_path`` is intentionally left out of the dict the UI
        # sees so a future Files plugin remains the path-handling surface.
        # The renderer only needs label/kind/size to draw a chip.
        return {
            "id": self.id,
            "turn_id": self.turn_id,
            "filename": self.filename,
            "mime": self.mime,
            "size": self.size,
            "kind": self.kind,
            "has_text": bool(self.extracted_text),
        }


def classify(filename: str, mime: str) -> str:
    """Pick the rendering/extraction kind for an upload. Suffix wins over
    mime so a misreported ``application/octet-stream`` from the browser
    still classifies a ``.md`` correctly."""
    suffix = Path(filename).suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return KIND_TEXT
    if suffix == ".pdf" or (mime or "").lower() == "application/pdf":
        return KIND_PDF
    if suffix in _IMAGE_SUFFIXES or (mime or "").lower().startswith("image/"):
        return KIND_IMAGE
    if (mime or "").lower().startswith("text/"):
        return KIND_TEXT
    return KIND_BINARY


def extract_text(data: bytes, kind: str) -> str:
    """Best-effort text extraction. Returns ``""`` when there is nothing
    sensible to fold into the LLM prompt -- the caller should treat
    that as "this attachment is a binary the model can't read"."""
    if not data:
        return ""
    if kind == KIND_TEXT:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # Fall back to latin-1 so a Windows-1252 .txt doesn't 500
            # the upload. The user can still re-encode externally.
            text = data.decode("latin-1", errors="replace")
        return text[:MAX_EXTRACTED_CHARS]
    if kind == KIND_PDF:
        return _extract_pdf_text(data)[:MAX_EXTRACTED_CHARS]
    return ""


def _extract_pdf_text(data: bytes) -> str:
    """Pull the visible text out of a PDF. Tries ``pypdf`` first (pure
    Python, no system deps) and falls back to an empty string if nothing
    is installed. OCR (pdf2image + pytesseract) is deliberately NOT
    attempted here -- it pulls in poppler and tesseract binaries and is
    too slow to run synchronously during an attach call."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            return ""
    import io

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return ""
    pieces: list[str] = []
    for page in reader.pages:
        try:
            pieces.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(p.strip() for p in pieces if p.strip())


class AttachmentStore:
    """SQLite-backed conversation attachment metadata + on-disk file store.

    One instance per Cerebral process. Mirrors the existing store shape
    (``__init__`` opens its own sqlite connection, ``_init_schema`` is
    idempotent so a fresh DB and a migrated DB both end up valid).
    """

    def __init__(
        self,
        db_path: Path = DB_PATH,
        store_root: Path = DEFAULT_STORE_ROOT,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store_root.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._store_root = store_root
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_attachments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id      INTEGER NOT NULL,
                turn_id         INTEGER,
                filename        TEXT    NOT NULL,
                mime            TEXT    NOT NULL DEFAULT '',
                size            INTEGER NOT NULL DEFAULT 0,
                kind            TEXT    NOT NULL DEFAULT 'binary',
                stored_path     TEXT    NOT NULL,
                extracted_text  TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now')),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY (turn_id)    REFERENCES conversation_turns(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_attach_profile
                ON conversation_attachments (profile_id);
            CREATE INDEX IF NOT EXISTS idx_conv_attach_turn
                ON conversation_attachments (turn_id);
        """)
        self._con.commit()

    # -- save / bind ------------------------------------------------------

    def save_file(
        self,
        profile_id: int,
        filename: str,
        data: bytes,
        mime: str = "",
    ) -> Attachment:
        """Write the bytes under the profile's store root and persist a
        metadata row with no turn binding yet. Returns the row.

        ``data`` may be empty -- a zero-byte attachment is still recorded
        so the renderer chip lines up with what the user dragged. Files
        larger than ``MAX_INLINE_BYTES`` are still stored on disk but
        their extracted_text is forced to ``""`` (the LLM won't see them).
        """
        safe_name = _safe_filename(filename)
        kind = classify(safe_name, mime)
        token = uuid.uuid4().hex
        dest_dir = self._store_root / str(profile_id) / token
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        dest.write_bytes(data)

        extracted = ""
        if len(data) <= MAX_INLINE_BYTES:
            extracted = extract_text(data, kind)

        cur = self._con.execute(
            "INSERT INTO conversation_attachments "
            "(profile_id, filename, mime, size, kind, stored_path, extracted_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (profile_id, safe_name, mime or "", len(data), kind,
             str(dest), extracted),
        )
        self._con.commit()
        return self._get(cur.lastrowid)  # type: ignore[arg-type]

    def bind_to_turn(self, attachment_ids: list[int], turn_id: int) -> int:
        """Bind a batch of previously-uploaded attachments to a turn.
        Returns the number of rows updated. Ignores unknown ids."""
        if not attachment_ids:
            return 0
        placeholders = ",".join("?" for _ in attachment_ids)
        cur = self._con.execute(
            f"UPDATE conversation_attachments SET turn_id = ? "
            f"WHERE id IN ({placeholders}) AND turn_id IS NULL",
            (turn_id, *attachment_ids),
        )
        self._con.commit()
        return cur.rowcount

    # -- read -------------------------------------------------------------

    def get(self, attachment_id: int) -> Attachment | None:
        row = self._con.execute(
            "SELECT * FROM conversation_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        return _row_to_attachment(row) if row else None

    def _get(self, attachment_id: int) -> Attachment:
        row = self._con.execute(
            "SELECT * FROM conversation_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"attachment {attachment_id} not found")
        return _row_to_attachment(row)

    def list_for_turn(self, turn_id: int) -> list[Attachment]:
        rows = self._con.execute(
            "SELECT * FROM conversation_attachments "
            "WHERE turn_id = ? ORDER BY id ASC",
            (turn_id,),
        ).fetchall()
        return [_row_to_attachment(r) for r in rows]

    def list_pending(self, profile_id: int) -> list[Attachment]:
        """Uploaded but not yet bound to a turn (e.g. user attached then
        navigated away). The renderer requests this on connect to rehydrate
        the chip row after a reconnect."""
        rows = self._con.execute(
            "SELECT * FROM conversation_attachments "
            "WHERE profile_id = ? AND turn_id IS NULL ORDER BY id ASC",
            (profile_id,),
        ).fetchall()
        return [_row_to_attachment(r) for r in rows]

    def drop_unbound(self, attachment_ids: list[int]) -> int:
        """Discard pending attachments (user clicked the chip's remove X
        before sending). Only drops rows that are still unbound, so a
        race with bind_to_turn never deletes a recorded turn's evidence."""
        if not attachment_ids:
            return 0
        placeholders = ",".join("?" for _ in attachment_ids)
        rows = self._con.execute(
            f"SELECT id, stored_path FROM conversation_attachments "
            f"WHERE id IN ({placeholders}) AND turn_id IS NULL",
            tuple(attachment_ids),
        ).fetchall()
        for r in rows:
            _safe_unlink(Path(r["stored_path"]))
        cur = self._con.execute(
            f"DELETE FROM conversation_attachments "
            f"WHERE id IN ({placeholders}) AND turn_id IS NULL",
            tuple(attachment_ids),
        )
        self._con.commit()
        return cur.rowcount


def _row_to_attachment(row: sqlite3.Row) -> Attachment:
    return Attachment(
        id=row["id"],
        profile_id=row["profile_id"],
        turn_id=row["turn_id"],
        filename=row["filename"] or "",
        mime=row["mime"] or "",
        size=row["size"] or 0,
        kind=row["kind"] or KIND_BINARY,
        stored_path=row["stored_path"] or "",
        extracted_text=row["extracted_text"] or "",
        created_at=row["created_at"] or "",
    )


def _safe_filename(filename: str) -> str:
    """Strip any directory parts and pick a safe basename. We never trust
    what the renderer hands us -- a malicious ``../../`` would otherwise
    write outside the per-profile store root."""
    base = Path(filename or "").name
    if not base:
        return "upload"
    # Reject control chars / nulls. The unique uuid directory keeps
    # collisions impossible, so simple sanitisation is enough.
    cleaned = "".join(c for c in base if c == " " or c.isprintable())
    return cleaned.strip() or "upload"


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        # Best-effort cleanup of the per-attachment parent dir so the
        # store doesn't fill up with empty uuid folders.
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def serialise_for_prompt(atts: list[Attachment]) -> str:
    """Render the extracted-text blob the LLM sees in front of the user's
    typed message. Returns ``""`` when no attachment carried text -- the
    caller skips the preamble entirely in that case so a pure image/binary
    upload doesn't waste tokens on an empty header.
    """
    pieces: list[str] = []
    for att in atts:
        if not att.extracted_text and att.kind not in (KIND_IMAGE, KIND_BINARY):
            continue
        header = f"[attachment: {att.filename} ({att.kind}, {att.size} bytes)]"
        if att.extracted_text:
            pieces.append(f"{header}\n{att.extracted_text}")
        else:
            pieces.append(f"{header} (no text extracted; stored at {att.stored_path})")
    if not pieces:
        return ""
    return "\n\n".join(pieces) + "\n\n"


def attachments_payload(atts: list[Attachment]) -> list[dict]:
    """Public dict form for IPC -- omits ``stored_path`` / ``extracted_text``
    so the renderer never sees the absolute on-disk path or echo back the
    file contents."""
    return [a.to_dict() for a in atts]


# Re-export the JSON shape used inside conversation turn content so the
# renderer and main.py agree on the field name without hard-coding it twice.
ATTACHMENTS_FIELD = "attachments"


def attach_to_turn_content(content: dict, atts: list[Attachment]) -> dict:
    """Inject the public attachment dicts into a turn's content payload."""
    if not atts:
        return content
    out = dict(content or {})
    out[ATTACHMENTS_FIELD] = attachments_payload(atts)
    return out


__all__ = [
    "ATTACHMENTS_FIELD",
    "Attachment",
    "AttachmentStore",
    "DEFAULT_STORE_ROOT",
    "KIND_BINARY",
    "KIND_IMAGE",
    "KIND_PDF",
    "KIND_TEXT",
    "MAX_EXTRACTED_CHARS",
    "MAX_INLINE_BYTES",
    "attach_to_turn_content",
    "attachments_payload",
    "classify",
    "extract_text",
    "serialise_for_prompt",
]


# Silence the unused-import for ``json`` -- it's exposed so a future
# manual-purge script can serialise an attachment row without re-importing.
_ = json
