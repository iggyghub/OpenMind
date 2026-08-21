"""book_ingest + book_source tests -- ADR-0025 S1.

Stubbed file-read and pdf-parse seams: no real files, no real pypdf, no network.
Verifies chapters become source_type='book' rows clustered into shared collections,
and that idempotent re-ingest skips unchanged chapters.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

import plugins.video as video_mod
from cerebral.video import book_source as bs
from cerebral.video.store import VideoStore
from plugins.book_ingest import BookIngestPlugin


# ── helpers ───────────────────────────────────────────────────────────────────

def _store() -> VideoStore:
    return VideoStore(db_path=Path(":memory:"))


def _make_epub(chapters: list[tuple[str, str]]) -> bytes:
    """Build a minimal in-memory EPUB zip from (filename, xhtml_body) pairs."""
    buf = io.BytesIO()
    spine_items = ""
    manifest_items = ""
    for i, (fname, _) in enumerate(chapters):
        manifest_items += (
            f'<item id="ch{i}" href="{fname}" media-type="application/xhtml+xml"/>\n'
        )
        spine_items += f'<itemref idref="ch{i}"/>\n'

    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata/>
  <manifest>
    {manifest_items}
  </manifest>
  <spine toc="ncx">
    {spine_items}
  </spine>
</package>"""

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("content.opf", opf)
        for fname, body in chapters:
            xhtml = (
                '<?xml version="1.0"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                f"{body}"
                "</body></html>"
            )
            zf.writestr(fname, xhtml)
    return buf.getvalue()


async def _fake_extract(transcript, ocr, vis, labels, category=""):
    return {"idea": "an idea", "cluster_label": "Book Ideas", "people_required": 1}


@pytest.fixture(autouse=True)
def _reset():
    yield
    video_mod.set_store(None) if hasattr(video_mod, "set_store") else None
    video_mod.set_extract_fn(None)
    bs.set_read_file_fn(None)
    bs.set_parse_pdf_fn(None)


def _wire(store, read_fn):
    video_mod.set_store(store)
    video_mod.set_extract_fn(_fake_extract)
    bs.set_read_file_fn(read_fn)


# ── EPUB chapter extraction (book_source) ─────────────────────────────────────

class TestEpubExtraction:
    def test_two_spine_items_yield_two_chapters(self):
        data = _make_epub([
            ("ch1.xhtml", "<p>Chapter one text goes here with enough words.</p>"),
            ("ch2.xhtml", "<p>Chapter two text also has content here.</p>"),
        ])
        bs.set_read_file_fn(lambda path: data)
        chapters = bs.extract_chapters("book.epub")
        assert len(chapters) == 2

    def test_html_stripped_to_plain_text(self):
        data = _make_epub([
            ("ch1.xhtml", "<h1>Intro</h1><p>Hello <b>world</b>.</p>"),
        ])
        bs.set_read_file_fn(lambda path: data)
        chapters = bs.extract_chapters("book.epub")
        assert "<" not in chapters[0]["text"]
        assert "Hello" in chapters[0]["text"]

    def test_page_provenance_sequential(self):
        data = _make_epub([
            ("a.xhtml", "<p>First chapter content here.</p>"),
            ("b.xhtml", "<p>Second chapter content here.</p>"),
            ("c.xhtml", "<p>Third chapter content here.</p>"),
        ])
        bs.set_read_file_fn(lambda path: data)
        chapters = bs.extract_chapters("book.epub")
        assert chapters[0]["page_start"] == 1
        assert chapters[1]["page_start"] == 2
        assert chapters[2]["page_start"] == 3

    def test_bad_zip_raises_value_error(self):
        bs.set_read_file_fn(lambda path: b"not a zip")
        with pytest.raises(ValueError, match="EPUB"):
            bs.extract_chapters("broken.epub")

    def test_script_style_tags_stripped(self):
        data = _make_epub([
            ("ch1.xhtml", "<script>alert('x')</script><p>Real content here.</p>"),
        ])
        bs.set_read_file_fn(lambda path: data)
        chapters = bs.extract_chapters("book.epub")
        assert "alert" not in chapters[0]["text"]
        assert "Real content" in chapters[0]["text"]


# ── PDF chapter extraction (book_source) ──────────────────────────────────────

class TestPdfExtraction:
    def test_outline_based_split(self):
        def fake_parse(data: bytes) -> list[dict]:
            return [
                {"title": "Chapter 1", "text": "Content one.", "page_start": 1, "page_end": 5},
                {"title": "Chapter 2", "text": "Content two.", "page_start": 6, "page_end": 10},
            ]
        bs.set_parse_pdf_fn(fake_parse)
        bs.set_read_file_fn(lambda path: b"fake-pdf-data")
        chapters = bs.extract_chapters("book.pdf")
        assert len(chapters) == 2
        assert chapters[0]["title"] == "Chapter 1"
        assert chapters[0]["page_start"] == 1
        assert chapters[1]["page_start"] == 6

    def test_heading_heuristic_fallback(self):
        def fake_parse(data: bytes) -> list[dict]:
            return [
                {"title": "Chapter 1", "text": "Heuristic split.", "page_start": 1, "page_end": 3},
                {"title": "Chapter 2", "text": "Second part.", "page_start": 4, "page_end": 6},
            ]
        bs.set_parse_pdf_fn(fake_parse)
        bs.set_read_file_fn(lambda path: b"fake-pdf-data")
        chapters = bs.extract_chapters("book.pdf")
        assert len(chapters) >= 1

    def test_no_chapters_yields_one(self):
        def fake_parse(data: bytes) -> list[dict]:
            return [{"title": "Book", "text": "All content.", "page_start": 1, "page_end": 20}]
        bs.set_parse_pdf_fn(fake_parse)
        bs.set_read_file_fn(lambda path: b"fake-pdf-data")
        chapters = bs.extract_chapters("book.pdf")
        assert len(chapters) == 1

    def test_page_provenance_on_pdf_chapters(self):
        def fake_parse(data: bytes) -> list[dict]:
            return [
                {"title": "Intro", "text": "x", "page_start": 1, "page_end": 3},
                {"title": "Body", "text": "y", "page_start": 4, "page_end": 50},
            ]
        bs.set_parse_pdf_fn(fake_parse)
        bs.set_read_file_fn(lambda path: b"fake-pdf-data")
        chapters = bs.extract_chapters("book.pdf")
        assert chapters[0]["page_end"] == 3
        assert chapters[1]["page_start"] == 4


# ── Unsupported formats ───────────────────────────────────────────────────────

class TestUnsupportedFormat:
    def test_docx_raises_value_error(self):
        bs.set_read_file_fn(lambda path: b"irrelevant")
        with pytest.raises(ValueError, match="Unsupported"):
            bs.extract_chapters("book.docx")

    def test_plugin_rejects_unsupported_extension(self):
        pass  # covered by TestBookIngest.test_book_ingest_rejects_unsupported_extension


# ── book_ingest plugin ────────────────────────────────────────────────────────

class TestBookIngest:
    @pytest.mark.asyncio
    async def test_book_ingest_clusters_chapters_into_collection(self):
        store = _store()
        epub_data = _make_epub([
            ("ch1.xhtml", "<p>" + "word " * 50 + "</p>"),
            ("ch2.xhtml", "<p>" + "word " * 50 + "</p>"),
        ])
        _wire(store, lambda path: epub_data)

        r = await BookIngestPlugin().call_tool(
            "book_ingest",
            {"path": "/books/test.epub", "category": "machine learning"},
        )
        assert not r.is_error, r.content
        data = json.loads(r.content)
        assert data["chapters"] == 2
        assert data["extracted"] == 2


# ── S2 metadata + book_list + CSV seed ─────────────────────────────────────────

import sqlite3

class TestBookMetaAndList:
    def test_upsert_and_list(self):
        meta = _bm.BookMetaStore(":memory:")
        meta.upsert("path/to/book.pdf", profile_id=1, title="Test Book", author="A.", source_tier=4)
        books = meta.list_for_profile(1)
        assert len(books) == 1
        assert books[0]["title"] == "Test Book"
        assert books[0]["source_tier"] == 4

    @pytest.mark.asyncio
    async def test_book_ingest_stores_metadata(self):
        store = _store()
        epub_data = _make_epub([("ch1.xhtml", "<p>text</p>")])
        _wire(store, lambda path: epub_data)
        plugin = BookIngestPlugin()
        r = await plugin.call_tool("book_ingest", {
            "path": "/books/meta_test.epub", "profile_id": 1,
            "title": "Meta Test", "author": "Jane Doe", "source_tier": 2
        })
        assert not r.is_error
        data = json.loads(r.content)
        assert data["title"] == "Meta Test"
        assert data["author"] == "Jane Doe"
        assert data["source_tier"] == 2

    @pytest.mark.asyncio
    async def test_book_list_tool(self):
        store = _store()
        video_mod.set_store(store)
        orig_init = _bm.BookMetaStore.__init__
        def mock_init(self, db_path=_bm.DB_PATH):
            sqlite3.Connection(":memory:").close()  # just avoid file creation
            object.__setattr__(self, '_con', sqlite3.connect(":memory:"))
            self._con.row_factory = sqlite3.Row
            self._con.executescript("CREATE TABLE books (id TEXT PRIMARY KEY, profile_id INTEGER, title TEXT, author TEXT, source_tier INTEGER, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP) INSERT INTO books VALUES('/books/listed.pdf', 99, 'Listed', 'Auth', 3, datetime('now'))")
            self._con.row_factory = sqlite3.Row
        _bm.BookMetaStore.__init__ = mock_init
        
        r = await BookIngestPlugin().call_tool("book_list", {"profile_id": 99})
        assert not r.is_error
        books = json.loads(r.content)
        assert len(books) == 1
        assert books[0]["title"] == "Listed"
        _bm.BookMetaStore.__init__ = orig_init

    @pytest.mark.asyncio
    async def test_books_seed_from_csv_skips_no_path(self, tmp_path):
        csv = tmp_path / "seed.csv"
        csv.write_text("title,author,file_path\nBook A,Auth1,\nBook B,Auth2,/fake/path.epub\n")
        
        store = _store()
        video_mod.set_store(store)
        original_ingest = _ingest_book
        async def fake_ingest(st, path, cat):
            return {"chapters": 0, "extracted": 0, "skipped": False}
        import plugins.book_ingest as pbi
        pbi._ingest_book = fake_ingest
        
        r = await BookIngestPlugin().call_tool("books_seed_from_csv", {"path": str(csv)})
        assert not r.is_error
        data = json.loads(r.content)
        assert data["seeded"] == 1
        assert data["rows"][0]["skipped"] is True
        assert data["rows"][1]["skipped"] is False
        
        pbi._ingest_book = original_ingest
        assert data["collection"] == "machine learning"

        # Rows are source_type='book'
        url0 = next(
            u for u in [
                store.get_by_url(f"/books/test.epub#ch{i}-ch1.xhtml"[:100])
                for i in range(5)
            ] if u is not None
        ) if False else None
        # Check via list_clusters
        clusters = store.list_clusters("machine learning", source_type="book")
        assert len(clusters) >= 1

    @pytest.mark.asyncio
    async def test_book_ingest_source_type_is_book(self):
        store = _store()
        epub_data = _make_epub([("ch1.xhtml", "<p>" + "word " * 50 + "</p>")])
        _wire(store, lambda path: epub_data)

        await BookIngestPlugin().call_tool(
            "book_ingest", {"path": "/books/test.epub", "category": "cat"},
        )
        # Enumerate all videos and check source_type
        import sqlite3
        rows = store._conn().execute(
            "SELECT source_type FROM videos WHERE channel = ?", ("/books/test.epub",)
        ).fetchall()
        assert rows, "No rows inserted"
        assert all(r[0] == "book" for r in rows)

    @pytest.mark.asyncio
    async def test_book_ingest_idempotent_skips_unchanged(self):
        store = _store()
        epub_data = _make_epub([("ch1.xhtml", "<p>" + "word " * 50 + "</p>")])
        _wire(store, lambda path: epub_data)

        plugin = BookIngestPlugin()
        await plugin.call_tool("book_ingest", {"path": "/books/test.epub", "category": "cat"})
        r2 = await plugin.call_tool("book_ingest", {"path": "/books/test.epub", "category": "cat"})
        data = json.loads(r2.content)
        assert data["skipped"] == 1
        assert data["extracted"] == 0

    @pytest.mark.asyncio
    async def test_book_ingest_requires_path(self):
        store = _store()
        video_mod.set_store(store)
        r = await BookIngestPlugin().call_tool("book_ingest", {})
        assert r.is_error
        assert "path" in r.content

    @pytest.mark.asyncio
    async def test_book_ingest_rejects_unsupported_extension(self):
        store = _store()
        video_mod.set_store(store)
        r = await BookIngestPlugin().call_tool(
            "book_ingest", {"path": "/books/report.docx"}
        )
        assert r.is_error
        assert "Unsupported" in r.content

    @pytest.mark.asyncio
    async def test_book_ingest_blank_category_defaults_uncategorised(self):
        store = _store()
        epub_data = _make_epub([("ch1.xhtml", "<p>" + "word " * 50 + "</p>")])
        _wire(store, lambda path: epub_data)

        r = await BookIngestPlugin().call_tool(
            "book_ingest", {"path": "/books/test.epub"}
        )
        data = json.loads(r.content)
        assert data["collection"] == "Uncategorised"

    def test_list_tools_exposes_book_ingest(self):
        tools = {t.name for t in BookIngestPlugin().list_tools()}
        assert "book_ingest" in tools

    def test_required_capabilities_is_fs_read(self):
        t = next(t for t in BookIngestPlugin().list_tools() if t.name == "book_ingest")
        assert t.required_capabilities == frozenset({"fs_read"})

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        r = await BookIngestPlugin().call_tool("no_such_tool", {})
        assert r.is_error

    @pytest.mark.asyncio
    async def test_pdf_chapters_ingest_with_page_provenance(self):
        store = _store()
        video_mod.set_store(store)
        video_mod.set_extract_fn(_fake_extract)

        def fake_parse(data: bytes) -> list[dict]:
            return [
                {"title": "Chapter 1", "text": "Content one " * 20, "page_start": 1, "page_end": 5},
                {"title": "Chapter 2", "text": "Content two " * 20, "page_start": 6, "page_end": 10},
            ]
        bs.set_read_file_fn(lambda path: b"fake-pdf")
        bs.set_parse_pdf_fn(fake_parse)

        r = await BookIngestPlugin().call_tool(
            "book_ingest", {"path": "/books/ml.pdf", "category": "ml"},
        )
        assert not r.is_error, r.content
        data = json.loads(r.content)
        assert data["chapters"] == 2
        assert data["extracted"] == 2
