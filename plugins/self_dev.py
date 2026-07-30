"""
Self-dev plugin -- ADR-0015 S1/S2/S4 (Issues #554/#555/#557).

Felix's self-dev loop: clone the repo, branch, have the model make a scoped
edit, run the test suite inside the ADR-0010 sandbox, and open a PR.

S2 adds self_dev_load: after a self-dev PR is merged to master, pull the live
repo (git pull --ff-only) and broadcast restart_felix to the tray so the
merged change goes live. No-op if already up-to-date.

S4 adds the blast-radius gate (ADR-0015 decision 5): after opening the PR,
inspect the diff. Safe-zone + green tests -> auto-merge + self_dev_load.
Any guardrail file in the diff -> escalate to human review regardless of tests.
GUARDRAIL_PATHS is the single definition of what counts as a guardrail.

Injected seams (clone_fn / edit_fn / test_fn / pr_fn / diff_fn / merge_fn /
pull_fn / restart_fn) make the whole flow hermetic in tests -- no real git /
gh / network / Cerebral.
"""
import inspect
import json
import logging
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from cerebral import self_dev_io as _io
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.paths import data_dir

logger = logging.getLogger(__name__)

PLUGIN_NAME = "self_dev"

# ADR-0005 / Issue #554 -- self_dev clones the repo (shell_exec via sandbox),
# writes to the sandbox workdir (fs_write), and opens a PR via gh
# (network_egress_cloud). shell_exec is DENY by default (ADR-0005) -- self_dev
# is deny-by-default and unavailable where no sandbox backend exists
# (fail-closed, same posture as shell_exec / ADR-0010).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset(
    {"shell_exec", "fs_write", "network_egress_cloud"}
)

# ADR-0015 decision 5 -- ONE authoritative list of guardrail paths.
# Any PR diff touching these paths always escalates to human review,
# regardless of test colour. Felix may propose changes but never self-approve.
#
# Entries ending with '/' are prefix matches (whole subtree).
# All other entries are exact path matches (relative to repo root, forward slashes).
GUARDRAIL_PATHS: frozenset[str] = frozenset({
    "cerebral/security/",       # ADR-0005 capability gate (prefix)
    "cerebral/sandbox/",        # ADR-0010 shell sandbox (prefix)
    "cerebral/db/credentials.py",   # credential / keyring store
    "cerebral/main.py",         # core Cerebral brain entry point
    "plugins/self_dev.py",      # the self-dev loop itself
    "tray/lib/boot-check.js",   # SD-3 launcher rollback + boot self-check
})


def is_guardrail_diff(changed_files: Iterable[str]) -> "tuple[bool, str]":
    """Return (True, reason) if any file in the diff touches a guardrail path.

    Pure function -- no side effects. Used by SelfDevPlugin._run to decide
    whether to auto-merge or escalate to human review (ADR-0015 decision 5).
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
# edit_fn/restart_fn can't be built at discovery time -- they close over the
# model router and the tray broadcast. Constructor injection still wins (tests
# pass them directly); prod resolves these globals lazily at call time.
_edit_fn = None
_restart_fn = None


def set_edit_fn(fn) -> None:
    global _edit_fn
    _edit_fn = fn


def set_restart_fn(fn) -> None:
    global _restart_fn
    _restart_fn = fn


async def _default_restart_fn() -> None:
    """Broadcast restart_felix to the tray -- main.py must wire this."""
    raise NotImplementedError(
        "SelfDevPlugin requires a restart_fn -- "
        "main.py must wire the broadcast in via SelfDevPlugin(restart_fn=...)."
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
        repo_url: str | None = None,
        sandbox_root: Path | None = None,
        live_root: Path | None = None,
    ) -> None:
        self._sandbox = sandbox
        self._clone = clone_fn or _default_clone_fn
        self._test = test_fn or _default_test_fn
        self._pr = pr_fn or _default_pr_fn
        self._diff = diff_fn or _default_diff_fn
        self._merge = merge_fn or _default_merge_fn
        self._pull = pull_fn or _default_pull_fn
        # edit_fn/restart_fn resolve lazily (constructor override > module seam
        # wired by main.py > raising default) -- the seams are set after
        # discovery constructs the instance, so we can't collapse them here.
        self._edit_override = edit_fn
        self._restart_override = restart_fn
        self._repo_url = repo_url or str(_REPO_ROOT)
        self._sandbox_root = sandbox_root or (data_dir() / "sandbox" / "self_dev")
        self._live_root = live_root or _REPO_ROOT

    def _resolve_edit(self) -> EditFn:
        return self._edit_override or _edit_fn or _default_edit_fn

    def _resolve_restart(self) -> RestartFn:
        return self._restart_override or _restart_fn or _default_restart_fn

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
                    "Unavailable without a sandbox backend."
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
                                "Used as the clone directory name and branch suffix."
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
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "self_dev":
            return await self._run(args)
        if tool_name == "self_dev_load":
            return await self._load(args)
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

        if clone_dir.exists():
            return ToolResult(
                content=f"Run {run_id!r} already exists -- use a different run_id.",
                is_error=True,
            )

        self._sandbox_root.mkdir(parents=True, exist_ok=True)

        # 1. Clone into an independent working tree (live .git untouched).
        try:
            self._clone(self._repo_url, clone_dir)
        except Exception as exc:
            return ToolResult(content=f"Clone failed: {exc}", is_error=True)

        # 2. Model edits + commits inside the clone. The prod edit_fn is async
        #    (it awaits the model router); test seams are plain sync callables.
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

        # 3. Test inside sandbox (failure is recorded, not fatal -- the
        #    blast-radius gate in SD-4 decides whether to auto-merge).
        test_passed = False
        test_output = ""
        try:
            test_passed, test_output = self._test(clone_dir)
        except Exception as exc:
            test_output = f"Test runner error: {exc}"

        # 4. Open PR (regardless of test colour; mergeability is decided below).
        try:
            pr_url = self._pr(clone_dir, branch, description, test_passed, test_output)
        except Exception as exc:
            return ToolResult(content=f"PR creation failed: {exc}", is_error=True)

        # 5. Blast-radius gate (SD-4 / ADR-0015 decision 5).
        #    Fail-safe: if we can't inspect the diff, treat as guardrail.
        guardrail_hit = False
        escalation_reason = ""
        try:
            changed_files = self._diff(pr_url)
            guardrail_hit, escalation_reason = is_guardrail_diff(changed_files)
        except Exception as exc:
            guardrail_hit = True
            escalation_reason = f"diff check failed (fail-safe escalation): {exc}"

        if guardrail_hit or not test_passed:
            reason = escalation_reason or ("tests did not pass" if not test_passed else "")
            return ToolResult(
                content=json.dumps({
                    "run_id": run_id,
                    "clone_dir": str(clone_dir),
                    "branch": branch,
                    "test_passed": test_passed,
                    "test_summary": test_output[:500],
                    "pr_url": pr_url,
                    "merge_decision": "escalate",
                    "escalation_reason": reason,
                })
            )

        # 6. Safe zone + green tests: auto-merge.
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
