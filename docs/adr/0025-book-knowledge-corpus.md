# 25. Book knowledge corpus: chapter-chunked ingest on the video/github spine, phased claim/evidence extraction

Date: 2026-08-21
Status: accepted (campaign-scaffold session, issues #TBD)

## Context

Felix can already ingest and organize two source types through one shared pipeline:
`cerebral/video/channel.py`'s `extract_and_cluster()`, called by `plugins/video.py`
(videos) and `plugins/github_ingest.py` (repo docs) against the same `videos` table
(`source_type` discriminates). Each call chunks a source into short units, extracts
one idea per unit, and files it into a collection/cluster.

The user wants a third source: books (PDF/EPUB), read and organized the same way —
but supplied a 32-section spec for a full research knowledge-graph over the corpus:
per-passage claim/evidence/concept/method/formula/case-study extraction, a claim
graph (supports/contradicts/depends_on), contradiction detection across sources,
author/source-tier metadata, edition-aware versioning, and multi-index retrieval
that keeps "author claim" distinct from "empirically demonstrated" and from Felix's
own inference at answer time.

That is a large, multi-layer system. Building the graph layers before a working
ingest exists is exactly the kind of speculative infrastructure Felix's other
campaigns (Documents, Skills) avoid — they land a working core first, UI and
richer capability after.

## Decision

1. **Reuse the video/github spine, don't fork it.** `plugins/book_ingest.py` mirrors
   `plugins/github_ingest.py`: acquire text, chunk it, call the shared
   `_channel.extract_and_cluster()` per chunk with `source_type="book"`, same
   `videos` table, same clusters/collections UI. A book is chunked **by chapter**,
   not fixed token windows (EPUB spine gives real chapter boundaries for free;
   PDF chapters are heuristically detected from the outline/bookmarks when present,
   falling back to heading-pattern detection). Page/paragraph provenance is kept on
   every chunk from S1 on — cheap now, required later for claim provenance, not
   worth re-deriving in a later slice.
2. **Acquisition formats, ranked by reliability.** EPUB (stdlib `zipfile` +
   `html.parser` — real chapter structure, no new dependency) is preferred.
   PDF needs a new lightweight dependency (`pypdf`) — the first PDF-parsing need in
   this repo; text/markdown reuse the existing doc-reading path from
   `github_ingest`. No OCR, no scanned-image support — out of scope.
3. **The corpus is phased, not built whole.** This ADR reserves the full object
   model (concepts, claims, evidence, methods, formulas, case studies, claim graph,
   contradiction detection, multi-index retrieval, answer-time citation) but only
   Phase 1 (S1-S2: ingest, chunk, cluster, library UI) lands as part of this
   campaign's first push. Phases 2-5 are filed as the campaign's remaining slices
   (S3-S8, see `BOOKS.md`) and executed by the same loop, in order — each phase
   only makes sense once the one before it has real data to run against.
4. **New tables, same database.** `openmind.db` gains `book_concepts`,
   `book_claims`, `book_evidence`, `book_methods`, `book_formulas`,
   `book_case_studies`, and `book_claim_edges` (S3-S6), profile-scoped like
   `DocumentStore`. Every row carries `book_id` + chapter/page/paragraph
   provenance back to the source chunk — an extracted claim with no recoverable
   passage is a bug, not an edge case (spec's provenance rule).
5. **Claims are never merged with facts or with Felix's inference.** A `claim_type`
   (factual/empirical/theoretical/causal/predictive/methodological/normative/
   opinion/anecdotal/historical/definitional) and `evidence_type` are stored on
   every claim (S4). Retrieval and answer-time synthesis (S8) must present "author
   X claims Y" distinctly from "Y is empirically supported" and from Felix's own
   synthesis — never silently collapsed into one voice.
6. **Contradictions are linked, never resolved.** `book_claim_edges` (S6) stores
   typed relations (`supports`/`contradicts`/`depends_on`/`supported_by`/
   `derived_from`) between claims/evidence/assumptions. When two claims conflict,
   Felix surfaces both with their sources and evidence — it does not pick a winner
   by authority. Source tier (1 Primary .. 4 Opinion/Anecdotal, S2) is retrieval
   context, never a per-author truth score.
7. **Retrieval stays layered, not one vector index.** S7 builds separate indexes
   (raw text, concepts, claims, methods, formulas, evidence, case studies) so a
   research question can pull concept definitions, claims, evidence, and known
   criticisms as distinct result sets instead of one blended similarity search.
8. **UI.** A Book library panel (S2) mirrors the Documents panel: list, ingestion
   status, browse by cluster/chapter. Concept/claim browsing UI is deferred past S8
   — read access via tool calls is the v1 bar, same as Documents shipped CRUD
   before its sidebar panel.

## Consequences

- Phase 1 (S1-S2) is a small, safe slice: no network calls beyond what
  `github_ingest` already does (none — books are local files), one new dependency
  (`pypdf`), reuses proven extraction/dedup/clustering code.
- Phases 2+ are genuinely valuable per the user's spec but explicitly gated on
  Phase 1 landing and being used — if book ingest turns out to need a different
  chunking or metadata shape once real books go through it, the graph layers built
  on top of the wrong shape would be wasted work. Reordering the queue after S2 is
  expected and fine.
- `book_claim_edges` contradiction detection (S6) requires cross-book claim
  comparison — likely an LLM-judge pass over claim pairs sharing a concept, not
  full pairwise comparison of the whole corpus. Left to S6 to spec precisely once
  S3-S5 show how many claims per book actually land.
- No claim/evidence/concept object is presented to the user as verified fact by
  Felix; source tier and claim type ride along at retrieval and answer time so the
  distinction in spec rule #27 ("books are a knowledge source, not ground truth")
  holds without a separate enforcement layer.
