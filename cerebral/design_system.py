"""Base design system compliance scan (2026-09-08).

The "package" a recurring background scan (see plugins/scheduler.py's
ensure_design_system_event) and self_dev both read from: a small registry of
baseline UI rules every OpenMind screen must follow, each with its own
detector. New rules get added here as entries are earned (ADR-0028 rule 2 --
promote on the third repeat, not speculatively) -- rule #1 is the tab-strip
convention (tray/lib/tab-strip.js): any horizontal row of tab buttons must
carry the `tab-strip` class + a `data-tab-selector` attribute so it
self-wires into scrollable + Shift-drag-reorderable behavior.

Pure and side-effect-free: scan() only reads files and returns findings. The
scheduler decides what to do with them (log-only, or file GitHub issues and
kick off a self_dev_campaign against BASE-DESIGN-SYSTEM.md) -- kept separate
so this module stays trivially testable and reusable from a chat-triggered
one-off check too.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    rule_id: str
    file: str
    line: int
    snippet: str
    description: str


# Matches a tag's `class="..."` attribute, capturing the class list, plus
# enough of the tag to also check for a sibling `data-tab-selector`.
_TAG_RE = re.compile(r"<[a-zA-Z][^>]*?class=\"([^\"]*)\"[^>]*>")

# Known exceptions the checker should not flag, with why: a plain
# `tab-strip` + `data-tab-selector` patch is wrong for these, not just
# unapplied. #ws-tabs rebuilds its .ws-tab children from scratch
# (innerHTML = '') on every open/close, each one also nesting its own
# close/detach buttons -- Shift-drag reorder would visually work then snap
# back on the next re-render, since nothing feeds a persisted order back
# into the openIds array that drives the rebuild. Needs bespoke
# order-persistence wiring in its own render function, not this scan's
# generic fix -- tracked by hand, not autofixed.
_EXEMPT_IDS = frozenset({"ws-tabs"})

_ID_RE = re.compile(r"\bid=\"([^\"]*)\"")


def check_tab_strip_compliance(tray_dir: Path) -> list[Violation]:
    """Flags any element whose class list looks like a tab bar (a class
    ending in "-tabs", this repo's own naming convention -- lib-tabs,
    trd-tabs, docs-subtabs, set-tabs, help-tabs) but is missing the
    `tab-strip` class tray/lib/tab-strip.js keys off. Heuristic, not a
    parser -- false negatives (a tab bar named some other way) are fine,
    this is advisory, not a build-breaking check."""
    violations: list[Violation] = []
    for html_file in sorted(tray_dir.rglob("*.html")):
        if "node_modules" in html_file.parts:
            continue
        lines = html_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for lineno, line in enumerate(lines, start=1):
            for match in _TAG_RE.finditer(line):
                classes = match.group(1).split()
                id_match = _ID_RE.search(match.group(0))
                if id_match and id_match.group(1) in _EXEMPT_IDS:
                    continue
                looks_like_tabs = any(c.endswith("-tabs") or c == "tabs" for c in classes)
                if looks_like_tabs and "tab-strip" not in classes:
                    violations.append(Violation(
                        rule_id="tab-strip",
                        file=str(html_file),
                        line=lineno,
                        snippet=line.strip()[:200],
                        description=(
                            f"Tab bar missing the `tab-strip` class + `data-tab-selector` "
                            f"attribute (tray/lib/tab-strip.js) -- classes found: {classes}"
                        ),
                    ))
    return violations


# rule_id -> checker. Each checker takes the tray/ dir, returns Violations.
RULES = {
    "tab-strip": check_tab_strip_compliance,
}


def scan(tray_dir: Path) -> list[Violation]:
    violations: list[Violation] = []
    for check_fn in RULES.values():
        violations.extend(check_fn(tray_dir))
    return violations


# ── Driver-file bookkeeping ─────────────────────────────────────────────────
# Hand-rolled, not imported from plugins/self_dev.py's own driver parser:
# cerebral/ may not `from plugins.<x> import ...` (seam rule #153/#385, see
# BOOKS.md's SAFETY section) -- self_dev_campaign only needs to read
# compliant text back off disk, so writing it here with the same documented
# format (campaign-scaffold SKILL.md) is enough; no cross-layer import needed.

_STATUS_RE = re.compile(r'^(?:#+\s*)?Status:\s*(\S+)', re.IGNORECASE | re.MULTILINE)
_QUEUE_RULE_RE = re.compile(r'^- \[[ xX]\] S\d+ -- #\d+ -- rule:(\S+)', re.MULTILINE)


def driver_status(text: str) -> str:
    m = _STATUS_RE.search(text)
    return m.group(1).lower() if m else "ready"


def queued_rule_ids(text: str) -> "set[str]":
    """Rule ids already represented in the Queue (any state) -- a rule gets
    filed at most once; a regression after its entry is ticked needs a human
    to reopen it, same as any other campaign slice."""
    return set(_QUEUE_RULE_RE.findall(text))


def sync_driver_queue(text: str, violations: "list[Violation]", create_issue_fn) -> "tuple[str, list[dict]]":
    """Files one GitHub issue per rule_id with new violations (grouping every
    current instance of that rule into one ticket, not one per finding), and
    appends it to the Queue. Sets Active to the first unticked entry if none
    is currently set. Never touches a driver whose Status is 'blocked' -- that
    means a human needs to look at it first, same as any other campaign.

    `create_issue_fn(rule_id, violations_for_rule) -> int | None` -- the
    caller's own GitHub-issue-creation seam; returns the new issue number, or
    None on failure (that rule is simply skipped this tick, retried next).

    Returns (new_text, [{'rule_id', 'issue'} for each newly filed rule]).
    """
    if driver_status(text) == "blocked":
        return text, []

    already_queued = queued_rule_ids(text)
    by_rule: dict[str, list[Violation]] = {}
    for v in violations:
        by_rule.setdefault(v.rule_id, []).append(v)

    filed: list[dict] = []
    existing_slice_count = len(re.findall(r'^- \[[ xX]\] S\d+', text, re.MULTILINE))
    queue_lines: list[str] = []
    for rule_id, rule_violations in by_rule.items():
        if rule_id in already_queued:
            continue
        issue_no = create_issue_fn(rule_id, rule_violations)
        if issue_no is None:
            continue
        existing_slice_count += 1
        label = f"S{existing_slice_count}"
        queue_lines.append(f"- [ ] {label} -- #{issue_no} -- rule:{rule_id}\n")
        filed.append({"rule_id": rule_id, "issue": issue_no, "label": label})

    if not filed:
        return text, []

    if "## Queue" in text:
        text = text.replace("## Queue\n", "## Queue\n" + "".join(queue_lines), 1)
    else:
        text = text.rstrip("\n") + "\n\n## Queue\n" + "".join(queue_lines)

    if re.search(r'Active:\**\s*\S+\s+--\s+#\d+', text, re.IGNORECASE) is None:
        first = filed[0]
        active_line = f"- **Active:** {first['label']} -- #{first['issue']}\n"
        if "## Next slice" in text:
            text = re.sub(r'(## Next slice[^\n]*\n)', r'\1\n' + active_line, text, count=1)
        else:
            text = text.rstrip("\n") + "\n\n## Next slice -- start here\n\n" + active_line

    if driver_status(text) == "done":
        text = re.sub(r'^((?:#+\s*)?Status:)\s*\S+', r'\1 ready', text, count=1, flags=re.IGNORECASE | re.MULTILINE)

    return text, filed


def create_issue(repo_root: Path, title: str, body: str) -> "int | None":
    """Files a GitHub issue via `gh issue create`, run with cwd=repo_root so
    `gh` auto-detects the repo from its origin remote -- same convention
    self_dev_io.py's own pr_fn uses for `gh pr create`, no explicit --repo
    needed. Tagged `ready-for-agent` (campaign-scaffold's own convention) so
    it shows up filtered the same way a hand-triaged issue would. Returns the
    new issue number, or None on failure (logged by the caller, retried next
    scan tick -- not raised, since one rule's issue-filing failure shouldn't
    block the others)."""
    result = subprocess.run(
        ["gh", "issue", "create", "--title", title, "--body", body,
         "--label", "ready-for-agent"],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    if result.returncode != 0:
        return None
    # gh prints the new issue's URL (e.g. https://github.com/o/r/issues/42) on stdout.
    match = re.search(r"/issues/(\d+)\s*$", result.stdout.strip())
    return int(match.group(1)) if match else None
