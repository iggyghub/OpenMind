"""Book-library plugin -- MCP tools for uploading and managing books.
Extracted from plugins/scheduler.py per SCHEDULER-SPLIT.md S2 (#1210).
"""
import asyncio
import base64
import json
import logging
import shutil
import uuid
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.trading.books import (
    BookStore, chunk_text, extract_claims_from_chunk, extract_full_text,
    ingest_book, list_validated_strategies,
)
from cerebral.trading.discovery import (
    DiscoveryAttempts,
    DiscoveryWatchlist,
    rank_for_day_trading,
)
from cerebral.trading.strategy_store import StrategyStore
from cerebral.trading_ideas import Idea, judge_idea as _judge_idea
from cerebral.settings import SettingsStore
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "book_library"

_DEFAULT_DB = data_dir() / "openmind.db"

# fs_read: list_books reads the BookStore DB; fs_write: upload/retry/resume
# mutate BookStore records; fs_delete: delete_book's shutil.rmtree removes
# the stored book file directory.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write", "fs_delete"})


class BookLibraryPlugin:
    name = PLUGIN_NAME

    def __init__(self, db_path=None, router=None, record_activity_fn=None,
                 discovery_watchlist=None, discovery_attempts=None,
                 settings=None, book_store=None, scheduler=None):
        # scheduler: the live SchedulerPlugin instance for _run_gauntlet access.
        self._scheduler = scheduler
        self._router = router
        self._record_activity_fn = record_activity_fn

        path = db_path if db_path is not None else str(_DEFAULT_DB)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        if discovery_watchlist is not None:
            self._discovery_watchlist = discovery_watchlist
        elif path == ":memory:":
            self._discovery_watchlist = DiscoveryWatchlist(db_path=Path(":memory:"))
        else:
            self._discovery_watchlist = DiscoveryWatchlist(
                db_path=Path(path).parent / "discovery_watchlist.db"
            )

        if discovery_attempts is not None:
            self._discovery_attempts = discovery_attempts
        elif path == ":memory:":
            self._discovery_attempts = DiscoveryAttempts(db_path=Path(":memory:"))
        else:
            self._discovery_attempts = DiscoveryAttempts(
                db_path=Path(path).parent / "discovery_attempts.db"
            )

        if settings is not None:
            self._settings = settings
        elif path == ":memory:":
            self._settings = SettingsStore(path=Path(":memory:"))
        else:
            self._settings = SettingsStore(path=Path(path).parent / "felix-settings.json")

        if book_store is not None:
            self._book_store = book_store
        elif path == ":memory:":
            self._book_store = BookStore(db_path=Path(":memory:"))
        else:
            self._book_store = BookStore(db_path=Path(path).parent / "books.db")

        self._books_dir = (
            Path(path).parent / "books" if path != ":memory:" else Path(":memory:")
        )
        self._book_tasks: dict[int, "asyncio.Task"] = {}
        self._book_ingest_semaphore = asyncio.Semaphore(1)
        self._on_trading_change = None

    def list_tools(self):
        return [
            Tool(
                name="upload_book",
                description=(
                    "2026-08-26: upload a book (PDF or plain text) for Felix to read in "
                    "full and pull testable trading-strategy claims out of. Each claim "
                    "found goes through the exact same judge/screen/gauntlet pipeline as "
                    "a web-sourced idea. Processing runs in the background (a real book "
                    "is many LLM passes, one per chunk) -- this returns immediately with "
                    "the book's id and queued status; poll list_books for progress. Call "
                    "once per file for multiple books."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Original filename, e.g. 'market_wizards.pdf'."},
                        "data_base64": {"type": "string", "description": "Base64-encoded file bytes."},
                        "title": {"type": "string", "description": "Book title (defaults to the filename without extension)."},
                        "category": {
                            "type": "string",
                            "description": "Category to file this book under (e.g. 'value investing', 'technical analysis'). Blank -> 'Uncategorised'.",
                        },
                    },
                    "required": ["filename", "data_base64"],
                },
            ),
            Tool(
                name="list_books",
                description="2026-08-26: every uploaded book with its ingestion status/progress/strategies-found count.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="stop_book",
                description=(
                    "2026-08-26: cancel a book's in-progress ingestion, freezing its "
                    "progress where it stands (strategies already dispatched are "
                    "unaffected). Also un-sticks a book left stuck at 'processing' "
                    "by a Cerebral restart mid-run, when there's no live task left "
                    "to cancel."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}},
                    "required": ["book_id"],
                },
            ),
            Tool(
                name="retry_book",
                description=(
                    "2026-08-26: redo a book's ingestion from scratch -- cancels any "
                    "in-progress run, re-extracts and re-chunks the original uploaded "
                    "file (still on disk), and reprocesses every chunk again. Does not "
                    "remove strategies/attempts a previous run already dispatched."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}},
                    "required": ["book_id"],
                },
            ),
            Tool(
                name="delete_book",
                description=(
                    "2026-08-26: remove a book's record and its stored file. Cancels "
                    "any in-progress ingestion first. Does not remove strategies/"
                    "attempts already dispatched from this book -- those are "
                    "independent historical records, same as a web-sourced idea's "
                    "attempts surviving even though the source URL isn't stored."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}},
                    "required": ["book_id"],
                },
            ),
            Tool(
                name="resume_book",
                description=(
                    "2026-08-28: continues a stopped book's ingestion from the exact "
                    "chunk it stopped at, instead of retry_book's from-scratch redo. "
                    "Only valid on a book whose status is 'stopped'."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {"book_id": {"type": "integer"}},
                    "required": ["book_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "upload_book":
            return await self._upload_book(args)
        if tool_name == "list_books":
            return self._list_books(args)
        if tool_name == "stop_book":
            return self._stop_book(args)
        if tool_name == "retry_book":
            return await self._retry_book(args)
        if tool_name == "resume_book":
            return await self._resume_book(args)
        if tool_name == "delete_book":
            return self._delete_book(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    # ------------------------------------------------------------------
    # 2026-08-26: book ingestion -- a book is just another idea SOURCE,
    # everything downstream (judge/screen/dispatch) reuses process_idea
    # unchanged (decision #33).
    # ------------------------------------------------------------------

    async def _upload_book(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        filename = (args.get("filename") or "").strip()
        data_b64 = args.get("data_base64") or ""
        if not filename or not data_b64:
            return ToolResult(content="filename and data_base64 are required", is_error=True)
        try:
            data = base64.b64decode(data_b64)
        except Exception as exc:
            return ToolResult(content=f"Invalid base64 data: {exc}", is_error=True)

        title = (args.get("title") or "").strip() or Path(filename).stem
        category = (args.get("category") or "").strip() or "Uncategorised"
        safe_name = Path(filename).name or "upload"

        dest_dir = self._books_dir / uuid.uuid4().hex
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        dest.write_bytes(data)

        text = extract_full_text(dest)
        if not text.strip():
            return ToolResult(
                content=(
                    f"Could not extract any text from '{filename}' -- supported "
                    "formats are PDF, EPUB, MOBI/AZW3, DOCX/DOC/ODT/RTF, and "
                    "plain text/Markdown."
                ),
                is_error=True,
            )

        book = self._book_store.add(title, safe_name, str(dest), category=category)
        chunks = chunk_text(text)
        self._book_store.set_total_chunks(book.id, len(chunks))
        self._launch_book_ingestion(book.id, chunks, title, strategy_store=strategy_store, fetch=fetch)

        return ToolResult(content=json.dumps({
            "book_id": book.id, "title": title, "status": "queued", "total_chunks": len(chunks),
        }))

    def _launch_book_ingestion(
        self, book_id: int, chunks: list, title: str, *, strategy_store=None, fetch=None,
        resume_done_offset: int = 0, resume_dispatched_offset: int = 0,
    ) -> None:
        """Shared by upload_book/retry_book/resume_book: fires the
        background ingestion task and registers it so stop_book/
        retry_book/resume_book/delete_book can find and cancel it later.
        resume_done_offset/resume_dispatched_offset (2026-08-28) are
        nonzero only for resume_book, whose `chunks` is a suffix of the
        book's real chunk list -- see _run_book_ingestion."""
        task = asyncio.create_task(
            self._run_book_ingestion(
                book_id, chunks, title, strategy_store=strategy_store, fetch=fetch,
                resume_done_offset=resume_done_offset, resume_dispatched_offset=resume_dispatched_offset,
            )
        )
        self._book_tasks[book_id] = task

    async def _run_book_ingestion(
        self, book_id: int, chunks: list, title: str, *, strategy_store=None, fetch=None,
        resume_done_offset: int = 0, resume_dispatched_offset: int = 0,
    ) -> None:
        """Runs in the background (asyncio.create_task, not awaited by the
        upload_book caller) -- a real book is many LLM passes and would
        block the IPC response for minutes otherwise. BookStore progress
        is polled by list_books, not pushed."""
        async def run_gauntlet_fn(idea: Idea, ticker: str) -> dict:
            gauntlet_args = {
                "symbol": ticker,
                "hypothesis": idea.claim_text or "book-sourced hypothesis",
                "provenance": idea.provenance,
            }
            # _run_gauntlet requires one of code/claim/url/book+chapter to
            # generate strategy code from -- a book-sourced idea carries
            # book_info (set by from_book_claim), not source_url like a
            # web-sourced one. claim is also passed so the real extracted
            # claim text survives (book+chapter alone would fall back to a
            # generic "Hypothesis from {book}" -- see _run_gauntlet's own
            # book+chapter branch).
            if idea.book_info:
                gauntlet_args["book"] = idea.book_info.get("book", "")
                gauntlet_args["chapter"] = idea.book_info.get("chapter", "")
                gauntlet_args["claim"] = idea.claim_text
            result = await self._scheduler._run_gauntlet(
                # "discovered", not a new "book" bucket -- origin is a
                # deliberately closed enum (strategy_store._VALID_ORIGINS)
                # and book ingestion is autonomous sourcing exactly like
                # web discovery, just from a different source. The book/
                # chapter provenance still survives via idea.provenance.
                gauntlet_args, origin="discovered", strategy_store=strategy_store, fetch=fetch,
            )
            if not result.is_error:
                try:
                    parsed = json.loads(result.content)
                    if parsed.get("verdict") == "VALIDATED":
                        sid = parsed.get("strategy_id", "")
                        if sid:
                            _ss = strategy_store if strategy_store is not None else StrategyStore()
                            row = _ss.get_current_version(sid)
                            if row is not None and row["provenance_json"]:
                                prov = json.loads(row["provenance_json"])
                                if "(repaired after 1 retry)" in prov.get("source", ""):
                                    self._book_store.increment_repaired(book_id)
                except Exception:
                    pass
            return {"ticker": ticker, "is_error": result.is_error, "result": result.content}

        async def claim_extractor(chunk: str) -> list:
            return await extract_claims_from_chunk(chunk, self._router)

        async def judge_idea_fn(idea: Idea) -> "tuple[bool, str]":
            return await _judge_idea(idea, router=self._router)

        def rank_fn(symbols: list) -> list:
            fetch_fn = fetch
            if fetch_fn is None:
                from cerebral.trading_data import fetch_ohlcv as fetch_fn
            return rank_for_day_trading(symbols, fetch_fn)

        async def record_attempt_fn(entry: dict) -> None:
            self._discovery_attempts.record(
                entry["symbol"], entry["verdict"],
                reason=entry.get("reason", ""), idea_url=entry.get("idea_url", ""),
            )

        def on_progress(done: int, total: int, dispatched: int) -> None:
            # `done`/`dispatched` are LOCAL to this call's `chunks` list,
            # which is a suffix of the book's real chunks when resuming --
            # add the offsets so BookStore sees the book's real, cumulative
            # progress, not a count that restarts low on every resume.
            self._book_store.update_progress(
                book_id, resume_done_offset + done, resume_dispatched_offset + dispatched,
            )
            if self._on_trading_change is not None:
                self._on_trading_change()

        # Every terminal-state write below is guarded by "am I still the
        # task registered for this book_id" -- retry_book cancels this
        # task and immediately registers a NEW one under the same id.
        # Cancellation is delivered asynchronously (at this coroutine's
        # next await, not synchronously in .cancel()), so without the
        # guard a stale cancelled run finishing its handler AFTER the new
        # run has already started would clobber fresh progress/state with
        # its own stale STOPPED/error write.
        current_task = asyncio.current_task()

        def _still_current() -> bool:
            return self._book_tasks.get(book_id) is current_task

        candidate_limit = self._settings.get("discovery_candidate_limit")
        try:
            # Only one book actually talks to the LLM at a time -- a second
            # upload waits here (still cancellable: stop_book's task.cancel()
            # reaches a task blocked on acquire() exactly like one blocked
            # inside ingest_book itself).
            async with self._book_ingest_semaphore:
                await ingest_book(
                    chunks, title, self._discovery_watchlist, run_gauntlet_fn, claim_extractor,
                    judge_idea_fn=judge_idea_fn, record_activity_fn=self._record_activity_fn,
                    record_attempt_fn=record_attempt_fn, rank_fn=rank_fn,
                    candidate_limit=candidate_limit, on_progress=on_progress,
                )
            if _still_current():
                self._book_store.set_done(book_id)
        except asyncio.CancelledError:
            # Deliberately not re-raised: this coroutine IS the task
            # itself (fire-and-forget via asyncio.create_task) -- nothing
            # awaits its result or checks task.cancelled(), so swallowing
            # here just ends the task cleanly with a real terminal status
            # instead of leaving the book stuck at "processing" forever.
            if _still_current():
                self._book_store.set_stopped(book_id)
        except Exception as exc:
            logger.exception("[book_library] book ingestion failed for book_id=%s", book_id)
            if _still_current():
                self._book_store.set_error(book_id, str(exc))
        finally:
            if _still_current():
                self._book_tasks.pop(book_id, None)
            if self._on_trading_change is not None:
                self._on_trading_change()

    def _list_books(self, args: dict, *, strategy_store=None) -> ToolResult:
        books = self._book_store.list_all()
        store = strategy_store if strategy_store is not None else StrategyStore()
        return ToolResult(content=json.dumps([
            {
                "id": b.id, "title": b.title, "filename": b.filename, "status": b.status,
                "total_chunks": b.total_chunks, "processed_chunks": b.processed_chunks,
                "strategies_found": b.strategies_found,
                "strategies_repaired": b.strategies_repaired,
                "created_at": b.created_at,
                "error_message": b.error_message, "category": b.category,
                "valid_strategies": list_validated_strategies(b.title, store),
            }
            for b in books
        ]))

    def _stop_book(self, args: dict) -> ToolResult:
        book_id = args.get("book_id")
        if not isinstance(book_id, int):
            return ToolResult(content="book_id (integer) is required", is_error=True)
        book = self._book_store.get(book_id)
        if book is None:
            return ToolResult(content=f"No book with id {book_id}", is_error=True)

        task = self._book_tasks.get(book_id)
        if task is not None and not task.done():
            task.cancel()  # _run_book_ingestion's own handler sets STATUS_STOPPED
            return ToolResult(content=json.dumps({"book_id": book_id, "status": "stopped"}))
        if book.status in ("done", "error"):
            # 2026-08-28: found live -- a stray Stop click on an already-
            # terminal book used to silently overwrite "done"/"error" with
            # "stopped", losing no data but showing wrong state until
            # someone noticed and hand-corrected it via a direct DB call.
            return ToolResult(
                content=f"Book '{book.title}' is already {book.status} -- nothing to stop",
                is_error=True,
            )
        # No live task, not already terminal -- e.g. a Cerebral restart
        # mid-run orphaned this book at "processing" with nothing left to
        # cancel. Mark it stopped directly so it isn't stuck forever.
        self._book_store.set_stopped(book_id)
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({"book_id": book_id, "status": "stopped"}))

    async def _retry_book(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        book_id = args.get("book_id")
        if not isinstance(book_id, int):
            return ToolResult(content="book_id (integer) is required", is_error=True)
        book = self._book_store.get(book_id)
        if book is None:
            return ToolResult(content=f"No book with id {book_id}", is_error=True)

        task = self._book_tasks.get(book_id)
        if task is not None and not task.done():
            task.cancel()

        path = Path(book.stored_path)
        if not path.exists():
            return ToolResult(
                content=f"Original file for '{book.title}' is no longer on disk -- cannot redo",
                is_error=True,
            )
        text = extract_full_text(path)
        if not text.strip():
            return ToolResult(content=f"Could not re-extract any text from '{book.title}'", is_error=True)

        chunks = chunk_text(text)
        self._book_store.reset(book_id)
        self._book_store.set_total_chunks(book_id, len(chunks))
        self._launch_book_ingestion(book_id, chunks, book.title, strategy_store=strategy_store, fetch=fetch)
        if self._on_trading_change is not None:
            self._on_trading_change()

        return ToolResult(content=json.dumps({
            "book_id": book_id, "title": book.title, "status": "queued", "total_chunks": len(chunks),
        }))

    async def _resume_book(self, args: dict, *, strategy_store=None, fetch=None) -> ToolResult:
        book_id = args.get("book_id")
        if not isinstance(book_id, int):
            return ToolResult(content="book_id (integer) is required", is_error=True)
        book = self._book_store.get(book_id)
        if book is None:
            return ToolResult(content=f"No book with id {book_id}", is_error=True)
        if book.status != "stopped":
            return ToolResult(
                content=f"Book '{book.title}' is not stopped (status={book.status}) -- nothing to resume",
                is_error=True,
            )

        path = Path(book.stored_path)
        if not path.exists():
            return ToolResult(
                content=f"Original file for '{book.title}' is no longer on disk -- cannot resume",
                is_error=True,
            )
        text = extract_full_text(path)
        all_chunks = chunk_text(text)
        if len(all_chunks) != book.total_chunks:
            # Re-chunking produced a different count than when this book
            # was first processed (extraction/chunking logic changed, or
            # the stored file was somehow altered) -- resuming by index
            # would misalign with what was actually already processed.
            # Refuse rather than silently reprocess the wrong chunks;
            # Redo is still available and always safe (starts clean).
            return ToolResult(
                content=(
                    f"Chunk count for '{book.title}' changed since it was stopped "
                    f"({book.total_chunks} -> {len(all_chunks)}) -- use Redo instead of Resume."
                ),
                is_error=True,
            )
        remaining = all_chunks[book.processed_chunks:]
        if not remaining:
            self._book_store.set_done(book_id)
            if self._on_trading_change is not None:
                self._on_trading_change()
            return ToolResult(content=json.dumps({"book_id": book_id, "status": "done"}))

        # set_total_chunks with the SAME total_chunks value is reused
        # purely for its status-flip-to-"processing" side effect -- unlike
        # reset() (which retry_book uses), it does not touch
        # processed_chunks/strategies_found, which is the whole point:
        # this feature exists to preserve that progress, not wipe it.
        self._book_store.set_total_chunks(book_id, book.total_chunks)
        self._launch_book_ingestion(
            book_id, remaining, book.title, strategy_store=strategy_store, fetch=fetch,
            resume_done_offset=book.processed_chunks, resume_dispatched_offset=book.strategies_found,
        )
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({
            "book_id": book_id, "title": book.title, "status": "processing",
            "resumed_from_chunk": book.processed_chunks, "total_chunks": book.total_chunks,
        }))

    def _delete_book(self, args: dict) -> ToolResult:
        book_id = args.get("book_id")
        if not isinstance(book_id, int):
            return ToolResult(content="book_id (integer) is required", is_error=True)
        book = self._book_store.get(book_id)
        if book is None:
            return ToolResult(content=f"No book with id {book_id}", is_error=True)

        task = self._book_tasks.get(book_id)
        if task is not None and not task.done():
            task.cancel()

        try:
            stored_dir = Path(book.stored_path).resolve().parent
            if stored_dir.is_relative_to(self._books_dir.resolve()):
                shutil.rmtree(stored_dir, ignore_errors=True)
        except Exception:
            logger.warning("[book_library] could not remove stored file for book_id=%s", book_id, exc_info=True)

        self._book_store.delete(book_id)
        if self._on_trading_change is not None:
            self._on_trading_change()
        return ToolResult(content=json.dumps({"book_id": book_id, "status": "deleted"}))


def create() -> BookLibraryPlugin:
    return BookLibraryPlugin()
