"""Tests for scripts/check_doc_drift.py."""
import subprocess
import sys
import tempfile
from pathlib import Path


def test_catches_broken_citation():
    """Verify that the script detects a missing file referenced in docs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tdp = Path(tmpdir)

        # Create a docs/adr directory and a markdown file with a broken citation
        adr_dir = tdp / "docs" / "adr"
        adr_dir.mkdir(parents=True)

        broken_md = adr_dir / "test.md"
        broken_md.write_text("# Test\n\nSee `docs/missing_file.md`\n", encoding="utf-8")

        # Also create a CONTEXT.md with a broken citation to test that path too
        context_file = tdp / "CONTEXT.md"
        context_file.write_text("# Context\n\nRef: `lib/nonexistent.py`\n", encoding="utf-8")

        # Run the script against this temp directory
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "check_doc_drift.py"
        result = subprocess.run(
            [sys.executable, str(script_path), "--base", str(tdp)],
            capture_output=True,
            text=True,
        )

        # Check that the script exited non-zero
        assert result.returncode != 0, (
            f"Expected non-zero exit code for broken citations, got {result.returncode}. "
            f"Stdout: {result.stdout}"
        )

        # Check that the orphans are reported
        assert "docs/missing_file.md" in result.stdout, (
            f"Expected 'docs/missing_file.md' in output. Got: {result.stdout}"
        )
        assert "lib/nonexistent.py" in result.stdout, (
            f"Expected 'lib/nonexistent.py' in output. Got: {result.stdout}"
        )
        assert "Orphaned citations found:" in result.stdout, (
            f"Expected 'Orphaned citations found:' in output. Got: {result.stdout}"
        )


def test_all_valid_citations_pass():
    """Verify that the script exits 0 when all citations are valid."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tdp = Path(tmpdir)

        # Create a valid doc referencing an existing file
        adr_dir = tdp / "docs" / "adr"
        adr_dir.mkdir(parents=True)

        existing_file = tdp / "docs" / "adr" / "existing.md"
        existing_file.write_text("# Existing\n\n", encoding="utf-8")

        doc_md = adr_dir / "test.md"
        doc_md.write_text(
            "# Test\n\nSee `docs/adr/existing.md`\n", encoding="utf-8"
        )

        script_path = Path(__file__).resolve().parent.parent / "scripts" / "check_doc_drift.py"
        result = subprocess.run(
            [sys.executable, str(script_path), "--base", str(tdp)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            f"Expected zero exit code for valid citations, got {result.returncode}. "
            f"Stdout: {result.stdout}"
        )
        assert "All citations valid." in result.stdout, (
            f"Expected 'All citations valid.' in output. Got: {result.stdout}"
        )
