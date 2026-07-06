"""
Notes plugin — MCP server for Felix.

Tools: create_note, search_notes, list_recent, delete_note.

Notes are stored as markdown files with YAML frontmatter.
An SQLite index enables fast keyword search.
"""
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "notes"

# ADR-0005 / Issue #44 — search_notes / list_recent read the SQLite index
# (fs_read); create_note writes both the index row and a markdown file on
# disk (fs_write); delete_note removes both (fs_delete).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "fs_read",
    "fs_write",
    "fs_delete",
})

from cerebral.paths import data_dir

_DEFAULT_NOTES_DIR = data_dir() / "notes"
_DEFAULT_DB = data_dir() / "openmind.db"


class NotesPlugin:
    name = PLUGIN_NAME

    def __init__(self, notes_dir=None, db_path=None):
        self._notes_dir = Path(notes_dir) if notes_dir else _DEFAULT_NOTES_DIR
        self._notes_dir.mkdir(parents=True, exist_ok=True)

        db = db_path if db_path is not None else str(_DEFAULT_DB)
        if db != ":memory:":
            Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT    NOT NULL,
                body       TEXT    NOT NULL DEFAULT '',
                filename   TEXT    NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._con.commit()

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="create_note",
                description="Creates a new markdown note with title and body.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body":  {"type": "string", "description": "Note content (markdown supported)"},
                    },
                    "required": ["title"],
                },
            ),
            Tool(
                name="search_notes",
                description="Keyword search across note titles and bodies (case-insensitive).",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="list_recent",
                description="Returns the n most recently created notes (default 10).",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer", "description": "How many notes to return (default 10)"},
                    },
                },
            ),
            Tool(
                name="delete_note",
                description="Deletes a note by id, removing both the file and index entry.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "create_note":
            return self._create_note(args)
        if tool_name == "search_notes":
            return self._search_notes(args)
        if tool_name == "list_recent":
            return self._list_recent(args)
        if tool_name == "delete_note":
            return self._delete_note(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------

    def _create_note(self, args: dict) -> ToolResult:
        title = args.get("title", "").strip()
        body  = args.get("body", "")
        if not title:
            return ToolResult(content="title is required", is_error=True)

        now = datetime.now(timezone.utc).isoformat()
        # Write to SQLite first to get the auto-increment id
        cur = self._con.execute(
            "INSERT INTO notes (title, body, filename) VALUES (?, ?, ?)",
            (title, body, ""),   # filename filled in next
        )
        note_id = cur.lastrowid

        # Sanitise title for filename
        safe = re.sub(r"[^a-zA-Z0-9_\- ]", "", title).strip().replace(" ", "_")
        filename = f"{note_id:06d}_{safe[:40]}.md"
        filepath = self._notes_dir / filename

        markdown = _build_markdown(note_id, title, now, body)
        filepath.write_text(markdown, encoding="utf-8")

        self._con.execute("UPDATE notes SET filename=? WHERE id=?", (filename, note_id))
        self._con.commit()

        return ToolResult(content=json.dumps({"id": note_id, "title": title, "filename": filename}))

    def _search_notes(self, args: dict) -> ToolResult:
        query = args.get("query", "").strip().lower()
        if not query:
            return ToolResult(content=json.dumps({"notes": []}))

        rows = self._con.execute(
            """SELECT id, title, body, filename, created_at
               FROM notes
               WHERE LOWER(title) LIKE ? OR LOWER(body) LIKE ?
               ORDER BY created_at DESC""",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        return ToolResult(content=json.dumps({"notes": [_row_to_note(r) for r in rows]}))

    def _list_recent(self, args: dict) -> ToolResult:
        n = int(args.get("n", 10))
        rows = self._con.execute(
            "SELECT id, title, body, filename, created_at FROM notes ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        return ToolResult(content=json.dumps({"notes": [_row_to_note(r) for r in rows]}))

    def _delete_note(self, args: dict) -> ToolResult:
        note_id = args.get("id")
        if note_id is None:
            return ToolResult(content="id is required", is_error=True)

        row = self._con.execute(
            "SELECT id, filename FROM notes WHERE id=?", (note_id,)
        ).fetchone()
        if not row:
            return ToolResult(content=f"Note {note_id} not found", is_error=True)

        filepath = self._notes_dir / row["filename"]
        if filepath.exists():
            filepath.unlink()

        self._con.execute("DELETE FROM notes WHERE id=?", (note_id,))
        self._con.commit()
        return ToolResult(content=json.dumps({"id": note_id, "deleted": True}))


def _build_markdown(note_id: int, title: str, created_at: str, body: str) -> str:
    frontmatter = f"---\nid: {note_id}\ntitle: {title}\ncreated_at: {created_at}\n---\n\n"
    return frontmatter + body


def _row_to_note(row: sqlite3.Row) -> dict:
    return {
        "id":         row["id"],
        "title":      row["title"],
        "filename":   row["filename"],
        "created_at": row["created_at"],
    }


def create(notes_dir=None, db_path=None) -> NotesPlugin:
    return NotesPlugin(notes_dir=notes_dir, db_path=db_path)
