# BOOKS.md -- Book knowledge corpus campaign driver

Design: `docs/adr/0025-book-knowledge-corpus.md` + CONTEXT.md ("Book knowledge corpus").
Scaffolded 2026-08-21 from a user-supplied full research-corpus spec (claims,
evidence, concepts, claim graph, contradiction detection). Phased: S1-S2 land a
working ingest+organize slice on the existing video/github clusters spine; S3-S8
build the extraction/graph/retrieval layers on top, each gated on the one before it
having real data.

## Status: ready

## Next slice -- start here

- **Active:** S1 -- #797
- **Model:** sonnet

## Queue

- [ ] S1 -- #797 -- book_ingest core: PDF/EPUB chapter chunking into the video/github spine
- [ ] S2 -- #798 -- book metadata (author/tier/edition) + Book library panel
- [ ] S3 -- #799 -- concept extraction per chapter
- [ ] S4 -- #800 -- claim + assumption extraction
- [ ] S5 -- #801 -- method, formula, and case-study extraction
- [ ] S6 -- #802 -- claim graph + contradiction detection
- [ ] S7 -- #803 -- layered retrieval across raw text / concepts / claims / evidence
- [ ] S8 -- #804 -- answer-time citation: claim vs fact vs inference

Per-slice model: sonnet unless the queue entry says otherwise. When ticking a slice,
set the next entry's model on the `Model:` line above.

## Landed PRs

## SAFETY

- NEVER fetch anything over the network for book acquisition -- `book_ingest` reads a
  local file path the user gives it. No downloading books, no scraping ebook sites.
- NEVER invoke a real LLM/Budd call in tests -- all extraction (S3-S6) goes through the
  same injectable model-routing seam `extract_and_cluster` already uses; stub it.
- NEVER call real `pypdf` parsing or real ChromaDB in unit tests -- inject fixtures/stubs
  per the seam conventions `github_ingest`/`video`/memory tests already use.
- Claims/concepts/evidence extracted from books are never presented as Felix's own
  belief or as verified fact -- ADR-0025 section 5/6/8 and CONTEXT.md's "Claim" /
  "Claim graph" entries govern this; do not collapse "author claims X" into "X is true"
  anywhere in the extraction or retrieval code.
- Contradiction detection (S6) never auto-resolves or ranks by source tier -- surfacing
  both sides with sources is the entire scope; do not add a "best answer" heuristic.
- Seam rule (#153/#385): no `from plugins.<x> import ...` inside cerebral/ -- wire
  through _wire_plugin_seams against _orc.get_plugin_module.
- Operator .ps1 scripts: ASCII-only bodies, pause-on-exit + -NoPause switch (CLAUDE.md
  rules).
- A slice that needs a real book file, real model call, or real ChromaDB index to
  verify -> APPEND a checklist item to docs/books-live-verify.md instead of performing
  it in the loop session.
