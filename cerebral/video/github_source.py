"""GitHub repo acquisition -- ADR-0018 S2.

No GitHub API. Repo files come from a shallow ``git clone`` (mirroring how the
video pipeline uses yt-dlp); the front-page description comes from a plain
public-page GET of ``github.com/owner/repo`` (og:description + topics), which are
GitHub metadata absent from the clone.

All network I/O is behind injectable seams (set_clone_fn / set_fetch_page_fn) so
tests need no network and no git binary. ``enumerate_docs`` is pure (reads files
off disk) and is the interesting logic -- the curated doc selection.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── injectable seams ──────────────────────────────────────────────────────────

_clone_fn: Optional[Callable[[str, Path], None]] = None
_fetch_page_fn: Optional[Callable[[str], str]] = None  # url -> html


def set_clone_fn(fn: "Callable[[str, Path], None] | None") -> None:
    global _clone_fn
    _clone_fn = fn


def set_fetch_page_fn(fn: "Callable[[str], str] | None") -> None:
    global _fetch_page_fn
    _fetch_page_fn = fn


def _prod_clone(repo_url: str, dest: Path) -> None:
    # ponytail: live-verify only -- stubbed in tests. Shallow, single commit.
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(dest)],
        check=True, capture_output=True, timeout=300,
    )


def _prod_fetch_page(url: str) -> str:
    # ponytail: live-verify only -- stubbed in tests.
    import urllib.request  # noqa: PLC0415
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- github.com only
        return r.read().decode("utf-8", "replace")


_head_sha_fn: Optional[Callable[[Path], str]] = None


def set_head_sha_fn(fn: "Callable[[Path], str] | None") -> None:
    global _head_sha_fn
    _head_sha_fn = fn


def _prod_head_sha(clone_dir: Path) -> str:
    # ponytail: live-verify only. HEAD of the shallow clone -- stored for S6's
    # ls-remote re-check.
    r = subprocess.run(
        ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return r.stdout.strip()


_ls_remote_fn: Optional[Callable[[str], str]] = None


def set_ls_remote_fn(fn: "Callable[[str], str] | None") -> None:
    global _ls_remote_fn
    _ls_remote_fn = fn


def _prod_ls_remote(repo_url: str) -> str:
    # ponytail: live-verify only. Remote HEAD sha WITHOUT cloning -- the cheap
    # no-API check that drives the up-arrow (ADR-0018 S6).
    r = subprocess.run(
        ["git", "ls-remote", repo_url, "HEAD"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    # Output: "<sha>\tHEAD"
    return r.stdout.split()[0] if r.stdout.strip() else ""


def get_clone_fn() -> "Callable[[str, Path], None]":
    return _clone_fn or _prod_clone


def get_head_sha_fn() -> "Callable[[Path], str]":
    return _head_sha_fn or _prod_head_sha


def get_ls_remote_fn() -> "Callable[[str], str]":
    return _ls_remote_fn or _prod_ls_remote


def get_fetch_page_fn() -> "Callable[[str], str]":
    return _fetch_page_fn or _prod_fetch_page


# ── doc enumeration (pure) ────────────────────────────────────────────────────

MIN_WORDS = 200  # skip stubs / badge-only files

# Denylisted basenames (stem, case-insensitive) -- boilerplate, not content.
_DENY_STEMS = {
    "license", "licence", "changelog", "code_of_conduct", "security",
    "contributing", "codeowners",
}
# README.zh.md / README.pt-br.md -- translated copies of the main README.
_LANG_README = re.compile(r"^readme\.[a-z]{2}(-[a-z]{2})?\.md$", re.I)


def _is_denied(relpath: str) -> bool:
    p = relpath.replace("\\", "/").lower()
    parts = p.split("/")
    if p.startswith(".github/") or "/.github/" in p:
        return True
    # any ancestor dir named test/tests/test_* (drop test-fixture markdown)
    if any(seg == "tests" or seg.startswith("test") for seg in parts[:-1]):
        return True
    name = parts[-1]
    stem = name[:-3] if name.endswith(".md") else name
    if stem in _DENY_STEMS:
        return True
    if _LANG_README.match(name):
        return True
    return False


def _in_scope(relpath: str) -> bool:
    """README.md + other top-level *.md + everything under docs/**. Deep READMEs
    (e.g. src/foo/README.md) are out of scope by design."""
    p = relpath.replace("\\", "/")
    if not p.lower().endswith(".md"):
        return False
    parts = p.split("/")
    if len(parts) == 1:
        return True               # top-level *.md (incl. README.md)
    return parts[0].lower() == "docs"   # docs/**/*.md


def enumerate_docs(clone_dir: "str | Path") -> list[dict]:
    """Curated markdown docs from a cloned repo.

    Scope: README + other top-level ``*.md`` + ``docs/**/*.md``, MINUS the
    boilerplate denylist (LICENSE/CHANGELOG/CODE_OF_CONDUCT/SECURITY/.github/
    tests/translated READMEs) and MINUS files under ``MIN_WORDS`` words.
    Returns ``[{relpath, text}]`` sorted by relpath.
    """
    root = Path(clone_dir)
    out: list[dict] = []
    for md in sorted(root.rglob("*.md")):
        if not md.is_file():
            continue
        rel = md.relative_to(root).as_posix()
        if not _in_scope(rel) or _is_denied(rel):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if len(text.split()) < MIN_WORDS:
            continue
        out.append({"relpath": rel, "text": text})
    return out


# ── front-page description (pure parse over the fetched HTML) ──────────────────

_OG_DESC = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', re.I
)
_META_DESC = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', re.I
)
_TOPIC = re.compile(r'topic-tag[^"]*"[^>]*>\s*([^<]+?)\s*<', re.I)
# GitHub appends "Contribute to owner/repo development by creating an account on
# GitHub." to og:description -- strip that boilerplate tail.
_CONTRIB_TAIL = re.compile(
    r"\s*Contribute to .*? development by creating an account on GitHub\.?\s*$", re.I
)


def fetch_description(repo_url: str) -> dict:
    """Front-page description + topics via a public-page GET (no API).

    Returns ``{description, topics}``. Best-effort: a page with neither meta tag
    yields an empty description (extraction still runs on the docs).
    """
    html = get_fetch_page_fn()(repo_url)
    m = _OG_DESC.search(html) or _META_DESC.search(html)
    desc = _CONTRIB_TAIL.sub("", m.group(1).strip()) if m else ""
    topics = []
    seen = set()
    for t in _TOPIC.findall(html):
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            topics.append(t)
    return {"description": desc, "topics": topics}
