# Books live-verify checklist

Steps that require a real book file, real model call, or real ChromaDB index
and cannot be performed in the automated loop session. Check each manually
after the relevant slice lands.

## S1 -- book_ingest core

- [ ] **Real EPUB ingest:** `book_ingest(path="/path/to/book.epub", category="test")` --
  verify chapters appear in the shared clusters panel under the "test" collection,
  `source_type="book"` rows in `openmind.db`, page provenance on each row.
- [ ] **Real PDF ingest (with outline):** Use a PDF that has bookmarks/outline -- confirm
  the outline-based chapter split fires (chapters match the bookmark titles).
- [ ] **Real PDF ingest (heuristic):** Use a PDF without bookmarks -- confirm the
  heading-pattern heuristic splits on "Chapter N" headings, or falls back to
  whole-doc if none match.
- [ ] **Idempotent re-ingest:** Run `book_ingest` twice on the same file -- confirm the
  second run reports `skipped=N, extracted=0` with no duplicate rows.
- [ ] **Unsupported format:** Run `book_ingest(path="report.docx")` -- confirm a clear
  error message ("Unsupported format") is returned, no crash.
