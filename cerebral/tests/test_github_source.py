"""Acquisition-seam tests for ADR-0018 S2. Pure/injectable -- no network, no git."""
from __future__ import annotations

from pathlib import Path

from cerebral.video import github_source as gs


def _write(root: Path, rel: str, words: int) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# doc\n\n" + " ".join(["word"] * words), encoding="utf-8")


# ── enumerate_docs (the curated selection) ────────────────────────────────────

def test_enumerate_docs_curated_selection(tmp_path):
    _write(tmp_path, "README.md", 300)                 # in: top-level readme
    _write(tmp_path, "docs/guide.md", 300)             # in: docs/**
    _write(tmp_path, "docs/deep/advanced.md", 300)     # in: docs/** recursive
    _write(tmp_path, "OVERVIEW.md", 300)               # in: other top-level *.md
    _write(tmp_path, "LICENSE.md", 300)                # out: denylist
    _write(tmp_path, "CHANGELOG.md", 300)              # out: denylist
    _write(tmp_path, "README.zh.md", 300)              # out: translation
    _write(tmp_path, ".github/PULL_REQUEST_TEMPLATE.md", 300)  # out: .github
    _write(tmp_path, "tests/fixture.md", 300)          # out: tests dir
    _write(tmp_path, "src/mod/README.md", 300)         # out: deep readme (not docs/, not top-level)
    _write(tmp_path, "docs/stub.md", 10)               # out: under word-floor

    rels = {d["relpath"] for d in gs.enumerate_docs(tmp_path)}
    assert rels == {"README.md", "OVERVIEW.md", "docs/guide.md", "docs/deep/advanced.md"}


def test_enumerate_docs_returns_text(tmp_path):
    _write(tmp_path, "README.md", 250)
    docs = gs.enumerate_docs(tmp_path)
    assert len(docs) == 1
    assert "word" in docs[0]["text"]


def test_enumerate_docs_empty_repo(tmp_path):
    assert gs.enumerate_docs(tmp_path) == []


# ── fetch_description (public-page parse, seam-injected) ───────────────────────

_HTML = """
<html><head>
<meta property="og:description" content="A tiny agent harness. Contribute to acme/harness development by creating an account on GitHub.">
</head><body>
<a class="topic-tag topic-tag-link" href="/topics/ai">ai</a>
<a class="topic-tag topic-tag-link" href="/topics/agents"> agents </a>
<a class="topic-tag topic-tag-link" href="/topics/ai">ai</a>
</body></html>
"""


def test_fetch_description_parses_and_strips_boilerplate(monkeypatch):
    gs.set_fetch_page_fn(lambda url: _HTML)
    try:
        out = gs.fetch_description("https://github.com/acme/harness")
        assert out["description"] == "A tiny agent harness."   # contrib tail stripped
        assert out["topics"] == ["ai", "agents"]               # deduped, trimmed
    finally:
        gs.set_fetch_page_fn(None)


def test_fetch_description_missing_meta_is_empty():
    gs.set_fetch_page_fn(lambda url: "<html><head></head><body>no meta</body></html>")
    try:
        out = gs.fetch_description("https://github.com/x/y")
        assert out["description"] == ""
        assert out["topics"] == []
    finally:
        gs.set_fetch_page_fn(None)


# ── seams are injectable ──────────────────────────────────────────────────────

def test_clone_seam_injectable(tmp_path):
    calls = {}
    gs.set_clone_fn(lambda url, dest: calls.update(url=url, dest=dest))
    try:
        gs.get_clone_fn()("https://github.com/a/b", tmp_path)
        assert calls["url"] == "https://github.com/a/b"
    finally:
        gs.set_clone_fn(None)
