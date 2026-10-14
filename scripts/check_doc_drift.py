#!/usr/bin/env python3
"""
Validate that backtick-quoted source file paths in documentation exist.

Scans docs/adr/*.md and CONTEXT.md for paths enclosed in backticks.
Verifies each referenced path exists relative to the repository root.
Prints orphaned citations to stdout and exits non-zero if any are found.

Usage:
    python scripts/check_doc_drift.py
    python scripts/check_doc_drift.py --base /path/to/repo
    python scripts/check_doc_drift.py docs/adr/1.md docs/adr/2.md
"""
import argparse
import re
import sys
from pathlib import Path

# A repo-relative source file path: path segments of word/dot/dash characters,
# ending in a known source extension, optionally followed by a :line or
# :line-line suffix (e.g. "cerebral/main.py:1127"). Deliberately excludes
# anything starting with "/" (slash-commands like "/grill-me"), URLs
# ("ws://localhost:7766", "accounts.google.com/..."), and placeholders
# ("plugins/<name>.py") -- those aren't file citations even though they
# contain a "/" or ".md"-like substring.
_SOURCE_EXTS = (
    "py|md|js|ts|jsx|tsx|json|ya?ml|ps1|sh|html|css|toml|cfg|ini|txt"
)
# Requires at least one "/" -- a bare filename like "main.py" is too ambiguous
# to resolve (which of the repo's several main.py?) and ADRs cite those in
# prose without a directory; only a real relative path is checkable here.
_PATH_RE = re.compile(
    rf"^[A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.\-]*)+\.({_SOURCE_EXTS})$"
)
_LINE_SUFFIX_RE = re.compile(r":\d+(-\d+)?$")


def extract_backtick_paths(file: Path) -> list[str]:
    """Extract backtick-quoted source file paths from a markdown file."""
    text = file.read_text(encoding="utf-8")
    matches = re.findall(r"`([^`]+)`", text)
    paths = []
    for m in matches:
        base = _LINE_SUFFIX_RE.sub("", m)
        if _PATH_RE.match(base):
            paths.append(base)
    return paths


def validate_paths(paths: list[str], base_dir: Path) -> list[str]:
    """Check if each path exists relative to the base directory."""
    orphans = []
    for file_path in paths:
        resolved = (base_dir / file_path).resolve()
        if not resolved.exists():
            orphans.append(file_path)
    return orphans


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check for broken file references in documentation."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to scan. If empty, scans docs/adr/*.md and CONTEXT.md.",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("."),
        help="Base directory to resolve paths against (default: current dir).",
    )
    args = parser.parse_args()

    base_dir = args.base.resolve()

    if not args.files:
        adr_dir = base_dir / "docs" / "adr"
        context_file = base_dir / "CONTEXT.md"

        if adr_dir.is_dir():
            args.files = list(adr_dir.glob("*.md"))
        if context_file.exists():
            args.files.append(context_file)

    if not args.files:
        print("No files to scan.", file=sys.stderr)
        return 0

    all_paths: list[str] = []
    for file in args.files:
        resolved_file = Path(file).resolve()
        if resolved_file.is_file():
            all_paths.extend(extract_backtick_paths(resolved_file))
        else:
            print(f"Warning: {file} is not a file, skipping.", file=sys.stderr)

    orphans = validate_paths(all_paths, base_dir)

    if orphans:
        print("Orphaned citations found:")
        for path in sorted(set(orphans)):
            print(f"  - {path}")
        return 1

    print("All citations valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
