"""
Self-dev plugin tests -- ADR-0015 S1 (Issue #554).

All side effects are injected (clone_fn / edit_fn / test_fn / pr_fn) so
the suite runs hermetically: no real git, no gh, no network, no Cerebral.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.self_dev import SelfDevPlugin, PLUGIN_NAME, REQUIRED_CAPABILITIES


# ---------------------------------------------------------------------------
# Fake sandbox -- signals availability without real Windows Job Objects.
# ---------------------------------------------------------------------------

class _FakeSandbox:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PR_URL = "https://github.com/iggyghub/OpenMind/pull/999"


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
        "sandbox_root": tmp_path / "self_dev",
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
    assert len(tools) == 1
    t = tools[0]
    assert t.name == "self_dev"
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
# Failing tests still open a PR (blast-radius gate owns merge decisions).
# ---------------------------------------------------------------------------

async def test_test_failure_still_opens_pr(tmp_path):
    pr_calls = []

    def pr_fn(d, br, desc, ok, out):
        pr_calls.append({"ok": ok, "out": out})
        return _PR_URL

    plugin = _make(
        tmp_path,
        test_fn=lambda d: (False, "1 failed, 0 passed"),
        pr_fn=pr_fn,
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Broken change"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["test_passed"] is False
    assert data["pr_url"] == _PR_URL
    assert len(pr_calls) == 1
    assert pr_calls[0]["ok"] is False


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
