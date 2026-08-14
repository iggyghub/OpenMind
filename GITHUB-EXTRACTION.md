# GitHub Information Extraction — campaign driver

Felix reads GitHub repos to learn from them, feeding the **same** idea →
cluster → collection → Memory spine as the video pipeline (ADR-0017). GitHub is
a new *source*, not a silo. Design: [ADR-0018](docs/adr/0018-github-information-extraction.md).

No API — `git clone` for docs + a public-page GET for the front-page description
(mirrors how video uses yt-dlp). Doc-level: each curated markdown file = one idea.

## Next slice — start here

**S1 (#699)** — no dependencies. Grab it first.

## Slices

| Slice | Issue | Depends on | What |
|-------|-------|-----------|------|
| S1 | #699 | — | Store: `source_type` col + `github_repos` table + `list_clusters(source_type)` |
| S2 | #700 | — | Acquisition seams: shallow clone + curated doc enum + description fetch |
| S3 | #701 | S1, S2 | **Tracer bullet:** `github_ingest(repo, category)` → shared clusters |
| S4 | #702 | S1 | Content-aware cross-source clustering (shared `extract_and_cluster`) |
| S5 | #703 | S1, S3 | GitHub panel: Library sub-tab + shared cluster/move widgets |
| S6 | #704 | S1, S3, S5 | Up-arrow re-check (`git ls-remote`) + user-initiated re-ingest |

S1 and S2 are independent — either can go first. S3 is the thinnest end-to-end
slice; land it before the panel (S5) and re-check (S6).

## Rules (per repo conventions)

- One PR per issue; body includes `Closes #N`. Standalone branches off `master`.
- Reuse the existing spine — do NOT fork a parallel github store. `videos` table
  + `source_type`; `channel` = repo URL; `channel.extract_and_cluster` unchanged
  (except S4's shared upgrade).
- ASCII-only in any `.ps1`; pause-on-exit for double-click scripts (CLAUDE.md).
- Each slice leaves its tests green; verify the full suite, not just per-slice.

## Live-verify targets

- S3: `github_ingest` on a small real repo → `source_type='github'` rows + clusters
  in the chosen collection, merging into existing same-collection clusters.
- S6: up-arrow appears only after the repo HEAD moves; re-ingest extracts only
  changed docs.
