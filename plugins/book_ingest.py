"""Book ingest MCP plugin -- ADR-0025 S1.

book_ingest(path, category): read one local book file (PDF/EPUB/txt/md),
split it into chapters, and extract one idea per chapter into the SHARED
clusters/collections spine (channel.extract_and_cluster) -- so book-sourced
ideas sit alongside video and GitHub ones. Each chapter becomes a source item
row (source_type='book', channel=path) in the same videos table.

Acquisition seams live in cerebral.video.book_source (read_file/parse_pdf),
all injectable so tests need no real files. The store + extraction seam are
the video plugin's -- reused, not forked.

SAFETY: reads local files only; no network, no downloading.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import plugins.video as _video  # reuse the shared VideoStore singleton
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.video import book_meta as _bm
from cerebral.video import book_source as _bs
from cerebral.video import channel as _channel

logger = logging.getLogger(__name__)

PLUGIN_NAME = "book_ingest"

REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read"})


def _chapter_url(path: str, idx: int, title: str) -> str:
    """Stable URL key for a chapter row: path#idx-slug."""
    slug = title[:40].replace(" ", "-").lower()
    return f"{path}#ch{idx}-{slug}"


async def _ingest_book(store, path: str, category: str) -> dict:
    """Read book -> chunk by chapter -> extract each into shared clusters.

    Idempotent: unchanged + already-clustered chapters are skipped.
    """
    try:
        chapters = _bs.extract_chapters(path)
    except ValueError as exc:
        return {"path": path, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("[book] read failed for %s: %s", path, exc)
        return {"path": path, "error": f"Read failed: {exc}"}

    results = []
    skipped = 0
    for idx, ch in enumerate(chapters):
        url = _chapter_url(path, idx, ch["title"])
        text = ch["text"]

        existing = store.get_by_url(url)
        if (
            existing is not None
            and (existing.transcript or "") == text
            and existing.stage in ("extracted", "verified")
        ):
            skipped += 1
            continue

        vid = store.upsert(
            url,
            channel=path,
            collection=category,
            title=ch["title"],
            transcript=text,
            stage="transcribed",
            source_type="book",
        )
        try:
            final = await _channel.extract_and_cluster(
                store, video_id=vid, url=url, transcript=text, collection=category, verify=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[book] extraction failed for %s chapter %d: %s", path, idx, exc)
            final = "transcribed"
        results.append({"chapter": ch["title"], "stage": final})

    return {
        "path": path,
        "collection": category,
        "chapters": len(chapters),
        "extracted": len(results),
        "skipped": skipped,
        "results": results,
    }


class BookIngestPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="book_ingest",
                description=(
                    "Read a local book file (PDF or EPUB) and extract one idea per "
                    "chapter into a collection (category), filed alongside video and "
                    "GitHub-sourced ideas. Path must be a local file -- no downloading. "
                    "Idempotent: unchanged chapters are skipped on re-ingest. "
                    "Supported formats: PDF, EPUB, txt, md."
                ),
                plugin=PLUGIN_NAME,
                required_capabilities=REQUIRED_CAPABILITIES,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to a local PDF, EPUB, txt, or md file.",
                        },
                        "category": {
                            "type": "string",
                            "description": (
                                "Collection to file the extracted ideas under "
                                "(e.g. 'machine learning'). Blank -> 'Uncategorised'."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": "Override book title. Falls back to filename stem when omitted.",
                        },
                        "author": {
                            "type": "string",
                            "description": "Author name.",
                        },
                        "edition": {
                            "type": "string",
                            "description": "Edition (e.g. '2nd Ed').",
                        },
                        "publication_year": {
                            "type": "integer",
                            "description": "Publication year.",
                        },
                        "isbn": {
                            "type": "string",
                            "description": "ISBN (optional).",
                        },
                        "source_tier": {
                            "type": "integer",
                            "description": "Knowledge tier 1-4 (default 3/Practitioner).",
                        },
                    },
                    "required": ["path"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "book_ingest":
            return await self._book_ingest(args)
        if tool_name == "book_list":
            return await self._book_list(args)
        if tool_name == "books_seed_from_csv":
            return await self._books_seed_from_csv(args)
        return ToolResult(content=f"Unknown tool: {tool_name}", is_error=True)

    async def _book_ingest(self, args: dict) -> ToolResult:
        path: str = (args.get("path") or "").strip()
        category: str = (args.get("category") or "").strip() or "Uncategorised"
        title: str = (args.get("title") or "").strip()
        author: str = (args.get("author") or "").strip()
        edition: str = (args.get("edition") or "").strip()
        publication_year = args.get("publication_year")
        isbn: str = (args.get("isbn") or "").strip()
        source_tier = args.get("source_tier")
        if source_tier is None:
            source_tier = 3
        else:
            source_tier = max(1, min(4, int(source_tier)))

        if not path:
            return ToolResult(content="path is required", is_error=True)

        suffix = Path(path).suffix.lower()
        if suffix not in _bs.SUPPORTED_EXTENSIONS:
            return ToolResult(
                content=(
                    f"Unsupported format '{suffix}'. Supported: "
                    + ", ".join(sorted(_bs.SUPPORTED_EXTENSIONS))
                ),
                is_error=True,
            )

        if not title:
            title = Path(path).stem

        store = _video._get_store()
        meta = _bm.BookMetaStore()
        meta.upsert(
            book_id=path, profile_id=args.get("profile_id"), title=title,
            author=author, edition=edition, publication_year=publication_year,
            isbn=isbn, source_tier=source_tier,
        )
        result = await _ingest_book(store, path, category)
        result["title"] = title
        result["author"] = author
        result["source_tier"] = source_tier
        return ToolResult(content=json.dumps(result), is_error=("error" in result))


    async def _book_list(self, args: dict) -> ToolResult:
        profile_id = args.get("profile_id")
        if profile_id is None:
            return ToolResult(content="profile_id is required", is_error=True)
        meta = _bm.BookMetaStore()
        books = meta.list_for_profile(int(profile_id))
        return ToolResult(content=json.dumps(books))

    async def _books_seed_from_csv(self, args: dict) -> ToolResult:
        import csv
        csv_path = (args.get("path") or "").strip()
        if not csv_path:
            return ToolResult(content="path is required", is_error=True)
        from pathlib import Path
        p = Path(csv_path)
        if not p.is_file():
            return ToolResult(content=f"CSV not found: {csv_path}", is_error=True)

        store = _video._get_store()
        meta = _bm.BookMetaStore()
        results = []
        try:
            with open(csv_path, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    title = (row.get("title") or "").strip()
                    author = (row.get("author") or "").strip()
                    file_path = (row.get("file_path") or "").strip()
                    if not file_path:
                        results.append({"title": title, "status": "not_yet_acquired", "skipped": True})
                        continue
                    meta.upsert(
                        book_id=file_path, profile_id=args.get("profile_id"),
                        title=title or Path(file_path).stem, author=author,
                        edition=row.get("edition", ""), isbn=row.get("isbn", ""),
                        source_tier=3, publication_year=row.get("publication_year"),
                    )
                    ing = await _ingest_book(store, file_path, title or "Uncategorised")
                    ing["title"] = title
                    ing["author"] = author
                    ing["skipped"] = False
                    results.append(ing)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(content=f"CSV read failed: {exc}", is_error=True)

        seeded = sum(1 for r in results if not r.get("skipped"))
        return ToolResult(content=json.dumps({"seeded": seeded, "rows": results}))


def create() -> BookIngestPlugin:
    return BookIngestPlugin()
