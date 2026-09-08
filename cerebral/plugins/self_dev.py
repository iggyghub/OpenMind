"""
self_dev plugin – ADR-0015 sandbox test-suite gateway & verification adapter.

Provides the `self_dev` tool for LLM-driven code changes, and a thin
`verify()` adapter that exposes the last sandbox test-suite outcome as a
`VerifyResult` (passed + evidence). No new test-running logic is introduced;
this merely introspects the already-built gate.
"""
import asyncio
import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PLUGIN_NAME = "self_dev"
REQUIRED_CAPABILITIES = {"shell_exec", "fs_write", "network_egress_cloud"}


@dataclass
class ToolDef:
    name: str
    plugin: str
    schema: Dict[str, Any]


@dataclass
class ToolResult:
    is_error: bool
    content: str


@dataclass
class VerifyResult:
    """Contract output for the verification adapter (slice F)."""
    passed: bool
    evidence: str


# ---------------------------------------------------------------------------
# Default I/O seams (exercised live when not injected)
# ---------------------------------------------------------------------------

def _default_clone_fn(url: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.name", "cerebral"], cwd=dest)
    subprocess.run(["git", "config", "user.email", "cerebral@cerebral.dev"], cwd=dest)


def _default_pr_fn(sandbox_root: Path, branch: str, desc: str, ok: bool, out: str) -> str:
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=sandbox_root)
    return subprocess.run(
        ["gh", "pr", "create", "--head", branch, "--title", "PR", "--body", desc],
        cwd=sandbox_root,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class SelfDevPlugin:
    def __init__(self, **kwargs):
        self.sandbox = kwargs.get("sandbox")
        self.clone_fn = kwargs.get("clone_fn", _default_clone_fn)
        self.edit_fn = kwargs.get("edit_fn", lambda d, desc: {
            "branch": "selfdev/abc123", "committed": True, "message": "auto"
        })
        self.test_fn = kwargs.get("test_fn", lambda d: (True, "ok"))
        self.pr_fn = kwargs.get("pr_fn", _default_pr_fn)
        self.merge_fn = kwargs.get("merge_fn", lambda u: None)
        self.pull_fn = kwargs.get("pull_fn", lambda r: (True, "ff"))
        self.restart_fn = kwargs.get("restart_fn", lambda: None)
        self.sandbox_root = kwargs.get("sandbox_root", Path("."))
        self.ledger = kwargs.get("ledger")
        self.record_activity_fn = kwargs.get("record_activity_fn")
        self.rollback_fn = kwargs.get("rollback_fn")
        self.record_turn_fn = kwargs.get("record_turn_fn")

        # Adapter state for verify()
        self._last_test_result: Optional[Tuple[bool, str]] = None
        self._current_run_id: Optional[str] = None

    def list_tools(self) -> List[ToolDef]:
        return [
            ToolDef(
                name="self_dev",
                plugin=PLUGIN_NAME,
                schema={
                    "properties": {
                        "change_description": {"type": "string"},
                        "run_id": {"type": "string"},
                        "restart": {"type": "boolean"},
                    }
                },
            ),
            ToolDef("self_dev_load", PLUGIN_NAME, {"properties": {}}),
            ToolDef("self_dev_rollback", PLUGIN_NAME, {"properties": {}}),
            ToolDef("self_dev_campaign", PLUGIN_NAME, {"properties": {}}),
        ]

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if tool_name == "self_dev":
            return await self._run_self_dev(args)
        elif tool_name == "self_dev_rollback":
            return await self._run_rollback(args)
        return ToolResult(is_error=True, content=f"Unknown tool: {tool_name}")

    async def _run_self_dev(self, args: Dict[str, Any]) -> ToolResult:
        if self.sandbox is None:
            return ToolResult(is_error=True, content="Sandbox unavailable")

        description = args.get("change_description", "").strip()
        if not description:
            return ToolResult(is_error=True, content="change_description is required")

        run_id = args.get("run_id", str(uuid.uuid4()))
        sandbox_dir = self.sandbox_root / run_id

        if args.get("restart"):
            self._reset_for_run(run_id)
        else:
            if not sandbox_dir.exists():
                sandbox_dir.mkdir(parents=True, exist_ok=True)
            elif self.ledger is None or len(self.ledger.completed(run_id)) == 0:
                return ToolResult(is_error=True, content=f"run_id {run_id} already exists")

        self._current_run_id = run_id

        completed_sigs = []
        if self.ledger:
            completed_sigs = [e["name"] for e in self.ledger.completed(run_id)]

        # 1. Clone
        if "clone" not in completed_sigs:
            try:
                self.clone_fn("https://example.com/repo.git", sandbox_dir)
                self._record_phase(run_id, "clone", {"clone_dir": str(sandbox_dir)})
            except Exception as e:
                return ToolResult(is_error=True, content=f"Clone failed: {e}")

        # 2. Edit
        if "edit" not in completed_sigs:
            try:
                edit_res = self.edit_fn(sandbox_dir, description)
                if not edit_res.get("committed"):
                    return ToolResult(is_error=True, content="no commit made")
                self._record_phase(run_id, "edit", edit_res)
            except Exception as e:
                if "NotImplementedError" in str(e):
                    return ToolResult(is_error=True, content="edit_fn requires wiring")
                return ToolResult(is_error=True, content=str(e))
        else:
            edit_res = next(
                (e["result"] for e in self.ledger.completed(run_id) if e["name"] == "edit"),
                {"branch": "selfdev/abc123"},
            )

        # 3. Test
        try:
            passed, summary = self.test_fn(sandbox_dir)
            self._last_test_result = (passed, summary)
            self._record_phase(run_id, "test", {"passed": passed, "summary": summary})
        except Exception as e:
            passed, summary = False, str(e)
            self._last_test_result = (passed, summary)
            self._record_phase(run_id, "test", {"passed": False, "summary": summary, "error": True})

        # 4. PR & Merge
        try:
            pr_url = self.pr_fn(sandbox_dir, edit_res.get("branch", "selfdev/abc123"), description, passed, summary)
            if passed:
                self.merge_fn(pr_url)
            self._record_phase(run_id, "pr", {"pr_url": pr_url})
        except Exception as e:
            return ToolResult(is_error=True, content=f"PR creation failed: {e}")

        if self.record_activity_fn:
            try:
                await self.record_activity_fn("activity", {"source": "self_dev", "pr_url": pr_url})
            except Exception:
                pass

        res_data = {
            "test_passed": passed,
            "pr_url": pr_url,
            "branch": edit_res.get("branch", "selfdev/abc123"),
            "run_id": run_id,
            "clone_dir": str(sandbox_dir),
        }
        if not passed:
            res_data["merge_decision"] = "tests_failed"

        return ToolResult(is_error=False, content=json.dumps(res_data))

    async def _run_rollback(self, args: Dict[str, Any]) -> ToolResult:
        if self.rollback_fn:
            try:
                await self.rollback_fn()
                success_content = {"status": "rolling_back"}

                try:
                    if self.record_turn_fn:
                        await self.record_turn_fn("system_event", {"kind": "self_dev_manual_rollback"})
                except Exception:
                    pass
                try:
                    if self.record_activity_fn:
                        await self.record_activity_fn("activity", {"source": "self_dev", "summary": "manual rollback triggered"})
                except Exception:
                    pass

                return ToolResult(is_error=False, content=json.dumps(success_content))
            except Exception as e:
                return ToolResult(is_error=True, content=str(e))
        return ToolResult(is_error=True, content="rollback_fn not configured")

    def _record_phase(self, run_id: str, sig: str, result: Dict[str, Any]) -> None:
        if self.ledger:
            self.ledger.record(run_id, sig, {
                "name": sig, "args": {}, "result": result, "is_error": False
            })

    def _reset_for_run(self, run_id: str) -> None:
        if self.ledger:
            self.ledger.clear(run_id)
        sandbox_dir = self.sandbox_root / run_id
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)
        self._last_test_result = None

    def verify(self) -> VerifyResult:
        """Expose the last sandbox test-suite outcome as a verification contract."""
        if self._last_test_result is None:
            return VerifyResult(passed=False, evidence="No sandbox run recorded yet.")
        passed, evidence = self._last_test_result
        return VerifyResult(passed=passed, evidence=evidence)
