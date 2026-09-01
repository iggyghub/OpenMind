"""
Self-dev plugin tests -- ADR-0015 S1 (Issue #554).

All side effects are injected (clone_fn / edit_fn / test_fn / pr_fn) so
the suite runs hermetically: no real git, no gh, no network, no Cerebral.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.self_dev import SelfDevPlugin, PLUGIN_NAME, REQUIRED_CAPABILITIES
from cerebral.llm.step_ledger import StepLedger


# ---------------------------------------------------------------------------
# Fake sandbox -- signals availability without real Windows Job Objects.
# ---------------------------------------------------------------------------

class _FakeSandbox:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PR_URL = "https://github.com/iggyghub/OpenMind/pull/999"


async def _noop_restart() -> None:
    pass


def _make(tmp_path: Path, **overrides) -> SelfDevPlugin:
    """Build a SelfDevPlugin with all side-effects injected as fakes."""
    defaults: dict = {
        "sandbox": _FakeSandbox(),
        # clone_fn creates the dest dir (git clone would do the same).
        "clone_fn": lambda url, dest: dest.mkdir(parents=True, exist_ok=True),
        "edit_fn": lambda d, desc: {
            "branch": "selfdev/abc123",
            "committed": True,
            "message": "chore: self-dev edit",
        },
        "test_fn": lambda d: (True, "1 passed in 0.01s"),
        "pr_fn": lambda d, br, desc, ok, out: _PR_URL,
        # diff_fn/merge_fn/pull_fn/restart_fn: since the 2026-08-21 "full
        # auto-merge" amendment removed the escalate-before-merge gate,
        # _run() always reaches _merge()/_load() now -- these must be
        # hermetic fakes like the rest of this table, not the real
        # gh/git-shelling defaults (previously masked because a real
        # _default_diff_fn failure against a fake PR happened to escalate
        # and stop the run before merge was ever attempted).
        "diff_fn": lambda url: ["plugins/weather.py"],
        "merge_fn": lambda url: None,
        "pull_fn": lambda root: (True, "fast-forward"),
        "restart_fn": _noop_restart,
        "sandbox_root": tmp_path / "self_dev",
        # #780 -- isolated per-test ledger (mirrors tests/test_step_ledger.py),
        # so resume state never leaks across tests/run_ids.
        "ledger": StepLedger(db_path=tmp_path / "ledger.db"),
    }
    defaults.update(overrides)
    return SelfDevPlugin(**defaults)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_plugin_name():
    assert PLUGIN_NAME == "self_dev"


def test_required_capabilities_in_vocabulary():
    from cerebral.security import CAPABILITY_VOCABULARY
    unknown = REQUIRED_CAPABILITIES - CAPABILITY_VOCABULARY
    assert not unknown, f"Unknown capabilities: {unknown}"


def test_required_capabilities_include_shell_exec():
    # shell_exec is DENY by default -- ensures self_dev is deny-by-default.
    assert "shell_exec" in REQUIRED_CAPABILITIES
    assert "fs_write" in REQUIRED_CAPABILITIES
    assert "network_egress_cloud" in REQUIRED_CAPABILITIES


def test_list_tools(tmp_path):
    plugin = _make(tmp_path)
    tools = plugin.list_tools()
    names = [t.name for t in tools]
    assert "self_dev" in names
    assert "self_dev_load" in names
    assert "self_dev_rollback" in names
    assert "self_dev_campaign" in names
    t = next(t for t in tools if t.name == "self_dev")
    assert t.plugin == PLUGIN_NAME
    assert "change_description" in t.schema["properties"]


# ---------------------------------------------------------------------------
# call_tool dispatch
# ---------------------------------------------------------------------------

async def test_unknown_tool_returns_error(tmp_path):
    plugin = _make(tmp_path)
    result = await plugin.call_tool("no_such_tool", {})
    assert result.is_error


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

async def test_missing_description_is_error(tmp_path):
    plugin = _make(tmp_path)
    result = await plugin.call_tool("self_dev", {})
    assert result.is_error
    assert "change_description" in result.content


async def test_empty_description_is_error(tmp_path):
    plugin = _make(tmp_path)
    result = await plugin.call_tool("self_dev", {"change_description": "   "})
    assert result.is_error


# ---------------------------------------------------------------------------
# Fail-closed: no sandbox
# ---------------------------------------------------------------------------

async def test_no_sandbox_fails_closed(tmp_path):
    plugin = _make(tmp_path, sandbox=None)
    result = await plugin.call_tool("self_dev", {"change_description": "anything"})
    assert result.is_error
    assert "unavailable" in result.content.lower()


# ---------------------------------------------------------------------------
# Duplicate run_id
# ---------------------------------------------------------------------------

async def test_duplicate_run_id_rejected(tmp_path):
    run_id = "fixed-run-001"
    sandbox_root = tmp_path / "self_dev"
    sandbox_root.mkdir()
    (sandbox_root / run_id).mkdir()  # pre-existing entry

    plugin = _make(tmp_path, sandbox_root=sandbox_root)
    result = await plugin.call_tool("self_dev", {
        "change_description": "something",
        "run_id": run_id,
    })
    assert result.is_error
    assert run_id in result.content


# ---------------------------------------------------------------------------
# Happy path: green tests -> PR opened
# ---------------------------------------------------------------------------

async def test_happy_path_green_pr(tmp_path):
    plugin = _make(tmp_path)
    result = await plugin.call_tool("self_dev", {"change_description": "Add a README comment"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["test_passed"] is True
    assert data["pr_url"] == _PR_URL
    assert data["branch"] == "selfdev/abc123"
    assert "run_id" in data
    assert "clone_dir" in data


# ---------------------------------------------------------------------------
# Failing tests open a PR but do NOT merge (2026-08-22 test-status gate --
# see PR #840/#842 in TRADING.md for what merging red tests actually cost).
# ---------------------------------------------------------------------------

async def test_test_failure_opens_pr_but_does_not_merge(tmp_path):
    pr_calls = []
    merge_calls = []

    def pr_fn(d, br, desc, ok, out):
        pr_calls.append({"ok": ok, "out": out})
        return _PR_URL

    plugin = _make(
        tmp_path,
        test_fn=lambda d: (False, "1 failed, 0 passed"),
        pr_fn=pr_fn,
        merge_fn=lambda url: merge_calls.append(url),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Broken change"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["test_passed"] is False
    assert data["pr_url"] == _PR_URL
    assert data["merge_decision"] == "tests_failed"
    assert len(pr_calls) == 1
    assert pr_calls[0]["ok"] is False
    assert merge_calls == [], "merge_fn must not be called when tests fail"


async def test_test_failure_records_an_activity_entry(tmp_path):
    """S26 (#879): a PR needing human review (guardrail hit or failed
    tests) is a real decision, logged to the Activity Log -- separate from
    the #810 pending-review card's record_turn_fn call."""
    recorded = []

    async def fake_record_activity(kind, content):
        recorded.append((kind, content))

    plugin = _make(
        tmp_path,
        test_fn=lambda d: (False, "1 failed, 0 passed"),
        record_activity_fn=fake_record_activity,
    )
    await plugin.call_tool("self_dev", {"change_description": "Broken change"})

    assert len(recorded) == 1
    kind, content = recorded[0]
    assert kind == "activity"
    assert content["source"] == "self_dev"
    assert content["pr_url"] == _PR_URL


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

async def test_clone_failure_is_error(tmp_path):
    def bad_clone(url, dest):
        raise RuntimeError("network unreachable")

    plugin = _make(tmp_path, clone_fn=bad_clone)
    result = await plugin.call_tool("self_dev", {"change_description": "anything"})
    assert result.is_error
    assert "Clone failed" in result.content


async def test_edit_not_implemented_is_error(tmp_path):
    """Default edit_fn raises NotImplementedError (needs main.py wiring)."""
    plugin = SelfDevPlugin(
        sandbox=_FakeSandbox(),
        clone_fn=lambda url, dest: dest.mkdir(parents=True, exist_ok=True),
        # edit_fn left as default -> raises NotImplementedError
        sandbox_root=tmp_path / "self_dev",
    )
    result = await plugin.call_tool("self_dev", {"change_description": "anything"})
    assert result.is_error
    assert "edit_fn" in result.content or "requires" in result.content.lower()


async def test_no_commit_aborts(tmp_path):
    plugin = _make(tmp_path, edit_fn=lambda d, desc: {"branch": "b", "committed": False})
    result = await plugin.call_tool("self_dev", {"change_description": "anything"})
    assert result.is_error
    assert "no commit" in result.content.lower()


async def test_pr_failure_is_error(tmp_path):
    def bad_pr(d, br, desc, ok, out):
        raise RuntimeError("gh: authentication failed")

    plugin = _make(tmp_path, pr_fn=bad_pr)
    result = await plugin.call_tool("self_dev", {"change_description": "anything"})
    assert result.is_error
    assert "PR creation failed" in result.content


async def test_test_runner_exception_does_not_prevent_pr(tmp_path):
    """A crashing test_fn still allows the PR to be opened."""
    def crashing_test(d):
        raise RuntimeError("test runner crashed")

    pr_calls = []

    def pr_fn(d, br, desc, ok, out):
        pr_calls.append({"ok": ok, "out": out})
        return _PR_URL

    plugin = _make(tmp_path, test_fn=crashing_test, pr_fn=pr_fn)
    result = await plugin.call_tool("self_dev", {"change_description": "anything"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["test_passed"] is False
    assert len(pr_calls) == 1
    assert "crashed" in pr_calls[0]["out"]


# ---------------------------------------------------------------------------
# S27: one bounded self-fix attempt when tests fail
# ---------------------------------------------------------------------------

async def test_failing_tests_get_one_fix_attempt_that_succeeds(tmp_path):
    """The second edit_fn call sees the failure output; if it fixes things,
    the run proceeds to auto-merge like any other green run."""
    edit_calls = []
    test_calls = []

    def edit_fn(d, desc):
        edit_calls.append(desc)
        return {"branch": "selfdev/abc123", "committed": True}

    def test_fn(d):
        test_calls.append(1)
        return (len(test_calls) >= 2, "1 failed" if len(test_calls) < 2 else "1 passed")

    merge_calls = []
    plugin = _make(
        tmp_path, edit_fn=edit_fn, test_fn=test_fn,
        merge_fn=lambda url: merge_calls.append(url),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Add a thing"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["test_passed"] is True
    assert data["merge_decision"] == "auto_merge"
    assert len(edit_calls) == 2, "edit_fn must run again with the failure context"
    assert "Test failure output" in edit_calls[1]
    assert "1 failed" in edit_calls[1]
    assert len(test_calls) == 2, "tests must re-run once after the fix attempt"
    assert merge_calls == [_PR_URL]


async def test_failing_tests_stay_failed_after_fix_attempt(tmp_path):
    """If the fix attempt doesn't help, the run still opens a red PR (one
    retry, not a loop) instead of merging."""
    edit_calls = []
    plugin = _make(
        tmp_path,
        edit_fn=lambda d, desc: (edit_calls.append(desc), {"branch": "b", "committed": True})[1],
        test_fn=lambda d: (False, "still failing"),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Broken change"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["test_passed"] is False
    assert data["merge_decision"] == "tests_failed"
    assert len(edit_calls) == 2, "exactly one fix attempt, not an unbounded loop"


async def test_retest_phase_is_resumable(tmp_path):
    """A crash after the fix attempt is recorded replays the outcome instead
    of attempting a third edit."""
    run_id = "post-fix-crash"
    ledger = StepLedger(db_path=tmp_path / "ledger.db")
    ledger.record(run_id, "clone", {"name": "clone", "args": {}, "result": {}, "is_error": False})
    ledger.record(run_id, "edit", {
        "name": "edit", "args": {},
        "result": {"branch": "selfdev/x", "committed": True}, "is_error": False,
    })
    ledger.record(run_id, "test", {
        "name": "test", "args": {}, "result": {"passed": False, "summary": "1 failed"}, "is_error": False,
    })
    ledger.record(run_id, "retest", {
        "name": "retest", "args": {}, "result": {"passed": True, "summary": "1 passed"}, "is_error": False,
    })

    edit_calls = []
    plugin = _make(
        tmp_path,
        ledger=ledger,
        edit_fn=lambda d, desc: (edit_calls.append(desc), {"branch": "should-not-run", "committed": True})[1],
    )
    result = await plugin.call_tool("self_dev", {"change_description": "x", "run_id": run_id})

    assert not result.is_error, result.content
    assert edit_calls == [], "the recorded retest result must be reused, not re-attempted"
    data = json.loads(result.content)
    assert data["test_passed"] is True
    assert data["merge_decision"] == "auto_merge"


# ---------------------------------------------------------------------------
# Custom run_id flows through
# ---------------------------------------------------------------------------

async def test_custom_run_id(tmp_path):
    plugin = _make(tmp_path)
    result = await plugin.call_tool("self_dev", {
        "change_description": "Add a comment",
        "run_id": "my-custom-run",
    })
    assert not result.is_error
    data = json.loads(result.content)
    assert data["run_id"] == "my-custom-run"
    assert "my-custom-run" in data["clone_dir"]


# ---------------------------------------------------------------------------
# Default I/O seams -- every other test injects these, so they are only ever
# exercised live. Regression for the two bugs the first live dry-run surfaced:
# a fresh clone has no committer identity, and `git push` without -u makes
# `gh pr create` refuse. These patch subprocess.run to assert the real commands.
# ---------------------------------------------------------------------------

def _capture_subprocess(monkeypatch, stdout=""):
    calls: list[list[str]] = []

    class _R:
        returncode = 0

        def __init__(self):
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))
        return _R()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_default_clone_sets_committer_identity(monkeypatch, tmp_path):
    from plugins import self_dev

    calls = _capture_subprocess(monkeypatch, stdout="somevalue")
    self_dev._default_clone_fn("https://example.test/repo.git", tmp_path / "clone")

    assert any(c[:2] == ["git", "clone"] for c in calls), "must clone"
    assert any("config" in c and "user.name" in c for c in calls), \
        "fresh clone must be given a user.name"
    assert any("config" in c and "user.email" in c for c in calls), \
        "fresh clone must be given a user.email"


def test_default_pr_pushes_with_upstream(monkeypatch, tmp_path):
    from plugins import self_dev

    calls = _capture_subprocess(monkeypatch, stdout="https://github.com/x/y/pull/1")
    url = self_dev._default_pr_fn(tmp_path, "selfdev/x", "desc", True, "output")

    push = next(c for c in calls if c[:2] == ["git", "push"])
    assert "-u" in push, f"push must set upstream so gh pr create works: {push}"
    gh = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
    assert "--head" in gh and "selfdev/x" in gh, \
        f"gh pr create must name the head branch explicitly: {gh}"
    assert url == "https://github.com/x/y/pull/1"


# ---------------------------------------------------------------------------
# Event-loop freeze fix: test_fn must run off the event loop thread.
# ---------------------------------------------------------------------------

async def test_fresh_run_records_each_phase_in_order(tmp_path):
    """#780 -- a fresh run_id with no prior ledger state records clone, edit,
    test, pr in execution order, and behaves byte-identical to before."""
    ledger = StepLedger(db_path=tmp_path / "ledger.db")
    plugin = _make(tmp_path, ledger=ledger)
    result = await plugin.call_tool("self_dev", {
        "change_description": "Add a README comment",
        "run_id": "fresh-run",
    })

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["test_passed"] is True
    assert data["pr_url"] == _PR_URL

    sigs = [s["sig"] for s in ledger.completed("fresh-run")]
    assert sigs == ["clone", "edit", "test", "pr"]


async def test_resume_skips_edit_fn_but_reaches_test_and_pr(tmp_path):
    """#780 -- the whole point: a run interrupted after clone+edit is
    re-invoked with the same run_id. The recorded edit is reused -- edit_fn
    is never called -- and the run still proceeds through test and PR."""
    run_id = "interrupted-run"
    sandbox_root = tmp_path / "self_dev"
    clone_dir = sandbox_root / run_id
    clone_dir.mkdir(parents=True)  # the "kept" clone dir from the dead run

    ledger = StepLedger(db_path=tmp_path / "ledger.db")
    ledger.record(run_id, "clone", {
        "name": "clone", "args": {}, "result": {"clone_dir": str(clone_dir)}, "is_error": False,
    })
    ledger.record(run_id, "edit", {
        "name": "edit", "args": {},
        "result": {"branch": "selfdev/interrupted", "committed": True},
        "is_error": False,
    })

    edit_calls = []

    def spy_edit(d, desc):
        edit_calls.append((d, desc))
        return {"branch": "should-not-be-used", "committed": True}

    test_calls = []
    pr_calls = []

    plugin = _make(
        tmp_path,
        sandbox_root=sandbox_root,
        ledger=ledger,
        edit_fn=spy_edit,
        test_fn=lambda d: (test_calls.append(d), (True, "1 passed"))[1],
        pr_fn=lambda d, br, desc, ok, out: (pr_calls.append(br), _PR_URL)[1],
    )
    result = await plugin.call_tool("self_dev", {
        "change_description": "anything",
        "run_id": run_id,
    })

    assert not result.is_error, result.content
    assert edit_calls == [], "edit_fn must not be called -- the edit was already recorded"
    assert len(test_calls) == 1, "test must still run"
    assert len(pr_calls) == 1, "pr must still run"
    data = json.loads(result.content)
    assert data["branch"] == "selfdev/interrupted"  # from the RECORDED edit, not spy_edit
    assert data["pr_url"] == _PR_URL


async def test_resume_with_no_ledger_entries_still_refused(tmp_path):
    """#780 -- a bare leftover clone dir with nothing in the ledger is stale,
    not resumable: today's refusal still applies."""
    run_id = "stale-run"
    sandbox_root = tmp_path / "self_dev"
    (sandbox_root / run_id).mkdir(parents=True)  # leftover dir, no ledger entries

    plugin = _make(tmp_path, sandbox_root=sandbox_root)
    result = await plugin.call_tool("self_dev", {
        "change_description": "anything",
        "run_id": run_id,
    })
    assert result.is_error
    assert run_id in result.content


async def test_clear_ledger_makes_run_id_startable_again(tmp_path):
    """#780 -- clear(run_id) is the documented way to abandon a stuck/poisoned
    resume: once cleared, the ledger no longer resumes and the seams fire
    fresh on the next call with that run_id."""
    run_id = "poisoned-run"
    ledger = StepLedger(db_path=tmp_path / "ledger.db")
    # A fully "completed" run recorded end to end (simulates a poisoned edit
    # that would otherwise resume forever without ever re-running anything).
    ledger.record(run_id, "clone", {"name": "clone", "args": {}, "result": {}, "is_error": False})
    ledger.record(run_id, "edit", {
        "name": "edit", "args": {},
        "result": {"branch": "selfdev/poisoned", "committed": True},
        "is_error": False,
    })
    ledger.record(run_id, "test", {
        "name": "test", "args": {}, "result": {"passed": True, "summary": "ok"}, "is_error": False,
    })
    ledger.record(run_id, "pr", {
        "name": "pr", "args": {}, "result": {"pr_url": _PR_URL}, "is_error": False,
    })

    edit_calls = []
    plugin = _make(
        tmp_path,
        ledger=ledger,
        edit_fn=lambda d, desc: (edit_calls.append(1), {"branch": "fresh", "committed": True})[1],
    )

    # Before clearing: fully resumes, edit_fn never fires.
    result = await plugin.call_tool("self_dev", {"change_description": "x", "run_id": run_id})
    assert not result.is_error, result.content
    assert edit_calls == []

    # Clear -- the documented restart mechanism.
    removed = ledger.clear(run_id)
    assert removed == 4

    # After clearing: no ledger entries and no clone dir on disk for this
    # run_id (never physically cloned during the resumed run above) -> the
    # run is startable again, seams fire fresh.
    result2 = await plugin.call_tool("self_dev", {"change_description": "x", "run_id": run_id})
    assert not result2.is_error, result2.content
    assert edit_calls == [1]


async def test_test_fn_does_not_block_event_loop(tmp_path):
    """A slow/blocking test_fn (real code: subprocess.run to pytest) must not
    freeze Cerebral's event loop -- a concurrent coroutine must still get to
    run while self_dev's test step is in flight. Regression test for the
    2026-08-16 incident: a synchronous, unawaited test_fn call froze the whole
    process (no WS, no heartbeats) for 50+ minutes on a hung sandbox suite."""
    import threading
    import time

    test_thread_ident = {}

    def blocking_test_fn(d):
        test_thread_ident["id"] = threading.get_ident()
        time.sleep(0.3)  # stands in for a slow pytest run
        return True, "ok"

    ticked = []

    async def ticker():
        for _ in range(20):
            ticked.append(time.monotonic())
            await asyncio.sleep(0.02)

    plugin = _make(tmp_path, test_fn=blocking_test_fn)
    tick_task = asyncio.create_task(ticker())
    result = await plugin.call_tool("self_dev", {"change_description": "x"})
    await tick_task

    assert not result.is_error, result.content
    # The blocking call ran on a worker thread, not the event-loop thread.
    assert test_thread_ident["id"] != threading.get_ident()
    # The ticker kept making progress DURING the 0.3s "test run" -- proof the
    # loop was never blocked. A pre-fix direct call would have starved it and
    # left far fewer than ~15 ticks in that window.
    assert len(ticked) >= 15, f"event loop starved during test_fn: only {len(ticked)} ticks"


async def test_restart_arg_abandons_recorded_progress(tmp_path):
    """#780 follow-up -- restart:true lets an operator abandon a run stuck on a
    bad recorded phase without needing Python access to the ledger. It clears
    the recorded phases AND the stale clone dir, so the run starts fresh:
    clone_fn and edit_fn both run again."""
    run_id = "poisoned-run"
    sandbox_root = tmp_path / "self_dev"
    clone_dir = sandbox_root / run_id
    clone_dir.mkdir(parents=True)
    (clone_dir / "stale.txt").write_text("from the dead run\n", encoding="utf-8")

    ledger = StepLedger(db_path=tmp_path / "ledger.db")
    ledger.record(run_id, "clone", {
        "name": "clone", "args": {}, "result": {"clone_dir": str(clone_dir)}, "is_error": False,
    })
    ledger.record(run_id, "edit", {
        "name": "edit", "args": {},
        "result": {"branch": "selfdev/poisoned", "committed": True},
        "is_error": False,
    })

    clone_calls, edit_calls = [], []

    plugin = _make(
        tmp_path,
        sandbox_root=sandbox_root,
        ledger=ledger,
        clone_fn=lambda url, dest: (clone_calls.append(dest), dest.mkdir(parents=True, exist_ok=True))[1],
        edit_fn=lambda d, desc: (edit_calls.append(d), {"branch": "selfdev/fresh", "committed": True})[1],
    )
    result = await plugin.call_tool("self_dev", {
        "change_description": "start over",
        "run_id": run_id,
        "restart": True,
    })

    assert not result.is_error, result.content
    assert len(clone_calls) == 1, "restart must re-clone, not reuse the stale dir"
    assert len(edit_calls) == 1, "restart must re-run the edit it previously recorded"
    data = json.loads(result.content)
    assert data["branch"] == "selfdev/fresh"  # the NEW edit, not the recorded one
    assert not (clone_dir / "stale.txt").exists(), "stale clone dir must be removed"


async def test_restart_on_unknown_run_id_is_harmless(tmp_path):
    """restart:true on a run_id with nothing recorded is a plain fresh run."""
    plugin = _make(tmp_path)
    result = await plugin.call_tool("self_dev", {
        "change_description": "fresh",
        "run_id": "never-seen",
        "restart": True,
    })
    assert not result.is_error, result.content


async def test_restart_arg_is_declared_in_the_schema(tmp_path):
    """An operator must be able to discover it from the tool listing."""
    plugin = _make(tmp_path)
    tool = next(t for t in plugin.list_tools() if t.name == "self_dev")
    assert "restart" in tool.schema["properties"]
    assert "restart" not in tool.schema.get("required", [])


# ---------------------------------------------------------------------------
# self_dev_rollback (#813) -- chat-reachable companion to the tray button,
# on-demand revert to the last self-dev snapshot. Unlike self_dev_pr_merge,
# this IS meant to be LLM/planner-reachable (undo is the safe direction).
# ---------------------------------------------------------------------------

async def test_rollback_fires_the_injected_rollback_fn(tmp_path):
    calls = []

    async def fake_rollback():
        calls.append(True)

    plugin = _make(tmp_path, rollback_fn=fake_rollback)
    result = await plugin.call_tool("self_dev_rollback", {})

    assert not result.is_error, result.content
    assert calls == [True]
    data = json.loads(result.content)
    assert data["status"] == "rolling_back"


async def test_rollback_without_wired_fn_returns_clear_error(tmp_path):
    """No rollback_fn override and no module-level set_rollback_fn() call ->
    the _default_rollback_fn's NotImplementedError surfaces as a normal
    error result, not an unhandled exception."""
    plugin = _make(tmp_path)  # no rollback_fn override
    result = await plugin.call_tool("self_dev_rollback", {})

    assert result.is_error
    assert "rollback_fn" in result.content


async def test_rollback_records_system_event(tmp_path):
    recorded = []

    async def fake_record_turn(kind, content):
        recorded.append((kind, content))

    async def fake_rollback():
        pass

    plugin = _make(tmp_path, rollback_fn=fake_rollback, record_turn_fn=fake_record_turn)
    result = await plugin.call_tool("self_dev_rollback", {})

    assert not result.is_error, result.content
    assert len(recorded) == 1
    kind, content = recorded[0]
    assert kind == "system_event"
    assert content == {"kind": "self_dev_manual_rollback"}


async def test_rollback_records_an_activity_entry(tmp_path):
    """S26 (#879): record_activity_fn is a separate seam from record_turn_fn
    -- both must fire for the same event, one for the #810 pending-review
    card's active-thread turn, one for the Activity Log's dedicated thread."""
    recorded = []

    async def fake_record_activity(kind, content):
        recorded.append((kind, content))

    async def fake_rollback():
        pass

    plugin = _make(tmp_path, rollback_fn=fake_rollback, record_activity_fn=fake_record_activity)
    result = await plugin.call_tool("self_dev_rollback", {})

    assert not result.is_error, result.content
    assert len(recorded) == 1
    kind, content = recorded[0]
    assert kind == "activity"
    assert content["source"] == "self_dev"
    assert "rollback" in content["summary"].lower()


async def test_rollback_survives_record_activity_fn_failure(tmp_path):
    async def fake_rollback():
        pass

    async def boom(kind, content):
        raise RuntimeError("conversation store unavailable")

    plugin = _make(tmp_path, rollback_fn=fake_rollback, record_activity_fn=boom)
    result = await plugin.call_tool("self_dev_rollback", {})

    assert not result.is_error, result.content


async def test_rollback_survives_record_turn_fn_failure(tmp_path):
    async def fake_rollback():
        pass

    async def boom(kind, content):
        raise RuntimeError("conversation store unavailable")

    plugin = _make(tmp_path, rollback_fn=fake_rollback, record_turn_fn=boom)
    result = await plugin.call_tool("self_dev_rollback", {})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["status"] == "rolling_back"


async def test_rollback_trigger_exception_returns_error(tmp_path):
    async def bad_rollback():
        raise RuntimeError("tray unreachable")

    plugin = _make(tmp_path, rollback_fn=bad_rollback)
    result = await plugin.call_tool("self_dev_rollback", {})

    assert result.is_error
    assert "tray unreachable" in result.content


def test_rollback_tool_takes_no_required_args(tmp_path):
    plugin = _make(tmp_path)
    tool = next(t for t in plugin.list_tools() if t.name == "self_dev_rollback")
    assert tool.schema.get("required", []) == []
