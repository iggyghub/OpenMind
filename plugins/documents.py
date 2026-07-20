"""
Documents MCP plugin -- Documents campaign ADR-0011, issues #452-#457 / #448.

Tools: doc_status (S2), doc_list / doc_store / doc_convert / doc_revert (S3)

S2 (#453): find_soffice() discovery helper + doc_status tool.
S3 (#454): DocumentStore (SQLite + file-based library), snapshot versioning,
           doc_list / doc_store / doc_convert / doc_revert tools.

All soffice discovery is injectable via set_find_soffice_fn for tests.
All file-conversion is injectable via set_converter_fn for tests.
Never invoke a real soffice.exe from within this module at import time or in tests.
"""
import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "documents"

# ADR-0005: doc_status is pure local introspection (fs_read).
# S3 adds doc_store/doc_convert/doc_revert which write files (fs_write).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write", "fs_delete"})

_SOFFICE_DEFAULT_DIRS: tuple[Path, ...] = (
    Path("C:/Program Files/LibreOffice/program"),
    Path("C:/Program Files (x86)/LibreOffice/program"),
)

# Injectable seam: tests replace this to avoid real filesystem/process calls.
_find_soffice_fn = None


def set_find_soffice_fn(fn) -> None:
    global _find_soffice_fn
    _find_soffice_fn = fn


def find_soffice(_dirs=None) -> "Path | None":
    """Locate soffice.exe in standard install dirs then PATH."""
    dirs = _dirs if _dirs is not None else _SOFFICE_DEFAULT_DIRS
    for d in dirs:
        p = d / "soffice.exe"
        if p.exists():
            return p
    found = shutil.which("soffice")
    return Path(found) if found else None


_INSTALL_MSG = (
    "LibreOffice not found. Run scripts/setup-libreoffice.ps1 to install it."
)

# ── S3: DocumentStore ─────────────────────────────────────────────────────────

_DB_PATH = data_dir() / "openmind.db"
_STORE_ROOT = data_dir() / "attachments"

_VERSIONS_TO_KEEP = 5  # keep v0 + this many most-recent timestamped snapshots


class DocumentStore:
    """Profile-scoped document library backed by SQLite + filesystem.

    Files live under store_root/<profile_id>/documents/<doc_id>/<filename>.
    Versions live under store_root/<profile_id>/documents/<doc_id>/versions/.
    """

    def __init__(self, db_path: Path = _DB_PATH, store_root: Path | None = None) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._store_root = store_root or _STORE_ROOT
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id  INTEGER NOT NULL,
                name        TEXT    NOT NULL DEFAULT '',
                path        TEXT    NOT NULL DEFAULT '',
                kind        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            )
        """)
        self._con.commit()

    def _doc_dir(self, profile_id: int, doc_id: int) -> Path:
        return self._store_root / str(profile_id) / "documents" / str(doc_id)

    def get_doc(self, doc_id: int) -> "dict | None":
        row = self._con.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_docs(self, profile_id: int) -> list:
        rows = self._con.execute(
            "SELECT * FROM documents WHERE profile_id = ? ORDER BY created_at DESC",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def store_doc(self, profile_id: int, name: str, source_path: "str | Path") -> dict:
        """Copy a file into the library and register it."""
        src = Path(source_path)
        kind = src.suffix.lstrip(".").lower()
        now = datetime.now(timezone.utc).isoformat()

        # Insert placeholder row to get the auto-assigned doc_id.
        cur = self._con.execute(
            "INSERT INTO documents (profile_id, name, path, kind, created_at, updated_at) "
            "VALUES (?, ?, '', ?, ?, ?)",
            (profile_id, name, kind, now, now),
        )
        doc_id = cur.lastrowid
        self._con.commit()

        doc_dir = self._doc_dir(profile_id, doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        dest = doc_dir / src.name
        shutil.copy2(src, dest)

        self._con.execute(
            "UPDATE documents SET path = ? WHERE id = ?", (str(dest), doc_id)
        )
        self._con.commit()
        return self.get_doc(doc_id)

    def register_file(
        self, profile_id: int, name: str, file_path: "str | Path", kind: str
    ) -> dict:
        """Register an already-present library file as a new document entry.

        Used by doc_convert: the converter writes the output into the source
        doc's directory; we then register it here without another copy.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self._con.execute(
            "INSERT INTO documents (profile_id, name, path, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (profile_id, name, str(file_path), kind, now, now),
        )
        self._con.commit()
        return self.get_doc(cur.lastrowid)

    def _snapshot(self, doc_path: "str | Path") -> None:
        """Snapshot the current file before any overwrite.

        First call creates v0 (original). Subsequent calls create timestamped
        copies. FIFO: retain v0 + the 5 most recent timestamped snapshots.
        """
        src = Path(doc_path)
        if not src.exists():
            return
        versions_dir = src.parent / "versions"
        versions_dir.mkdir(exist_ok=True)

        v0 = versions_dir / f"v0_{src.name}"
        if not v0.exists():
            shutil.copy2(src, v0)
            return  # v0 just created; no timestamped copy on the very first overwrite

        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        shutil.copy2(src, versions_dir / f"{ts}_{src.name}")

        # Prune: keep only _VERSIONS_TO_KEEP most-recent timestamped copies.
        timestamped = sorted(
            [p for p in versions_dir.iterdir() if not p.name.startswith("v0_")],
            key=lambda p: p.name,
        )
        for old in timestamped[:-_VERSIONS_TO_KEEP]:
            old.unlink()

    def list_versions(self, doc_id: int) -> list:
        doc = self.get_doc(doc_id)
        if not doc:
            return []
        versions_dir = Path(doc["path"]).parent / "versions"
        if not versions_dir.exists():
            return []
        return sorted(p.name for p in versions_dir.iterdir())

    def revert_doc(self, doc_id: int, version: str) -> dict:
        """Snapshot the current file, then restore a named version."""
        doc = self.get_doc(doc_id)
        if not doc:
            raise KeyError(f"doc {doc_id} not found")
        versions_dir = Path(doc["path"]).parent / "versions"
        version_file = versions_dir / version
        if not version_file.exists():
            raise FileNotFoundError(f"version {version!r} not found")
        self._snapshot(doc["path"])
        shutil.copy2(version_file, doc["path"])
        now = datetime.now(timezone.utc).isoformat()
        self._con.execute(
            "UPDATE documents SET updated_at = ? WHERE id = ?", (now, doc_id)
        )
        self._con.commit()
        return self.get_doc(doc_id)


# ── S3: module-level seams ────────────────────────────────────────────────────

_store: "DocumentStore | None" = None
_active_profile_id: "int | None" = None
# callable(source_path: str, fmt: str, out_dir: str) -> str; async
_converter_fn = None
# callable() -> None; async — broadcasts documents_update to tray
_broadcast_fn = None


def set_store(store: DocumentStore) -> None:
    global _store
    _store = store


def set_active_profile_id(pid: "int | None") -> None:
    global _active_profile_id
    _active_profile_id = pid


def set_converter_fn(fn) -> None:
    global _converter_fn
    _converter_fn = fn


def set_broadcast_fn(fn) -> None:
    global _broadcast_fn
    _broadcast_fn = fn


# ── Plugin class ──────────────────────────────────────────────────────────────


class DocumentsPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list:
        return [
            Tool(
                name="doc_status",
                description=(
                    "Returns LibreOffice availability. available=true when soffice.exe "
                    "is found; false with an install hint."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doc_list",
                description="List all documents in the active profile's library.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="doc_store",
                description=(
                    "Copy a file into the document library and register it. "
                    "name: display name; source_path: absolute path to the source file."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "source_path": {"type": "string"},
                    },
                    "required": ["name", "source_path"],
                },
            ),
            Tool(
                name="doc_convert",
                description=(
                    "Convert a library document to another format via LibreOffice "
                    "(headless). doc_id: integer library id; format: target extension "
                    "(e.g. 'pdf', 'docx', 'odt'). The converted file is stored next to "
                    "the source doc and registered in the library."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "integer"},
                        "format": {"type": "string"},
                    },
                    "required": ["doc_id", "format"],
                },
            ),
            Tool(
                name="doc_revert",
                description=(
                    "Restore a library document to a specific snapshot. "
                    "doc_id: integer library id; version: version filename from doc_list_versions."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "integer"},
                        "version": {"type": "string"},
                    },
                    "required": ["doc_id", "version"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "doc_status":
            return self._doc_status()
        if tool_name == "doc_list":
            return self._doc_list()
        if tool_name == "doc_store":
            return await self._doc_store(args)
        if tool_name == "doc_convert":
            return await self._doc_convert(args)
        if tool_name == "doc_revert":
            return await self._doc_revert(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ── tool implementations ──────────────────────────────────────────────────

    def _doc_status(self) -> ToolResult:
        finder = _find_soffice_fn if _find_soffice_fn is not None else find_soffice
        path = finder()
        if path:
            return ToolResult(content=json.dumps({
                "available": True,
                "soffice_path": str(path),
            }))
        return ToolResult(content=json.dumps({
            "available": False,
            "message": _INSTALL_MSG,
        }))

    def _doc_list(self) -> ToolResult:
        if _store is None or _active_profile_id is None:
            return ToolResult(content=json.dumps({"docs": []}))
        docs = _store.list_docs(_active_profile_id)
        return ToolResult(content=json.dumps({"docs": docs}))

    async def _doc_store(self, args: dict) -> ToolResult:
        name = (args.get("name") or "").strip()
        source_path = (args.get("source_path") or "").strip()
        if not name or not source_path:
            return ToolResult(content="name and source_path are required", is_error=True)
        if _store is None or _active_profile_id is None:
            return ToolResult(content="store or profile not wired", is_error=True)
        if not Path(source_path).exists():
            return ToolResult(content=f"source_path not found: {source_path}", is_error=True)
        try:
            doc = _store.store_doc(_active_profile_id, name, source_path)
        except Exception as exc:
            return ToolResult(content=f"doc_store failed: {exc}", is_error=True)
        if _broadcast_fn:
            await _broadcast_fn()
        return ToolResult(content=json.dumps({"doc": doc}))

    async def _doc_convert(self, args: dict) -> ToolResult:
        doc_id = args.get("doc_id")
        fmt = (args.get("format") or "").lstrip(".").lower()
        if doc_id is None or not fmt:
            return ToolResult(content="doc_id and format are required", is_error=True)
        if _store is None:
            return ToolResult(content="store not wired", is_error=True)
        doc = _store.get_doc(int(doc_id))
        if not doc:
            return ToolResult(content=f"doc {doc_id} not found", is_error=True)
        if _converter_fn is None:
            return ToolResult(content="converter seam not wired", is_error=True)
        source_path = doc["path"]
        out_dir = str(Path(source_path).parent)
        try:
            converted_path = await _converter_fn(source_path, fmt, out_dir)
        except Exception as exc:
            return ToolResult(content=f"conversion failed: {exc}", is_error=True)
        stem = Path(source_path).stem
        converted_name = f"{doc['name']} ({fmt.upper()})"
        new_doc = _store.register_file(
            doc["profile_id"], converted_name, converted_path, fmt
        )
        if _broadcast_fn:
            await _broadcast_fn()
        return ToolResult(content=json.dumps({"doc": new_doc}))

    async def _doc_revert(self, args: dict) -> ToolResult:
        doc_id = args.get("doc_id")
        version = (args.get("version") or "").strip()
        if doc_id is None or not version:
            return ToolResult(content="doc_id and version are required", is_error=True)
        if _store is None:
            return ToolResult(content="store not wired", is_error=True)
        try:
            doc = _store.revert_doc(int(doc_id), version)
        except (KeyError, FileNotFoundError) as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:
            return ToolResult(content=f"revert failed: {exc}", is_error=True)
        if _broadcast_fn:
            await _broadcast_fn()
        return ToolResult(content=json.dumps({"doc": doc}))


def create() -> DocumentsPlugin:
    return DocumentsPlugin()
