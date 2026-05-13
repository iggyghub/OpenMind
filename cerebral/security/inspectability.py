"""
Plugin inspectability — ADR-0005, Issue #46.

A plugin is *inspectable* iff:
  1. It is text Python at one of:
       - ``plugins/<name>.py``
       - ``plugins/<name>/server.py``
  2. Its source passes the static-pattern scan in ``FORBIDDEN_PATTERNS`` below.

The escape hatch is ``plugins/_trusted/<name>/server.py``: those plugins skip
the scan but still pass through the capability gate at call time. The tray
renders a permanent red "trusted, unverified" badge for them.

``FORBIDDEN_PATTERNS`` is the canonical list. The builder (which generates
plugin source on-the-fly) and the orchestrator (which loads existing source
from disk) both import from here — no duplication.

This is a deliberately cheap, regex-only check. AST-level call-site →
capability completeness is a separate gate that lands in #47.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Inspectability marks — string-form for direct use in IPC payloads.
# ---------------------------------------------------------------------------

INSPECTED = "inspected"   # Default: text-Python, scan passed.
TRUSTED = "trusted"       # Under plugins/_trusted/, scan skipped.


# ---------------------------------------------------------------------------
# Refusal reason codes — match orchestrator REASON_* style (Issue #44).
# ---------------------------------------------------------------------------

REASON_FORBIDDEN_PATTERN = "forbidden_pattern"
REASON_NOT_INSPECTABLE_PATH = "not_inspectable_path"
REASON_NON_TEXT = "non_text"


# ---------------------------------------------------------------------------
# Canonical static-pattern scan list (ADR-0005, Issue #46).
#
# Strict superset of builder.py's pre-#46 list:
#   - The original 8 entries (os.system, subprocess shell-out, os.popen,
#     from-os-import-system, __import__('os'), exec, eval, raw write)
#   - +4 extensions: subprocess via from-import / __import__, compile with
#     'exec' mode, pickle.loads, marshal.loads.
# All 32 shipped plugins are clean against the full list (verified by
# grep + cerebral/tests/test_plugin_inspectability.py).
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bos\.system\s*\(", "os.system"),
    (r"\bsubprocess\.(?:Popen|run|call|check_output|check_call)\s*\(", "subprocess shell-out"),
    (r"\bos\.popen\s*\(", "os.popen"),
    (r"^\s*from\s+os\s+import\s+system", "from os import system"),
    (r"\b__import__\s*\(\s*['\"]os['\"]\s*\)", "__import__('os')"),
    (r"^\s*from\s+subprocess\s+import\s+", "from subprocess import"),
    (r"\b__import__\s*\(\s*['\"]subprocess['\"]\s*\)", "__import__('subprocess')"),
    (r"(?<!\.)\bexec\s*\(", "exec()"),
    (r"(?<!\.)\beval\s*\(", "eval()"),
    (r"\bcompile\s*\([^)]*['\"]exec['\"]", "compile(..., 'exec')"),
    (r"\bpickle\.loads\s*\(", "pickle.loads"),
    (r"\bmarshal\.loads\s*\(", "marshal.loads"),
    (r"\bopen\s*\([^)]*['\"]w['\"]", "raw file write"),
)


@dataclass(frozen=True)
class InspectabilityIssue:
    """A reason a plugin file failed the inspectability check.

    ``reason`` is one of the module-level ``REASON_*`` codes — stable for
    tray rendering. ``detail`` is the human-readable explanation surfaced
    next to the plugin name in the refusal list.
    """
    reason: str
    detail: str


def scan_source(source: str) -> InspectabilityIssue | None:
    """Return the first forbidden-pattern match, or None when source is clean.

    Only runs the regex pattern scan — structural checks (PLUGIN_NAME,
    REQUIRED_CAPABILITIES, create()) belong to whichever caller cares about
    them (the builder, the orchestrator's REQUIRED_CAPABILITIES validator).
    """
    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, source, re.MULTILINE):
            return InspectabilityIssue(
                reason=REASON_FORBIDDEN_PATTERN,
                detail=f"forbidden / unsafe pattern: {label}",
            )
    return None


def classify_path(
    path: Path, plugins_dir: Path,
) -> tuple[str, InspectabilityIssue | None]:
    """Decide whether ``path`` is an inspectable, trusted, or non-conforming entry.

    Returns ``(mark, issue)``:
      - ``(INSPECTED, None)`` for ``plugins/<name>.py`` or ``plugins/<name>/server.py``
      - ``(TRUSTED, None)`` for ``plugins/_trusted/<name>/server.py``
      - ``("", InspectabilityIssue)`` for anything else under ``plugins/``

    ``path`` must be a file path (not a directory). The caller is responsible
    for choosing what to feed in — typically a ``*.py`` or ``server.py``
    found during discovery, or a sentinel for a subdir missing its server.py.
    """
    try:
        rel = path.resolve().relative_to(plugins_dir.resolve())
    except (ValueError, OSError):
        return "", InspectabilityIssue(
            reason=REASON_NOT_INSPECTABLE_PATH,
            detail=f"path {path} is not under plugins_dir {plugins_dir}",
        )

    parts = rel.parts

    # plugins/_trusted/<name>/server.py
    if len(parts) == 3 and parts[0] == "_trusted" and parts[2] == "server.py":
        return TRUSTED, None

    # plugins/<name>.py — single .py file directly in plugins/
    if len(parts) == 1 and parts[0].endswith(".py") and not parts[0].startswith("_"):
        return INSPECTED, None

    # plugins/<name>/server.py — subdir form, name doesn't start with _ or .
    if (
        len(parts) == 2
        and parts[1] == "server.py"
        and not parts[0].startswith("_")
        and not parts[0].startswith(".")
    ):
        return INSPECTED, None

    return "", InspectabilityIssue(
        reason=REASON_NOT_INSPECTABLE_PATH,
        detail=(
            f"plugin entry {rel.as_posix()!r} does not match a conforming layout "
            "(expected plugins/<name>.py, plugins/<name>/server.py, or "
            "plugins/_trusted/<name>/server.py)"
        ),
    )


__all__ = [
    "FORBIDDEN_PATTERNS",
    "INSPECTED",
    "TRUSTED",
    "InspectabilityIssue",
    "REASON_FORBIDDEN_PATTERN",
    "REASON_NON_TEXT",
    "REASON_NOT_INSPECTABLE_PATH",
    "classify_path",
    "scan_source",
]
