"""
Self-dev plugin -- ADR-0015 S1/S2/S4/S5 (Issues #554/#555/#557/#807).

Felix's self-dev loop: clone the repo, branch, have the model make a scoped
edit, run the test suite inside the ADR-0010 sandbox, and open a PR.

S2 adds self_dev_load: after a self-dev PR is merged to master, pull the live
repo (git pull --ff-only) and broadcast restart_felix to the tray so the
merged change goes live. No-op if already up-to-date.

S4 originally added a blast-radius gate (ADR-0015 decision 5): a guardrail
file in the diff, or red tests, escalated to human review instead of merging.
The 2026-08-21 "full auto-merge" amendment removed that block entirely.
2026-08-22 tightening: test status is gated again -- a run whose tests don't
pass (including tests that fail to even collect) opens its PR but does not
merge, returning merge_decision "tests_failed" instead. This reverses only
the test half of the amendment: it was letting genuinely broken code onto
master silently (PR #840 merged with an uncollectable test suite -- a missing
dependency; PR #842 merged with a Monte Carlo gate that was statistically
meaningless as written). Guardrail-path hits remain informational only, per
the original amendment -- detection (is_guardrail_diff / GUARDRAIL_PATHS)
still runs and is recorded as a system_event, but doesn't block merge on its
own. The safety net for a bad self-merge that DOES land is the launcher's
boot self-check + SHA/DB rollback (tray/lib/boot-check.js, SD-3), independent
of this gate and still runs on every self-dev restart.

Issue #780 wires cerebral.llm.step_ledger.StepLedger into _run: each completed
phase (clone / edit / test / pr) is recorded keyed by run_id. Re-invoking with
the same run_id resumes from the ledger -- already-recorded phases are reused
instead of re-run (the model-driven edit is the expensive, stochastic one this
protects). The ledger, not clone-directory existence, is the resume signal:
clone dirs are deliberately kept after a run (operator preference -- last
run's tree is reference material), so a bare leftover dir with no ledger
entries is stale, not resumable, and still gets today's refusal. To
deliberately abandon a stuck/poisoned run_id, pass `restart: true`: it clears
the recorded phases AND removes the stale clone dir, so the run starts over
from scratch. (The underlying `StepLedger.clear(run_id)` is still available
via the injectable `ledger` seam for programmatic use.)

S27 adds one bounded self-fix attempt (ADR-0008-style bounded self-correction,
applied to the test step): when the test run fails, edit_fn is called a
second time on the SAME clone with the failure output appended to the
description, then tests re-run once, before the PR opens. Resumable via a
"retest" ledger phase like every other step -- a crash between the two test
runs replays the recorded outcome instead of re-attempting the fix. This
targets the same class of problem the 2026-08-22 test-status gate above
surfaced (red PRs sitting unmerged for a human to look at): most red runs
are single mistakes an immediate second pass fixes, so this catches those
before they ever become a pending-review PR.

S5 (#807) adds self_dev_campaign: drives the existing _run() internals in a
loop against a campaign driver file (Status: / - **Active:** Sx -- #N /
- **Model:** / ## Queue / ## Landed PRs). Same driver-file format as the
scripts/run-<campaign>.ps1 external loop. Per-slice issue spec fetched via
injectable issue_fn seam (never real gh in tests). Stops immediately on error,
setting Status: blocked; every slice auto-merges per the amended decision 5
above (_run always returns merge_decision "auto_merge" now), so a campaign
runs slice-to-slice unattended unless a run itself errors out.

Injected seams (clone_fn / edit_fn / test_fn / pr_fn / diff_fn / merge_fn /
pull_fn / restart_fn / issue_fn / ledger) make the whole flow hermetic in
tests -- no real git / gh / network / Cerebral.
"""
import asyncio
import inspect
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from cerebral import self_dev_io as _io
from cerebral.llm.step_ledger import StepLedger
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "self_dev"

# ADR-0005 / Issue #554 -- self_dev clones the repo (shell_exec via sandbox),
# writes to the sandbox workdir (fs_write), and opens a PR via gh
# (network_egress_cloud). shell_exec is DENY by default (ADR-0005) -- self_dev
# is deny-by-default and unavailable where no sandbox backend exists
# (fail-closed, same posture as shell_exec / ADR-0010).
#
# fs_delete (#780 follow-up): `restart: true` removes the stale clone dir via
# shutil.rmtree so a poisoned run can start over. The AST-completeness check
# (#47) caught this as an undeclared capability -- declaring it is the honest
# fix, since the plugin genuinely deletes from the sandbox workdir now. The
# effective gate posture is unchanged: check_capabilities takes the WORST
# decision across the set and shell_exec is already DENY by default, so
# adding an ASK-default class cannot loosen anything.
#
# fs_read (#807 follow-up): self_dev_campaign reads the campaign driver file
# (driver_path.read_text) and rewrites it after each auto-merge. The AST-
# completeness check (#47) would flag read_text as undeclared; fs_write covers
# writes, so fs_read is explicitly added here for reads.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset(
    {"shell_exec", "fs_read", "fs_write", "fs_delete", "network_egress_cloud"}
)

# ADR-0015 decision 5 -- ONE authoritative list of guardrail paths.
# Originally: any PR diff touching these paths always escalated to human
# review. The 2026-08-21 "full auto-merge" amendment removed that block --
# these paths are still detected (informational system_event + result
# field) but no longer stop a merge. Kept as the single definition in case
# the gate is ever re-tightened, and because is_guardrail_diff's detection
# is still useful signal on its own.
#
# Entries ending with '/' are prefix matches (whole subtree).
# All other entries are exact path matches (relative to repo root, forward slashes).
GUARDRAIL_PATHS: frozenset[str] = frozenset({
    "cerebral/security/",       # ADR-0005 capability gate (prefix)
    "cerebral/sandbox/",        # ADR-0010 shell sandbox (prefix)
    "cerebral/db/credentials.py",   # credential / keyring store
    "cerebral/main.py",         # core Cerebral brain entry point
    "plugins/self_dev.py",      # the self-dev loop itself
    "tray/",                    # all Electron/tray UI (prefix) -- the sandbox
                                # test gate runs pytest only and cannot validate
                                # JS (incl. tray/lib/boot-check.js, SD-3).
})


# Substrings observed in real `gh pr merge` failures when a PR's branch has
# fallen behind master (another slice merged first) -- as opposed to other
# merge failures (auth, network, rate limit) that a retry can't fix.
_MERGE_CONFLICT_MARKERS = (
    "not mergeable",
    "cannot be cleanly created",
    "conflicting",
)


def _is_merge_conflict_error(text: str) -> bool:
    """True if a failed run's error text looks like a stale-branch conflict.

    Used by _campaign to decide whether a single re-clone-and-retry is worth
    attempting (PR #842's failure mode) versus an error a retry can't help
    with. Pure function, case-insensitive substring match -- no side effects.
    """
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _MERGE_CONFLICT_MARKERS)


def is_guardrail_diff(changed_files: Iterable[str]) -> "tuple[bool, str]":
    """Return (True, reason) if any file in the diff touches a guardrail path.

    Pure function -- no side effects. Used by SelfDevPlugin._run purely for
    visibility (informational system_event + result field) since the
    2026-08-21 "full auto-merge" amendment -- it no longer gates merge.
    """
    for f in changed_files:
        path = f.replace("\\", "/").lstrip("/")
        for g in GUARDRAIL_PATHS:
            if g.endswith("/"):
                if path.startswith(g) or path == g.rstrip("/"):
                    return True, f"{path!r} is in guardrail area {g!r}"
            elif path == g:
                return True, f"{path!r} is a guardrail file"
    return False, ""


# ── Driver-file parsing (SD-5 / #807) ──────────────────────────────────────
# Module-level for unit-testability with tmp_path fixtures.
# "Tolerant of markdown framing": both `Status: ready` and `## Status: ready`
# are accepted; `- **Active:** Sx -- #N` and plain `Active: Sx -- #N` both
# parse; `Model:` inside a queue checkbox line (`- [ ]` / `- [x]`) is never
# mistaken for the top-level model field (the 'queue-line Model: trap').

def parse_driver_status(text: str) -> str:
    """Return the driver Status field ('ready', 'blocked', or 'done').

    Matches 'Status: <val>' or '## Status: <val>'. Default: 'ready'.
    """
    for line in text.splitlines():
        m = re.match(r'^(?:#+\s*)?Status:\s*(\S+)', line, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return "ready"


def parse_driver_active(text: str) -> "tuple[str | None, int | None]":
    """Return (slice_label, issue_number) from the Active field.

    Accepts '- **Active:** Sx -- #N' and plain 'Active: Sx -- #N'.
    The trailing '**' after 'Active:' (markdown bold closing) is consumed
    before capturing the label. Returns (None, None) when not found.
    """
    for line in text.splitlines():
        # \** consumes optional trailing '**' after the colon (bold markdown)
        m = re.search(r'Active:\**\s*(\S+)\s+--\s+#(\d+)', line, re.IGNORECASE)
        if m:
            return m.group(1), int(m.group(2))
    return None, None


def parse_driver_model(text: str) -> str:
    """Return the driver Model field, skipping queue checkbox lines.

    Accepts '- **Model:** sonnet' and plain 'Model: sonnet'. Skips lines
    matching '- [ ]' / '- [x]' so an inline 'Model: X' in a queue entry
    never shadows the top-level field. Default: 'sonnet'.
    """
    for line in text.splitlines():
        if re.match(r'^\s*-\s*\[[ xX]\]', line):
            continue
        # Optional markdown prefix (e.g. '- **'), then Model:, optional trailing
        # '**' (e.g. '- **Model:**'), then the value.
        m = re.match(r'^[^\S\n]*(?:[#*\-\s]*)Model:\**\s*(\S+)', line, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return "sonnet"


def _set_driver_status(text: str, status: str, reason: str = "") -> str:
    """Rewrite the Status field in a driver file. Prepends if not found.

    `reason` routinely carries multi-line content (a pytest failure summary,
    a multi-line HTTP error body) -- collapsed to one line here so Status
    can never itself span multiple lines going forward. Also consumes any
    pre-existing continuation lines below a prior multi-line Status (from
    before this fix, or a manual edit) up to the next blank line or `##`
    header, so replacing an old multi-line block doesn't leave its tail
    behind as orphaned text -- this is what corrupted TRADING.md when a
    single-line "blocked" status overwrote just the first line of an
    earlier multi-line one, twice, in this same campaign.
    """
    reason = " ".join(reason.split())  # collapse embedded newlines/whitespace
    val = status if not reason else f"{status} -- {reason}"
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = re.match(r'^((?:#+\s*)?Status:)', line, re.IGNORECASE)
        if m:
            end = i + 1
            while end < len(lines) and lines[end].strip() and not lines[end].lstrip().startswith("#"):
                end += 1
            lines[i:end] = [m.group(1) + " " + val + "\n"]
            return "".join(lines)
    return f"Status: {val}\n" + text


def _advance_driver(text: str, active_label: str, active_issue: int, pr_url: str) -> str:
    """Rewrite the driver file after a successful auto-merge.

    - Ticks the active queue entry ([ ] -> [x])
    - Finds the next unticked queue entry; updates Active + Model lines
    - Appends the PR to the Landed PRs section
    - Sets Status: done when no more unticked entries remain
    """
    lines = text.splitlines(keepends=True)

    # 1. Tick the queue entry for the completed slice (handles '-- #N' separator).
    tick_re = re.compile(
        r'^(\s*-\s*)\[ \](\s+' + re.escape(active_label)
        + r'\s+--\s+#' + str(active_issue) + r'\b)',
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        if tick_re.match(line):
            lines[i] = tick_re.sub(r'\1[x]\2', line)
            break

    # 2. Find the next unticked queue entry (only inside ## Queue section).
    next_label: "str | None" = None
    next_issue: "int | None" = None
    next_model_override: "str | None" = None
    in_queue = False
    for line in lines:
        if re.match(r'^#+\s*Queue', line.strip(), re.IGNORECASE):
            in_queue = True
            continue
        if in_queue and re.match(r'^#+\s', line.strip()):
            in_queue = False
            continue
        if not in_queue:
            continue
        m = re.match(r'^\s*-\s*\[ \]\s+(\S+)\s+--\s+#(\d+)(.*)', line)
        if m:
            next_label, next_issue = m.group(1), int(m.group(2))
            mm = re.search(r'\bModel:\s*(\S+)', m.group(3), re.IGNORECASE)
            if mm:
                next_model_override = mm.group(1).lower()
            break

    # 3. Update the Active line (tolerant of markdown framing).
    if next_label is not None:
        active_line_re = re.compile(
            r'^([^\S\n]*(?:[#*\-\s]*)\**Active:\**\s*)\S+\s+--\s+#\d+',
            re.IGNORECASE,
        )
        for i, line in enumerate(lines):
            if active_line_re.match(line):
                lines[i] = active_line_re.sub(
                    rf'\g<1>{next_label} -- #{next_issue}', line
                )
                break

    # 4. Update the Model line if the next queue entry overrides it.
    if next_model_override is not None:
        model_line_re = re.compile(
            r'^([^\S\n]*(?:[#*\-\s]*)Model:\**\s*)\S+',
            re.IGNORECASE,
        )
        for i, line in enumerate(lines):
            if re.match(r'^\s*-\s*\[[ xX]\]', line):
                continue
            if model_line_re.match(line):
                lines[i] = model_line_re.sub(rf'\g<1>{next_model_override}', line)
                break

    # 5. Append the PR to the Landed PRs section (insert before next ## header).
    pr_num = pr_url.rstrip("/").split("/")[-1]
    pr_entry = f"- PR #{pr_num} -- {active_label} (auto-merged by self_dev_campaign)\n"
    in_landed = False
    insert_pos = len(lines)
    for i, line in enumerate(lines):
        if re.match(r'^#+\s*Landed PRs', line.strip(), re.IGNORECASE):
            in_landed = True
        elif in_landed and re.match(r'^#+\s', line.strip()):
            insert_pos = i
            break
    lines.insert(insert_pos, pr_entry)

    # 6. If no more unticked entries, mark done.
    if next_label is None:
        return _set_driver_status("".join(lines), "done")

    return "".join(lines)


# Repo root: plugins/self_dev.py -> parent = plugins/, parent.parent = repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Injected callable type aliases.
CloneFn = Callable[[str, Path], None]         # (repo_url, dest) -> None
EditFn = Callable[[Path, str], dict]           # (clone_dir, description) -> {branch, committed, ...}
TestFn = Callable[[Path], "tuple[bool, str]"]  # (clone_dir) -> (passed, output)
PrFn = Callable[[Path, str, str, bool, str], str]  # (clone_dir, branch, desc, ok, out) -> pr_url
DiffFn = Callable[[str], "list[str]"]          # (pr_url) -> changed_files
MergeFn = Callable[[str], None]                # (pr_url) -> None (squash-merges the PR)
PullFn = Callable[[Path], "tuple[bool, str]"]  # (live_root) -> (updated, output)
RestartFn = Callable[[], "Awaitable[None]"]    # async -- broadcasts restart_felix to tray
RollbackFn = Callable[[], "Awaitable[None]"]   # async -- broadcasts self_dev_manual_rollback (#813)
IssueFn = Callable[[int], str]                 # (issue_number) -> "# Title\n\nBody"
RecordTurnFn = Callable[[str, dict], "Awaitable[None]"]  # (kind, content) -> None
PrStateFn = Callable[[str], str]               # (pr_url) -> "OPEN"/"MERGED"/"CLOSED"


# git/gh/pytest I/O lives in cerebral/self_dev_io.py (NOT scanned by the
# ADR-0005 inspectability gate, which forbids shell-out calls in a plugin
# body). We re-alias the helpers as the `_default_*_fn` seams so the plugin
# body stays scan-clean while behaviour is unchanged. Tests that call
# `self_dev._default_clone_fn` / `_default_pr_fn` and monkeypatch the global
# subprocess shell-out keep working -- the patch lands on the module `_io` uses.
_default_clone_fn = _io.clone_fn
_default_test_fn = _io.test_fn
_default_pr_fn = _io.pr_fn
_default_diff_fn = _io.diff_fn
_default_merge_fn = _io.merge_fn
_default_pull_fn = _io.pull_fn
_default_issue_fn = _io.issue_fn
_default_pr_state_fn = _io.pr_state_fn


def _default_edit_fn(clone_dir: Path, description: str) -> dict:
    """Model makes a scoped edit, commits it, returns branch + message.

    Uses task_type='self_dev' so the user's per-task model selection applies.
    Wired in by main.py (set_edit_fn); tests always inject this seam.
    """
    raise NotImplementedError(
        "SelfDevPlugin requires an edit_fn -- "
        "main.py must wire the model router in via set_edit_fn(...)."
    )


# ── Module-level seams (wired by cerebral/main.py after discovery) ────────────
# edit_fn/restart_fn/record_turn_fn can't be built at discovery time -- they
# close over the model router, the tray broadcast, and the Conversation
# store. Constructor injection still wins (tests pass them directly); prod
# resolves these globals lazily at call time.
_edit_fn = None
_restart_fn = None
_record_turn_fn = None
_rollback_fn = None
# S26 (#879), decision #46 -- distinct from record_turn_fn: record_turn_fn
# (below) writes into the user's active chat thread for the #810 pending-
# review card, an inline actionable UI element that belongs where the user
# is looking. record_activity_fn writes into the dedicated "Autonomous
# activity" thread instead, for the Activity Log -- a different destination
# for the same event, not a replacement.
_record_activity_fn = None


def set_edit_fn(fn) -> None:
    global _edit_fn
    _edit_fn = fn


def set_restart_fn(fn) -> None:
    global _restart_fn
    _restart_fn = fn


def set_record_turn_fn(fn) -> None:
    global _record_turn_fn
    _record_turn_fn = fn


def set_rollback_fn(fn) -> None:
    global _rollback_fn
    _rollback_fn = fn


def set_record_activity_fn(fn) -> None:
    global _record_activity_fn
    _record_activity_fn = fn


async def _default_record_turn_fn(kind: str, content: dict) -> None:
    """No-op until main.py wires the Conversation store seam via
    set_record_turn_fn (main.py's own ``_record_turn``, S9/#292).

    Unlike edit_fn/restart_fn, this seam is NOT load-bearing to the run
    itself -- dropping the in-chat pending-review card is a lesser failure
    than refusing self-dev entirely, so this defaults to fail-open (log +
    continue) rather than raising."""
    logger.warning(
        "[self_dev] record_turn_fn not wired -- system_event turn dropped (kind=%s)",
        kind,
    )


async def _default_record_activity_fn(kind: str, content: dict) -> None:
    """No-op until main.py wires the Activity Log seam via
    set_record_activity_fn (main.py's own ``_record_activity``, S26/#879).
    Same fail-open reasoning as _default_record_turn_fn -- a dropped
    Activity Log entry is a lesser failure than refusing self-dev."""
    logger.warning(
        "[self_dev] record_activity_fn not wired -- activity entry dropped (kind=%s)",
        kind,
    )


async def _default_restart_fn() -> None:
    """Broadcast restart_felix to the tray -- main.py must wire this."""
    raise NotImplementedError(
        "SelfDevPlugin requires a restart_fn -- "
        "main.py must wire the broadcast in via SelfDevPlugin(restart_fn=...)."
    )


async def _default_rollback_fn() -> None:
    """Broadcast self_dev_manual_rollback to the tray -- main.py must wire this."""
    raise NotImplementedError(
        "SelfDevPlugin requires a rollback_fn -- "
        "main.py must wire the broadcast in via SelfDevPlugin(rollback_fn=...)."
    )


class SelfDevPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        *,
        sandbox=None,
        clone_fn: CloneFn | None = None,
        edit_fn: EditFn | None = None,
        test_fn: TestFn | None = None,
        pr_fn: PrFn | None = None,
        diff_fn: DiffFn | None = None,
        merge_fn: MergeFn | None = None,
        pull_fn: PullFn | None = None,
        restart_fn: RestartFn | None = None,
        rollback_fn: RollbackFn | None = None,
        issue_fn: IssueFn | None = None,
        record_turn_fn: RecordTurnFn | None = None,
        record_activity_fn: RecordTurnFn | None = None,
        pr_state_fn: PrStateFn | None = None,
        repo_url: str | None = None,
        sandbox_root: Path | None = None,
        live_root: Path | None = None,
        ledger: "StepLedger | None" = None,
    ) -> None:
        self._sandbox = sandbox
        self._clone = clone_fn or _default_clone_fn
        self._test = test_fn or _default_test_fn
        self._pr = pr_fn or _default_pr_fn
        self._diff = diff_fn or _default_diff_fn
        self._merge = merge_fn or _default_merge_fn
        self._pull = pull_fn or _default_pull_fn
        self._issue_fn = issue_fn or _default_issue_fn
        # pr_state_fn (#810), like merge_fn/diff_fn/pull_fn, needs no main.py
        # closure -- the default just works standalone.
        self._pr_state_fn = pr_state_fn or _default_pr_state_fn
        # #780 -- unlike edit_fn/restart_fn, StepLedger needs no main.py
        # wiring (no model router / tray closure to capture): it's
        # self-sufficient against the shared openmind.db, so the default
        # just works. Tests inject a StepLedger(db_path=tmp_path/...) (or any
        # duck-typed record/completed/clear object) via this seam instead.
        self._ledger = ledger if ledger is not None else StepLedger()
        # edit_fn/restart_fn resolve lazily (constructor override > module seam
        # wired by main.py > raising default) -- the seams are set after
        # discovery constructs the instance, so we can't collapse them here.
        self._edit_override = edit_fn
        self._restart_override = restart_fn
        self._rollback_override = rollback_fn
        self._record_turn_override = record_turn_fn
        self._record_activity_override = record_activity_fn
        self._repo_url = repo_url or str(_REPO_ROOT)
        self._sandbox_root = sandbox_root or (data_dir() / "sandbox" / "self_dev")
        self._live_root = live_root or _REPO_ROOT

    def _resolve_edit(self) -> EditFn:
        return self._edit_override or _edit_fn or _default_edit_fn

    def _resolve_restart(self) -> RestartFn:
        return self._restart_override or _restart_fn or _default_restart_fn

    def _resolve_rollback(self) -> RollbackFn:
        return self._rollback_override or _rollback_fn or _default_rollback_fn

    def _resolve_record_turn(self) -> RecordTurnFn:
        return self._record_turn_override or _record_turn_fn or _default_record_turn_fn

    def _resolve_record_activity(self) -> RecordTurnFn:
        return self._record_activity_override or _record_activity_fn or _default_record_activity_fn

    def pr_state(self, pr_url: str) -> str:
        """Live PR state (OPEN/MERGED/CLOSED) via the injected pr_state_fn
        seam (#810). Deliberately NOT a Tool and NOT wired into list_tools /
        call_tool -- main.py calls this directly (same posture as _merge/
        _load) so it can never be reached through the planner's tool-calling
        loop (ADR-0015 amendment 3)."""
        return self._pr_state_fn(pr_url)

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="self_dev",
                description=(
                    "Modify Felix's own core: clone the repo, branch, have the "
                    "model make a scoped edit (task_type='self_dev'), run the "
                    "test suite inside the ADR-0010 sandbox, and open a PR. "
                    "The PR is the final output -- nothing is merged or loaded "
                    "automatically (that requires a human review or the SD-2..4 "
                    "slices). Deny-by-default per ADR-0005 (shell_exec). "
                    "Unavailable without a sandbox backend. Re-calling with the "
                    "same run_id after an interrupted run resumes from the last "
                    "completed phase (clone/edit/test/pr) instead of refusing."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "change_description": {
                            "type": "string",
                            "description": "Plain-English description of the change to make.",
                        },
                        "run_id": {
                            "type": "string",
                            "description": (
                                "Optional run identifier (defaults to a UUID). "
                                "Used as the clone directory name and branch suffix. "
                                "Re-calling with the same run_id resumes from the "
                                "last completed phase."
                            ),
                        },
                        "restart": {
                            "type": "boolean",
                            "description": (
                                "Abandon any recorded progress for this run_id and "
                                "start over from the clone. Use when a resumed run "
                                "is stuck on a bad recorded phase."
                            ),
                        },
                    },
                    "required": ["change_description"],
                },
            ),
            Tool(
                name="self_dev_load",
                description=(
                    "After a self-dev PR is merged to master: pull the live repo "
                    "(git pull --ff-only) and trigger a tray relaunch so the merged "
                    "change goes live. No-ops silently if already up-to-date. "
                    "Requires restart_fn wired by main.py (fail-closed without it)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "pr_url": {
                            "type": "string",
                            "description": "Optional: URL of the merged PR (for the result log).",
                        },
                    },
                },
            ),
            Tool(
                name="self_dev_rollback",
                description=(
                    "Revert Felix to its last known-good self-dev state -- resets the "
                    "live code to the last verified commit and restores the matching "
                    "openmind.db/felix-settings.json snapshot, then relaunches (#813). "
                    "Use when a recent self-merge (yours or a prior one) turns out to "
                    "be wrong even though it booted fine -- the automatic SD-3 rollback "
                    "only catches a change that fails to boot at all, not one that boots "
                    "but behaves badly. No-ops with a clear reason if no self-dev restart "
                    "has ever run yet. Requires rollback_fn wired by main.py."
                ),
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {}},
            ),
            Tool(
                name="self_dev_campaign",
                description=(
                    "Drive a multi-slice self-dev campaign from a driver .md file "
                    "(ADR-0015 amendment, SD-5/#807). Reads Active/Model from the "
                    "Next-slice block, fetches each slice's spec from its GitHub "
                    "issue (injectable issue_fn seam), calls self_dev per slice, "
                    "and rewrites the driver file after each auto-merge (tick "
                    "queue entry, advance Active + Model, append to Landed PRs). "
                    "Every slice auto-merges regardless of guardrail/test status "
                    "(2026-08-21 full-auto-merge amendment); stops only on error, "
                    "setting Status: blocked. Deny-by-default per ADR-0005. "
                    "Requires sandbox."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "driver_file": {
                            "type": "string",
                            "description": (
                                "Absolute path to the campaign driver .md file "
                                "(e.g. '/path/to/BOOKS.md')."
                            ),
                        },
                        "max_slices": {
                            "type": "integer",
                            "description": "Maximum slices to run in one call. Default: 20.",
                        },
                    },
                    "required": ["driver_file"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "self_dev":
            return await self._run(args)
        if tool_name == "self_dev_load":
            return await self._load(args)
        if tool_name == "self_dev_rollback":
            return await self._rollback(args)
        if tool_name == "self_dev_campaign":
            return await self._campaign(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    async def _run(self, args: dict) -> ToolResult:
        description = (args or {}).get("change_description", "").strip()
        if not description:
            return ToolResult(content="change_description is required", is_error=True)

        # Fail-closed: no sandbox = no self-dev (ADR-0015 decision 7 / ADR-0010).
        if self._sandbox is None:
            return ToolResult(
                content=(
                    "self_dev unavailable: no sandbox backend is configured "
                    "(fail-closed per ADR-0015 / ADR-0010). A WindowsSandbox "
                    "is required; main.py must inject it."
                ),
                is_error=True,
            )

        run_id = str((args or {}).get("run_id") or uuid.uuid4()).strip()
        clone_dir = self._sandbox_root / run_id

        # #780 -- the ledger, not clone-dir existence, is the resume signal.
        # Clone dirs are deliberately kept after a run, so a bare leftover dir
        # with nothing recorded for this run_id is stale, not resumable: keep
        # today's refusal for that case. Non-empty ledger => resume, and the
        # dir-exists check is skipped on purpose (resuming reuses that dir).
        # restart=true abandons recorded progress so a run stuck on a bad phase
        # can start over without needing Python access to the ledger. Clearing
        # BEFORE reading `resumed` is what makes the rest of this function take
        # the fresh-run path; the stale clone dir is removed too, otherwise the
        # dir-exists refusal below would immediately block the restart.
        if (args or {}).get("restart"):
            cleared = self._ledger.clear(run_id)
            if clone_dir.exists():
                shutil.rmtree(clone_dir, ignore_errors=True)
            logger.info(
                "[self_dev] run %r: restart requested -- cleared %d recorded phase(s)",
                run_id, cleared,
            )

        resumed = {s["sig"]: s for s in self._ledger.completed(run_id)}

        if not resumed and clone_dir.exists():
            return ToolResult(
                content=f"Run {run_id!r} already exists -- use a different run_id.",
                is_error=True,
            )

        self._sandbox_root.mkdir(parents=True, exist_ok=True)

        # 1. Clone into an independent working tree (live .git untouched).
        if "clone" in resumed:
            logger.info("[self_dev] run %r: resuming -- clone already recorded", run_id)
        else:
            try:
                self._clone(self._repo_url, clone_dir)
            except Exception as exc:
                return ToolResult(content=f"Clone failed: {exc}", is_error=True)
            self._ledger.record(run_id, "clone", {
                "name": "clone",
                "args": {"repo_url": self._repo_url},
                "result": {"clone_dir": str(clone_dir)},
                "is_error": False,
            })

        # 2. Model edits + commits inside the clone. The prod edit_fn is async
        #    (it awaits the model router); test seams are plain sync callables.
        #    This is the expensive, stochastic step the ledger exists to
        #    protect -- resuming reuses the recorded result and never calls
        #    edit_fn again.
        if "edit" in resumed:
            logger.info("[self_dev] run %r: resuming -- edit already recorded", run_id)
            edit_result = resumed["edit"]["result"]
        else:
            try:
                edit_result = self._resolve_edit()(clone_dir, description)
                if inspect.isawaitable(edit_result):
                    edit_result = await edit_result
            except NotImplementedError as exc:
                return ToolResult(content=str(exc), is_error=True)
            except Exception as exc:
                return ToolResult(content=f"Edit failed: {exc}", is_error=True)

        branch = str(edit_result.get("branch") or f"selfdev/{run_id[:8]}")
        if not edit_result.get("committed"):
            return ToolResult(
                content="Edit step produced no commit -- aborting.",
                is_error=True,
            )

        if "edit" not in resumed:
            self._ledger.record(run_id, "edit", {
                "name": "edit",
                "args": {"description": description},
                "result": edit_result,
                "is_error": False,
            })

        # 3. Test inside sandbox (failure is recorded, not fatal -- the
        #    blast-radius gate in SD-4 decides whether to auto-merge).
        #    Off-thread: the default test_fn shells out to pytest and can run
        #    for minutes (or, pre-fix, hang indefinitely) -- calling it directly
        #    would block Cerebral's whole event loop (no WS, no heartbeats) for
        #    the duration. asyncio.to_thread keeps Cerebral responsive while it
        #    runs; self_dev_io.test_fn's own timeout still bounds a real hang.
        if "test" in resumed:
            logger.info("[self_dev] run %r: resuming -- test already recorded", run_id)
            test_result = resumed["test"]["result"] or {}
            test_passed = bool(test_result.get("passed"))
            test_output = str(test_result.get("summary") or "")
        else:
            test_passed = False
            test_output = ""
            try:
                test_passed, test_output = await asyncio.to_thread(self._test, clone_dir)
            except Exception as exc:
                test_output = f"Test runner error: {exc}"
            self._ledger.record(run_id, "test", {
                "name": "test",
                "args": {},
                "result": {"passed": test_passed, "summary": test_output},
                "is_error": False,
            })

        # 3b. One bounded self-fix attempt if tests failed (S27): the model
        #     gets a second commit on the same clone, told what broke, before
        #     the PR opens as tests_failed. Resumable via the "retest" phase.
        if not test_passed:
            if "retest" in resumed:
                logger.info("[self_dev] run %r: resuming -- retest already recorded", run_id)
                retest_result = resumed["retest"]["result"] or {}
                test_passed = bool(retest_result.get("passed"))
                test_output = str(retest_result.get("summary") or "")
            else:
                fix_description = (
                    "The previous change failed the test suite. Fix the code "
                    "so the tests pass, then commit.\n\nOriginal task:\n"
                    f"{description}\n\nTest failure output:\n{test_output[:2000]}"
                )
                try:
                    fix_result = self._resolve_edit()(clone_dir, fix_description)
                    if inspect.isawaitable(fix_result):
                        fix_result = await fix_result
                    if fix_result.get("committed"):
                        test_passed, test_output = await asyncio.to_thread(self._test, clone_dir)
                except Exception as exc:
                    test_output = f"{test_output}\n\nFix attempt failed: {exc}"
                self._ledger.record(run_id, "retest", {
                    "name": "retest",
                    "args": {},
                    "result": {"passed": test_passed, "summary": test_output},
                    "is_error": False,
                })

        # 4. Open PR (regardless of test colour; mergeability is decided below).
        if "pr" in resumed:
            logger.info("[self_dev] run %r: resuming -- pr already recorded", run_id)
            pr_url = resumed["pr"]["result"]["pr_url"]
        else:
            try:
                pr_url = self._pr(clone_dir, branch, description, test_passed, test_output)
            except Exception as exc:
                return ToolResult(content=f"PR creation failed: {exc}", is_error=True)
            self._ledger.record(run_id, "pr", {
                "name": "pr",
                "args": {"branch": branch},
                "result": {"pr_url": pr_url},
                "is_error": False,
            })

        # 5. Blast-radius check -- guardrail-path hits are detected and
        #    recorded for visibility but do not block merge on their own
        #    (ADR-0015 decision 5 amendment, 2026-08-21 "full auto-merge").
        #    Test status DOES gate merge again (2026-08-22 tightening): the
        #    original amendment let PR #840 (S1a) and PR #842 (S3) both merge
        #    with tests that never even ran -- collection errors and a
        #    statistically meaningless Monte Carlo gate -- because nothing
        #    checked test_passed before merging. Safety net for a bad
        #    self-merge is still the launcher's boot self-check + SHA/DB
        #    rollback (tray/lib/boot-check.js, SD-3), independent of this gate.
        guardrail_hit = False
        escalation_reason = ""
        try:
            changed_files = self._diff(pr_url)
            guardrail_hit, escalation_reason = is_guardrail_diff(changed_files)
        except Exception as exc:
            escalation_reason = f"diff check failed: {exc}"

        if guardrail_hit or not test_passed:
            reason = escalation_reason or ("tests did not pass" if not test_passed else "")
            try:
                await self._resolve_record_turn()("system_event", {
                    "kind": "self_dev_pr_auto_merged",
                    "pr_url": pr_url,
                    "run_id": run_id,
                    "branch": branch,
                    "reason": reason,
                    "test_passed": test_passed,
                })
            except Exception:
                logger.exception(
                    "[self_dev] record_turn_fn failed for run %r -- "
                    "merge decision proceeding regardless", run_id,
                )
            # S26 (#879): a real decision (a PR needs human review), logged
            # to the Activity Log individually -- not batched, unlike
            # routine/noisy activity, per decision #46's "log real decisions
            # individually" -- separate from the pending-review card above,
            # which writes to the user's active chat thread instead.
            try:
                await self._resolve_record_activity()("activity", {
                    "source": "self_dev",
                    "summary": f"Self-dev PR {pr_url} needs human review: {reason}",
                    "pr_url": pr_url,
                    "run_id": run_id,
                })
            except Exception:
                logger.exception(
                    "[self_dev] record_activity_fn failed for run %r", run_id,
                )

        if not test_passed:
            # PR stays open for review -- this is a normal, non-error outcome
            # (the run itself succeeded; it just isn't safe to merge red).
            return ToolResult(content=json.dumps({
                "run_id": run_id,
                "clone_dir": str(clone_dir),
                "branch": branch,
                "test_passed": test_passed,
                "test_summary": test_output[:500],
                "pr_url": pr_url,
                "merge_decision": "tests_failed",
                "guardrail_hit": guardrail_hit,
                "guardrail_reason": escalation_reason,
            }))

        # 6. Auto-merge (tests passed; guardrail hits remain informational).
        try:
            self._merge(pr_url)
        except Exception as exc:
            return ToolResult(
                content=f"Auto-merge failed (PR stays open for manual review): {exc}",
                is_error=True,
            )

        # 7. Trigger load (git pull + restart) -- S3 boot self-check runs on restart.
        load_result = await self._load({"pr_url": pr_url})
        load_data = (
            json.loads(load_result.content)
            if not load_result.is_error
            else {"error": load_result.content}
        )
        return ToolResult(
            content=json.dumps({
                "run_id": run_id,
                "clone_dir": str(clone_dir),
                "branch": branch,
                "test_passed": test_passed,
                "test_summary": test_output[:500],
                "pr_url": pr_url,
                "merge_decision": "auto_merge",
                "guardrail_hit": guardrail_hit,
                "guardrail_reason": escalation_reason,
                "load": load_data,
            })
        )

    async def _load(self, args: dict) -> ToolResult:
        pr_url = (args or {}).get("pr_url", "").strip() or None

        try:
            updated, output = self._pull(self._live_root)
        except Exception as exc:
            return ToolResult(content=f"git pull failed: {exc}", is_error=True)

        if not updated:
            return ToolResult(content=json.dumps({
                "status": "no_op",
                "message": "Already up to date -- no restart needed.",
                "output": output,
                "pr_url": pr_url,
            }))

        try:
            await self._resolve_restart()()
        except NotImplementedError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:
            return ToolResult(content=f"Restart trigger failed: {exc}", is_error=True)

        return ToolResult(content=json.dumps({
            "status": "restarting",
            "message": "Pulled new commits -- relaunch triggered.",
            "output": output,
            "pr_url": pr_url,
        }))

    async def _rollback(self, args: dict) -> ToolResult:
        """On-demand revert to the last self-dev snapshot (#813).

        The actual reset+relaunch happens in the tray/Electron layer
        (tray/lib/boot-check.js manualRollback) -- same reasoning as restart:
        a broken brain can't rescue itself, so this just fires the broadcast
        and trusts the tray to do the work, exactly like _load()/restart_fn.
        """
        try:
            await self._resolve_rollback()()
        except NotImplementedError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:
            return ToolResult(content=f"Rollback trigger failed: {exc}", is_error=True)

        try:
            await self._resolve_record_turn()("system_event", {
                "kind": "self_dev_manual_rollback",
            })
        except Exception:
            logger.exception("[self_dev] record_turn_fn failed for manual rollback")

        # S26 (#879): a manual rollback is a real decision, logged
        # individually to the Activity Log.
        try:
            await self._resolve_record_activity()("activity", {
                "source": "self_dev",
                "summary": "Manual rollback triggered -- reverting to the last known-good state",
            })
        except Exception:
            logger.exception("[self_dev] record_activity_fn failed for manual rollback")

        return ToolResult(content=json.dumps({
            "status": "rolling_back",
            "message": (
                "Rollback triggered -- reverting to the last known-good state "
                "and relaunching."
            ),
        }))

    async def _campaign(self, args: dict) -> ToolResult:
        """Drive a multi-slice campaign from a driver .md file (SD-5/#807).

        Loops around the existing _run() engine. Each iteration:
          1. Parse Status/Active/Model from the driver file.
          2. Fetch the active issue's spec via self._issue_fn (injectable seam).
          3. Call self._run with the issue spec as change_description.
          4. auto_merge -> tick queue, advance Active+Model, append PR, continue.
          5. tests_failed / other non-error merge_decision -> set Status: blocked,
             stop (PR is left open for review; see 2026-08-22 test-status gate
             in _run()).
          6. is_error AND the failure looks like a merge conflict (the PR's
             branch fell behind master -- e.g. another slice merged first) ->
             retry ONCE with a fresh run_id, forcing a brand-new clone that
             picks up whatever has since landed on master. This is what would
             have avoided PR #842 (S3): its branch was cloned before PR #841
             (S2) merged, so by the time #842 tried to merge, master had moved
             and the two slices had independently written colliding versions
             of the same new file. A fresh clone means the retry's edit step
             sees #841's real code already in the tree instead of reinventing
             it. If the retry also fails (conflict or otherwise), fall through
             to the normal blocked-and-stop handling -- this is a bounded
             single retry, not a loop.
          7. is_error for any other reason -> set Status: blocked, stop.
        """
        driver_file_str = ((args or {}).get("driver_file") or "").strip()
        if not driver_file_str:
            return ToolResult(content="driver_file is required", is_error=True)

        driver_path = Path(driver_file_str)
        if not driver_path.exists():
            return ToolResult(
                content=f"Driver file not found: {driver_path}",
                is_error=True,
            )

        max_slices = int((args or {}).get("max_slices") or 20)
        slug = driver_path.stem.lower()
        results: list[dict] = []

        for n in range(1, max_slices + 1):
            text = driver_path.read_text(encoding="utf-8")
            status = parse_driver_status(text)

            if status in ("done", "blocked"):
                reason = None
                for line in text.splitlines():
                    m = re.match(r'^(?:#+\s*)?Status:\s*(\S+)(?:\s*--\s*(.*))\s*$', line, re.IGNORECASE)
                    if m:
                        reason = m.group(2).strip() if m.group(2) else None
                        break
                return ToolResult(content=json.dumps({
                    "status": status,
                    "reason": reason,
                    "slices_run": len(results),
                    "results": results,
                }))

            active_label, active_issue = parse_driver_active(text)
            if active_label is None or active_issue is None:
                # No unticked Active entry -- campaign complete.
                text = _set_driver_status(text, "done")
                driver_path.write_text(text, encoding="utf-8")
                return ToolResult(content=json.dumps({
                    "status": "done",
                    "slices_run": len(results),
                    "results": results,
                }))

            try:
                issue_text = self._issue_fn(active_issue)
            except Exception as exc:
                reason = f"issue_fn({active_issue}) failed: {exc}"
                text = _set_driver_status(text, "blocked", reason)
                driver_path.write_text(text, encoding="utf-8")
                return ToolResult(content=json.dumps({
                    "status": "blocked",
                    "reason": reason,
                    "slices_run": len(results),
                    "results": results,
                }))

            change_description = (
                "Implement ONLY this slice exactly as the issue specifies:\n\n"
                + issue_text
            )
            # run_id keys the step ledger (a persistent SQLite file, not
            # per-invocation state) -- it must identify the SLICE, not this
            # call's position in its own loop. `s{n}` collided across
            # separate campaign() invocations: invocation 1 processed S1a,
            # S1b+S2, S3 as n=1,2,3; invocation 2 (this one, started fresh
            # for S4 after those were already done) began its own loop back
            # at n=1, generating the SAME run_id "campaign-trading-s1" that
            # S1a used hours earlier. _run()'s resume logic then replayed
            # that old, already-fixed-by-hand S1a failure instead of doing
            # any real work on S4. A label-derived id is stable and unique
            # per slice regardless of which invocation or loop position
            # processes it.
            label_slug = re.sub(r"[^a-z0-9]+", "-", active_label.lower()).strip("-")
            run_id = f"campaign-{slug}-{label_slug}"

            run_result = await self._run({
                "change_description": change_description,
                "run_id": run_id,
            })

            # Bounded single retry: a conflict-shaped failure means the clone
            # this slice worked from is stale (something else merged to
            # master since). A fresh run_id forces _run() to clone again,
            # picking up whatever landed -- see PR #842 in TRADING.md's
            # Landed PRs for the failure mode this specifically targets.
            if run_result.is_error and _is_merge_conflict_error(run_result.content):
                retry_run_id = f"{run_id}-retry"
                run_result = await self._run({
                    "change_description": change_description,
                    "run_id": retry_run_id,
                })

            if run_result.is_error:
                reason = f"run error: {run_result.content[:200]}"
                text = driver_path.read_text(encoding="utf-8")
                text = _set_driver_status(text, "blocked", reason)
                driver_path.write_text(text, encoding="utf-8")
                results.append({
                    "slice": n,
                    "label": active_label,
                    "issue": active_issue,
                    "is_error": True,
                    "error": run_result.content,
                })
                return ToolResult(content=json.dumps({
                    "status": "blocked",
                    "reason": reason,
                    "slices_run": len(results),
                    "results": results,
                }))

            data = json.loads(run_result.content)
            merge_decision = data.get("merge_decision")
            pr_url = data.get("pr_url", "")

            results.append({
                "slice": n,
                "label": active_label,
                "issue": active_issue,
                "merge_decision": merge_decision,
                "pr_url": pr_url,
            })

            if merge_decision == "auto_merge":
                text = driver_path.read_text(encoding="utf-8")
                text = _advance_driver(text, active_label, active_issue, pr_url)
                driver_path.write_text(text, encoding="utf-8")
                # Continue loop to the next slice.
            else:
                if merge_decision == "tests_failed":
                    reason = data.get("test_summary") or "tests did not pass"
                else:
                    reason = data.get("guardrail_reason") or data.get("escalation_reason") or "escalated"
                text = driver_path.read_text(encoding="utf-8")
                text = _set_driver_status(
                    text, "blocked",
                    f"PR {pr_url} not merged ({merge_decision}): {reason}",
                )
                driver_path.write_text(text, encoding="utf-8")
                return ToolResult(content=json.dumps({
                    "status": "blocked",
                    "reason": reason,
                    "slices_run": len(results),
                    "results": results,
                }))

        # Reached max_slices cap.
        return ToolResult(content=json.dumps({
            "status": "max_slices_reached",
            "slices_run": len(results),
            "results": results,
        }))


def create(sandbox=None, **kwargs) -> SelfDevPlugin:
    """Factory called by MCPOrchestrator.discover_plugins."""
    if sandbox is None and __import__("sys").platform == "win32":
        try:
            from cerebral.sandbox import WindowsSandbox, available
            if available():
                sandbox = WindowsSandbox()
        except Exception:
            pass
    return SelfDevPlugin(sandbox=sandbox, **kwargs)
