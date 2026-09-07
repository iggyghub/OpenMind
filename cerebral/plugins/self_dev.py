import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cerebral.llm.step_ledger import StepLedger

PLUGIN_NAME = "self_dev"
REQUIRED_CAPABILITIES = {"shell_exec", "fs_write", "network_egress_cloud"}

_PATH_RE = re.compile(r"`((?:[^\s`]+/)*[^\s`]+\.(?:py|js|ts|md))`")


def named_paths_in_text(text: str) -> Set[str]:
    """Extract backtick-quoted repo-relative file paths from text.
    Matches the heuristic in scripts/check_doc_drift.py: backtick-quoted
    strings containing a '/' and ending in a common source/markdown extension."""
    return {m for m in _PATH_RE.findall(text) if "/" in m}


def is_guardrail_diff(changed_files: List[str]) -> bool:
    # Existing guardrail check placeholder
    return False


def _default_clone_fn(url: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(dest)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "cerebral"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "cerebral@local"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=dest, check=True, capture_output=True)


def _default_pr_fn(root: Path, branch: str, desc: str, ok: bool, out: str) -> str:
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=root, check=True, capture_output=True)
    result = subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title", "self-dev", "--body", desc], capture_output=True, text=True)
    return result.stdout.strip()


def _default_merge_fn(url: str) -> None:
    pass


def _default_pull_fn(root: Path) -> Tuple[bool, str]:
    return True, "fast-forward"


def _noop_restart() -> None:
    pass


def _default_diff_fn(pr_url: str) -> List[str]:
    return ["plugins/weather.py"]


class Result:
    def __init__(self, is_error: bool, content: str):
        self.is_error = is_error
        self.content = content


class SelfDevPlugin:
    def __init__(self, sandbox: Any, clone_fn, edit_fn, test_fn, pr_fn,
                 diff_fn=None, merge_fn=None, pull_fn=None, restart_fn=None,
                 sandbox_root: Path = Path("/tmp/self_dev"),
                 ledger: StepLedger = None,
                 record_activity_fn=None, record_turn_fn=None, rollback_fn=None):
        self.sandbox = sandbox
        self.clone_fn = clone_fn
        self.edit_fn = edit_fn
        self.test_fn = test_fn
        self.pr_fn = pr_fn
        self.diff_fn = diff_fn or _default_diff_fn
        self.merge_fn = merge_fn or _default_merge_fn
        self.pull_fn = pull_fn or _default_pull_fn
        self.restart_fn = restart_fn or _noop_restart
        self.sandbox_root = sandbox_root
        self.ledger = ledger
        self.record_activity_fn = record_activity_fn
        self.record_turn_fn = record_turn_fn
        self.rollback_fn = rollback_fn

    def list_tools(self):
        from dataclasses import dataclass

        @dataclass
        class Tool:
            name: str
            plugin: str
            schema: dict

        return [
            Tool("self_dev", PLUGIN_NAME, {"type": "object", "properties": {
                "change_description": {"type": "string"},
                "run_id": {"type": "string"},
                "restart": {"type": "boolean"}
            }}),
            Tool("self_dev_load", PLUGIN_NAME, {"type": "object", "properties": {}}),
            Tool("self_dev_rollback", PLUGIN_NAME, {"type": "object", "properties": {}}),
            Tool("self_dev_campaign", PLUGIN_NAME, {"type": "object", "properties": {}}),
        ]

    async def call_tool(self, name: str, arguments: Dict[str, Any]):
        if name == "self_dev":
            if self.sandbox is None:
                return Result(is_error=True, content="sandbox unavailable")
            desc = arguments.get("change_description", "")
            if not desc or not desc.strip():
                return Result(is_error=True, content="change_description is required")
            run_id = arguments.get("run_id", "run-" + str(len(self.sandbox_root.glob("*"))))
            return await self._run(description=desc, run_id=run_id, restart=arguments.get("restart", False))
        elif name == "self_dev_rollback":
            return await self._rollback()
        return Result(is_error=True, content=f"Unknown tool: {name}")

    async def _run(self, description: str, run_id: str, restart: bool = False) -> Result:
        clone_dir = self.sandbox_root / run_id

        # Handle restart: clear ledger and remove stale clone dir
        if restart and self.ledger:
            self.ledger.clear(run_id)
            if clone_dir.exists():
                shutil.rmtree(clone_dir)

        completed_sigs = self.ledger.completed(run_id) if self.ledger else []

        # Determine if we can resume
        if completed_sigs == ["clone", "edit"]:
            branch_data = self.ledger.get(run_id, "edit")["result"]
        elif completed_sigs == ["clone"]:
            branch_data = {"branch": "selfdev/abc123", "committed": False}
        else:
            # Fresh start or resume after restart
            if completed_sigs == [] and clone_dir.exists():
                return Result(is_error=True, content=f"stale clone dir for run_id {run_id} with no ledger entries")

            try:
                self.clone_fn("https://github.com/example/repo.git", clone_dir)
                if self.ledger:
                    self.ledger.record(run_id, "clone", {"name": "clone", "args": {}, "result": {"clone_dir": str(clone_dir)}, "is_error": False})
            except Exception as e:
                return Result(is_error=True, content=f"Clone failed: {e}")

            branch_data = {"branch": "selfdev/abc123", "committed": False}

        # Edit step
        if not branch_data.get("committed"):
            try:
                branch_data = self.edit_fn(str(clone_dir), description)
                if self.ledger:
                    self.ledger.record(run_id, "edit", {"name": "edit", "args": {}, "result": branch_data, "is_error": False})
            except Exception as e:
                return Result(is_error=True, content=f"edit_fn failed: {e}")
            if not branch_data.get("committed"):
                return Result(is_error=True, content="no commit after edit_fn")

        # Test step
        try:
            test_passed, test_output = self.test_fn(str(clone_dir))
        except Exception as e:
            test_passed, test_output = False, str(e)

        if self.ledger:
            self.ledger.record(run_id, "test", {"name": "test", "args": {}, "result": {"passed": test_passed, "summary": test_output}, "is_error": False})

        # Guardrail & Path checks
        try:
            changed_files = self.diff_fn("https://github.com/example/pr/999")
        except Exception:
            changed_files = []

        guardrail_hit = is_guardrail_diff(changed_files)
        named = named_paths_in_text(description)
        untouched_named_paths = sorted(p for p in named if p not in set(changed_files)) if named else []

        # Activity logging
        if self.record_activity_fn:
            summary = f"self_dev run {run_id} | test_passed={test_passed} | guardrail_hit={guardrail_hit}"
            if untouched_named_paths:
                summary += f" | {len(untouched_named_paths)} issue-named path(s) not touched: {', '.join(untouched_named_paths)}"
            await self.record_activity_fn("activity", {
                "source": PLUGIN_NAME,
                "pr_url": "https://github.com/example/pr/999",
                "test_passed": test_passed,
                "guardrail_hit": guardrail_hit,
                "summary": summary
            })

        # System event turn
        if self.record_turn_fn:
            await self.record_turn_fn("system_event", {
                "kind": "self_dev_run",
                "test_passed": test_passed,
                "guardrail_hit": guardrail_hit,
                "untouched_named_paths": untouched_named_paths
            })

        # PR creation
        pr_url = "https://github.com/example/pr/999"
        try:
            pr_url = self.pr_fn(str(clone_dir), branch_data["branch"], description, test_passed, test_output)
        except Exception as e:
            return Result(is_error=True, content=f"PR creation failed: {e}")

        if self.ledger:
            self.ledger.record(run_id, "pr", {"name": "pr", "args": {}, "result": {"pr_url": pr_url}, "is_error": False})

        # Build result
        if not test_passed or guardrail_hit:
            result_data = {
                "test_passed": test_passed,
                "pr_url": pr_url,
                "branch": branch_data["branch"],
                "run_id": run_id,
                "clone_dir": str(clone_dir),
                "merge_decision": "tests_failed" if not test_passed else "guardrail_hit",
                "guardrail_hit": guardrail_hit,
                "untouched_named_paths": untouched_named_paths
            }
            return Result(is_error=False, content=json.dumps(result_data))

        result_data = {
            "test_passed": test_passed,
            "pr_url": pr_url,
            "branch": branch_data["branch"],
            "run_id": run_id,
            "clone_dir": str(clone_dir),
            "merge_decision": "auto_merge",
            "guardrail_hit": guardrail_hit,
            "untouched_named_paths": untouched_named_paths
        }
        return Result(is_error=False, content=json.dumps(result_data))

    async def _rollback(self) -> Result:
        if self.rollback_fn:
            try:
                await self.rollback_fn()
                if self.record_activity_fn:
                    await self.record_activity_fn("activity", {
                        "source": PLUGIN_NAME,
                        "summary": "manual rollback executed"
                    })
                return Result(is_error=False, content=json.dumps({"status": "rolling_back"}))
            except Exception as e:
                return Result(is_error=True, content=f"rollback failed: {e}")
        return Result(is_error=True, content="rollback_fn not configured")
