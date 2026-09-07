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


def extract_backtick_paths(file: Path) -> list[str]:
    """Extract all backtick-quoted paths from a markdown file."""
    text = file.read_text(encoding="utf-8")
    # Match text inside backticks that looks like a file path
    pattern = r"`([^`]+)`"
    matches = re.findall(pattern, text)
    # Filter to only those that look like file paths (contain a slash or end with .md)
    paths = [m for m in matches if "/" in m or m.endswith(".md")]
    return paths


def validate_paths(paths: list[Path], base_dir: Path) -> list[str]:
    """Check if each path exists relative to the base directory."""
    orphans = []
    for file_path in paths:
        resolved = (base_dir / file_path).resolve()
        if not resolved.exists():
            orphans.append(str(file_path))
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
        # Default scan locations
        adr_dir = base_dir / "docs" / "adr"
        context_file = base_dir / "CONTEXT.md"

        if adr_dir.is_dir():
            args.files = list(adr_dir.glob("*.md"))
        if context_file.exists():
            args.files.append(context_file)

    if not args.files:
        print("No files to scan.", file=sys.stderr)
        return 0

    all_paths = []
    for file in args.files:
        resolved_file = file.resolve()
        if resolved_file.is_file():
            all_paths.extend(extract_backtick_paths(resolved_file))
        else:
            print(f"Warning: {file} is not a file, skipping.", file=sys.stderr)

    orphans = validate_paths(all_paths, base_dir)

    if orphans:
        print("Orphaned citations found:")
        for path in orphans:
            print(f"  - {path}")
        return 1

    print("All citations valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
