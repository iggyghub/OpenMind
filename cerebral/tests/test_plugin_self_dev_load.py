"""
Self-dev load tests -- ADR-0015 S2 (Issue #555).

All side effects injected (pull_fn / restart_fn) -- no real git, no network,
no Cerebral.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.self_dev import SelfDevPlugin


class _FakeSandbox:
    pass


def _make(tmp_path: Path, **overrides) -> SelfDevPlugin:
    defaults: dict = {
        "sandbox": _FakeSandbox(),
        "clone_fn": lambda url, dest: dest.mkdir(parents=True, exist_ok=True),
        "edit_fn": lambda d, desc: {"branch": "selfdev/x", "committed": True},
        "test_fn": lambda d: (True, "1 passed"),
        "pr_fn": lambda d, br, desc, ok, out: "https://github.com/x/y/pull/1",
        "sandbox_root": tmp_path / "self_dev",
    }
    defaults.update(overrides)
    return SelfDevPlugin(**defaults)


# ---------------------------------------------------------------------------
# Tool is listed
# ---------------------------------------------------------------------------

def test_self_dev_load_in_tool_list(tmp_path):
    plugin = _make(tmp_path)
    names = [t.name for t in plugin.list_tools()]
    assert "self_dev_load" in names


# ---------------------------------------------------------------------------
# No-op when already up to date -- no restart
# ---------------------------------------------------------------------------

async def test_no_restart_when_already_up_to_date(tmp_path):
    restart_calls = []

    async def fake_restart():
        restart_calls.append(True)

    plugin = _make(
        tmp_path,
        pull_fn=lambda root: (False, "Already up to date."),
        restart_fn=fake_restart,
    )
    result = await plugin.call_tool("self_dev_load", {})
    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["status"] == "no_op"
    assert not restart_calls


# ---------------------------------------------------------------------------
# Restart triggered when new commits arrive
# ---------------------------------------------------------------------------

async def test_restart_triggered_on_new_commits(tmp_path):
    restart_calls = []

    async def fake_restart():
        restart_calls.append(True)

    plugin = _make(
        tmp_path,
        pull_fn=lambda root: (True, "Updating abc..def\n1 file changed, 2 insertions(+)"),
        restart_fn=fake_restart,
    )
    result = await plugin.call_tool("self_dev_load", {})
    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["status"] == "restarting"
    assert restart_calls == [True]


# ---------------------------------------------------------------------------
# pull_fn failure is surfaced as an error
# ---------------------------------------------------------------------------

async def test_pull_failure_is_error(tmp_path):
    def bad_pull(root):
        raise RuntimeError("network unreachable")

    plugin = _make(tmp_path, pull_fn=bad_pull)
    result = await plugin.call_tool("self_dev_load", {})
    assert result.is_error
    assert "git pull failed" in result.content


# ---------------------------------------------------------------------------
# Default restart_fn (unwired) returns error, not exception
# ---------------------------------------------------------------------------

async def test_default_restart_fn_returns_error(tmp_path):
    # No restart_fn -> _default_restart_fn -> NotImplementedError -> error result
    plugin = _make(
        tmp_path,
        pull_fn=lambda root: (True, "fast-forward"),
    )
    result = await plugin.call_tool("self_dev_load", {})
    assert result.is_error
    assert "restart_fn" in result.content or "requires" in result.content.lower()


# ---------------------------------------------------------------------------
# Optional pr_url echoed in both update and no-op results
# ---------------------------------------------------------------------------

async def test_pr_url_echoed_on_restart(tmp_path):
    async def fake_restart():
        pass

    plugin = _make(
        tmp_path,
        pull_fn=lambda root: (True, "fast-forward"),
        restart_fn=fake_restart,
    )
    result = await plugin.call_tool(
        "self_dev_load", {"pr_url": "https://github.com/x/y/pull/42"}
    )
    assert not result.is_error
    data = json.loads(result.content)
    assert data["pr_url"] == "https://github.com/x/y/pull/42"


async def test_pr_url_echoed_on_noop(tmp_path):
    plugin = _make(
        tmp_path,
        pull_fn=lambda root: (False, "Already up to date."),
    )
    result = await plugin.call_tool(
        "self_dev_load", {"pr_url": "https://github.com/x/y/pull/42"}
    )
    assert not result.is_error
    data = json.loads(result.content)
    assert data["pr_url"] == "https://github.com/x/y/pull/42"
