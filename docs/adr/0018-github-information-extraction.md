# ADR-0018: GitHub information extraction — Felix reads repos to learn from them

**Date:** 2026-08-14
**Status:** Accepted (grill session)

## Context

Felix watches videos to understand and learn from them (ADR-0017): a source is
acquired, transcribed/read, an idea is extracted, clustered into a **collection**,
and — on the user's explicit commit — promoted to Memory. GitHub repositories are
another source of the same thing: the docs in a repo carry techniques, patterns,
and ideas the user wants Felix to learn and file alongside the video-sourced ones.

The reusable thing here is **not** "a GitHub scraper." It is the same
extraction spine ADR-0017 already built. GitHub is the *second source* feeding
that spine — the first caller was a TikTok channel, this is a repo. Build the new
acquisition front-end; ride the existing clusters/collections/Memory machinery.

Two constraints from the user shape every decision:

1. **No GitHub API.** Mirror how the video pipeline uses `yt-dlp` — a no-key CLI
   fetch of public content — rather than a token-gated REST API. The no-API
   acquisition is `git clone` for repo files and a public-page GET for the
   front-page description (both are public fetches, not the API).
2. **Doc-level granularity.** Each curated markdown doc is its own extraction
   unit, not one idea per repo — the user wants the granular ideas, and the noise
   is dammed at doc-selection time, not by collapsing to repo-level.

## Decision

1. **GitHub is a new source on the existing spine, not a silo.** Reuse the
   `videos` / `video_ideas` / `video_clusters` tables. Add a `source_type` column
   to `videos` (default `'video'`; GitHub docs = `'github'`); `channel` = the repo
   URL, so a "repo" is the set of `source_type='github'` rows sharing that channel.
   A doc is a source item exactly like a video: a row with a URL
   (`repo_url#path/to/doc.md`), `transcript` = the markdown, a `stage`, and one
   extracted idea. `channel.extract_and_cluster()` runs on it unchanged, so
   clusters, collections, verdicts, and commit-to-Memory are **shared** with
   video. A small `github_repos(repo_url PK, head_sha, description, updated_at)`
   side-table holds git/page state that doesn't belong in the shared tables. The
   `videos` table is now really a "text source item" table; the rename to
   `sources` is deferred until the abstraction earns it (YAGNI).

2. **No-API acquisition, two paths.** (a) `git clone --depth 1` into a temp dir
   for the repo files (stateless — read the docs, delete the clone). (b) A plain
   public-page GET of `github.com/owner/repo` for the front-page description
   (`og:description` meta) and topics, which are GitHub metadata absent from the
   clone. Both are injectable seams (like `set_download_fn` for yt-dlp) so tests
   need no network. Same capability posture as yt-dlp: `external_data_read` +
   temp `fs_write`, consent per ADR-0005.

3. **Curated doc set.** Extract `README.md` + everything under `docs/**/*.md` +
   other top-level `*.md`, **minus** a boilerplate denylist (`LICENSE`,
   `CHANGELOG`, `CODE_OF_CONDUCT`, `SECURITY`, `.github/`, `*/test*`, translated
   `README.<lang>.md`) **and** a minimum-content floor (skip files under ~200
   words). The front-page description is **grounding context**, not its own idea:
   it is prepended to each doc's text before extraction (`"Repo: <name> —
   <description> (topics: …)"`) and shown as the repo's subtitle in the panel.

4. **Content-aware, cross-source clustering.** Clustering is scoped to the
   **collection**, not the source: a GitHub doc extracted into "harness
   improvements" sees the existing clusters in that whole collection (video ones
   included) and merges into them rather than spawning a github-only twin. The
   extractor is fed a representative idea per existing cluster
   (`get_cluster_idea_text()`), so it merges on *meaning*, not just label wording.
   This upgrade lives in the shared `extract_and_cluster()` and improves the video
   pipeline too.

5. **User-initiated re-check, never a silent background watch.** Watching a repo
   for a moving `main` re-pulls content from a source the user is no longer
   vetting — the "their account gets compromised later" supply-chain risk. Instead:
   store the repo HEAD SHA at ingest; a cheap `git ls-remote <repo> HEAD` (no API,
   no clone) on panel refresh compares it. When the remote HEAD has moved, an
   **up-arrow appears** on that repo; clicking it re-clones and extracts only
   new/changed docs (content compare), then stores the new SHA. Nothing enters
   Memory without the user's explicit commit, so a poisoned doc is at worst a
   *candidate* the user still gates. Deleted docs are left alone (never
   auto-removed — they may already be committed).

6. **Separate GitHub tab, shared clusters.** A "GitHub" sub-tab under Library
   beside "Videos" (same `.lib-tab`/`.lib-sub` pattern), fed by a `plugins/github_*`
   panel spec. Views are **source-scoped** — the GitHub tab shows clusters that
   contain ≥1 github-sourced idea (`list_clusters(source_type='github')`); a mixed
   cluster appears in **both** tabs as the one shared entity. The tab reuses the
   existing cluster / group / move / commit widgets (ADR-0012 panel spec + the
   manual-move UI), and adds a repo ingest form and a repos list with up-arrows.

## Consequences

- **Small diff, large reuse.** One column + one side-table + a new acquisition
  front-end; the entire extract → cluster → collection → Memory → panel spine is
  reused, including the manual-move UI shipped this cycle.
- **`videos` table is overloaded** as a generic source-item table. Accepted; the
  `sources` rename is a follow-up if a third source appears.
- **A second no-API acquisition path** (public-page fetch for the description)
  sits alongside `git clone`. Both are public fetches, consistent with the no-API
  rule.
- **Prompt-injection / idea-poisoning is the real threat surface** (untrusted
  markdown → extraction LLM → candidate idea), the same surface video transcripts
  already have. It is contained by the manual commit-to-Memory gate and by
  user-initiated (not background) re-checks.
- **`--depth 1` still clones the whole tree.** Some waste on large repos; sparse
  checkout (`*.md` only) is a later optimization, not built now.

## Alternatives considered

- **Generalize `videos` → `sources` now.** Cleaner name, but a broad refactor of
  the whole video pipeline for a naming win. Deferred (decision 1).
- **GitHub REST API via `gh api`.** Structured and easy, but token-gated and
  against the user's no-API rule. Rejected.
- **Background watch of a moving `main`.** Silent re-trust of a source no longer
  vetted. Replaced by the user-initiated up-arrow (decision 5).
- **Repo-level extraction (one idea per repo).** Too coarse; loses the granular
  ideas doc-level gives. Rejected.

## Slices (tracer-bullet)

- **S1 — Store:** `source_type` column on `videos`, `github_repos` side-table,
  `list_clusters(source_type=…)` filter. Migration + tests.
- **S2 — Acquisition seams:** shallow `git clone`, curated doc enumeration
  (denylist + word-floor), front-page description fetch. Pure/injectable, no
  network in tests.
- **S3 — `github_ingest(repo_url, category)` — the tracer bullet:** wire S2 →
  `extract_and_cluster` end-to-end for one repo; docs become `source_type='github'`
  rows, ideas land in shared clusters.
- **S4 — Content-aware merge (decision 4):** feed a representative idea per
  existing cluster into `extract_and_cluster` (benefits video too).
- **S5 — GitHub panel:** Library sub-tab beside Videos; panel spec with the ingest
  form, repos list, and source-scoped clusters reusing the cluster/move widgets.
- **S6 — Up-arrow re-check:** `git ls-remote` SHA compare, `github_repos.head_sha`,
  re-ingest action with per-doc dedup.
