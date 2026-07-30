"""
Blast-radius gate tests -- ADR-0015 S4 (Issue #557).

Tests the is_guardrail_diff pure function and the SelfDevPlugin integration:
auto-merge on safe-zone + green, escalate on guardrail or test failure.

All side effects are injected -- no real git, gh, network, or Cerebral.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.self_dev import (
    GUARDRAIL_PATHS,
    SelfDevPlugin,
    is_guardrail_diff,
)

# ---------------------------------------------------------------------------
# Fake sandbox
# ---------------------------------------------------------------------------

class _FakeSandbox:
    pass


_PR_URL = "https://github.com/iggyghub/OpenMind/pull/999"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(tmp_path: Path, **overrides) -> SelfDevPlugin:
    """SelfDevPlugin with all seams injected as safe fakes."""
    defaults: dict = {
        "sandbox": _FakeSandbox(),
        "clone_fn": lambda url, dest: dest.mkdir(parents=True, exist_ok=True),
        "edit_fn": lambda d, desc: {"branch": "selfdev/abc123", "committed": True},
        "test_fn": lambda d: (True, "1 passed in 0.01s"),
        "pr_fn": lambda d, br, desc, ok, out: _PR_URL,
        "diff_fn": lambda url: ["plugins/weather.py"],  # safe zone by default
        "merge_fn": lambda url: None,
        "pull_fn": lambda root: (True, "fast-forward"),
        "restart_fn": _noop_restart,
        "sandbox_root": tmp_path / "self_dev",
    }
    defaults.update(overrides)
    return SelfDevPlugin(**defaults)


async def _noop_restart() -> None:
    pass


# ---------------------------------------------------------------------------
# GUARDRAIL_PATHS: defined in one place
# ---------------------------------------------------------------------------

def test_guardrail_paths_defined():
    assert isinstance(GUARDRAIL_PATHS, frozenset)
    assert len(GUARDRAIL_PATHS) > 0


def test_guardrail_paths_cover_required_entries():
    # ADR-0015 decision 5 mandates these specific entries.
    joined = " ".join(GUARDRAIL_PATHS)
    assert "cerebral/security/" in joined
    assert "cerebral/sandbox/" in joined
    assert "cerebral/main.py" in joined
    assert "plugins/self_dev.py" in joined
    # All tray/ escalates (prefix) -- covers the SD-3 boot-check.js and every
    # other JS the pytest gate can't validate.
    assert "tray/" in joined
    assert is_guardrail_diff(["tray/lib/boot-check.js"])[0]


# ---------------------------------------------------------------------------
# is_guardrail_diff -- pure function
# ---------------------------------------------------------------------------

def test_empty_diff_is_safe():
    ok, reason = is_guardrail_diff([])
    assert not ok
    assert reason == ""


def test_safe_zone_plugins_dir():
    ok, _ = is_guardrail_diff(["plugins/weather.py"])
    assert not ok


def test_safe_zone_skills_dir():
    ok, _ = is_guardrail_diff(["skills/my_skill/SKILL.md"])
    assert not ok


def test_safe_zone_docs_dir():
    ok, _ = is_guardrail_diff(["docs/adr/0001-example.md"])
    assert not ok


def test_guardrail_tray_prefix_escalates_all_js():
    # self_dev can now edit tray/*.js, but the pytest gate can't validate JS,
    # so every tray edit escalates to human review (whole-subtree prefix).
    hit, reason = is_guardrail_diff(["tray/lib/panel-spec.js"])
    assert hit
    assert "tray/" in reason


def test_guardrail_security_prefix():
    hit, reason = is_guardrail_diff(["cerebral/security/gate.py"])
    assert hit
    assert "security" in reason


def test_guardrail_security_prefix_subdir():
    hit, _ = is_guardrail_diff(["cerebral/security/acl.py"])
    assert hit


def test_guardrail_sandbox_prefix():
    hit, reason = is_guardrail_diff(["cerebral/sandbox/__init__.py"])
    assert hit
    assert "sandbox" in reason


def test_guardrail_main_py():
    hit, reason = is_guardrail_diff(["cerebral/main.py"])
    assert hit
    assert "main.py" in reason


def test_guardrail_self_dev_plugin():
    hit, reason = is_guardrail_diff(["plugins/self_dev.py"])
    assert hit
    assert "self_dev" in reason


def test_guardrail_boot_check_js():
    hit, reason = is_guardrail_diff(["tray/lib/boot-check.js"])
    assert hit
    assert "boot-check" in reason


def test_guardrail_credentials():
    hit, reason = is_guardrail_diff(["cerebral/db/credentials.py"])
    assert hit
    assert "credentials" in reason


def test_mixed_diff_guardrail_wins():
    # A safe file + one guardrail file -> escalate.
    hit, reason = is_guardrail_diff([
        "plugins/weather.py",
        "skills/ponytail/SKILL.md",
        "cerebral/security/gate.py",
    ])
    assert hit


def test_backslash_path_normalised():
    # Windows paths should be normalised to forward slashes.
    hit, _ = is_guardrail_diff(["cerebral\\security\\gate.py"])
    assert hit


# ---------------------------------------------------------------------------
# SelfDevPlugin integration: gate controls merge_decision
# ---------------------------------------------------------------------------

async def test_safe_green_auto_merges(tmp_path):
    merge_calls = []
    restart_calls = []

    async def fake_restart():
        restart_calls.append(True)

    plugin = _make(
        tmp_path,
        diff_fn=lambda url: ["plugins/weather.py"],  # safe
        merge_fn=lambda url: merge_calls.append(url),
        restart_fn=fake_restart,
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Add weather plugin"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["merge_decision"] == "auto_merge"
    assert merge_calls == [_PR_URL]
    # Restart must fire -- S3 boot self-check runs on restart in production.
    assert restart_calls == [True]


async def test_safe_fail_escalates(tmp_path):
    merge_calls = []

    plugin = _make(
        tmp_path,
        test_fn=lambda d: (False, "1 failed"),
        diff_fn=lambda url: ["plugins/weather.py"],  # safe
        merge_fn=lambda url: merge_calls.append(url),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Broken change"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["merge_decision"] == "escalate"
    assert "tests" in data["escalation_reason"].lower()
    assert not merge_calls


async def test_guardrail_green_escalates(tmp_path):
    merge_calls = []

    plugin = _make(
        tmp_path,
        diff_fn=lambda url: ["cerebral/security/gate.py"],  # guardrail
        merge_fn=lambda url: merge_calls.append(url),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Tighten ACL"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["merge_decision"] == "escalate"
    assert "security" in data["escalation_reason"]
    assert not merge_calls


async def test_guardrail_fail_escalates(tmp_path):
    merge_calls = []

    plugin = _make(
        tmp_path,
        test_fn=lambda d: (False, "1 failed"),
        diff_fn=lambda url: ["cerebral/security/gate.py"],  # guardrail
        merge_fn=lambda url: merge_calls.append(url),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Bad security change"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["merge_decision"] == "escalate"
    assert not merge_calls


async def test_diff_failure_escalates_fail_safe(tmp_path):
    """If diff_fn raises, escalate rather than default-auto-merge."""
    merge_calls = []

    def bad_diff(url):
        raise RuntimeError("gh: auth failed")

    plugin = _make(
        tmp_path,
        diff_fn=bad_diff,
        merge_fn=lambda url: merge_calls.append(url),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Anything"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["merge_decision"] == "escalate"
    assert "fail-safe" in data["escalation_reason"].lower()
    assert not merge_calls


async def test_merge_failure_returns_error(tmp_path):
    def bad_merge(url):
        raise RuntimeError("gh: not mergeable")

    plugin = _make(
        tmp_path,
        diff_fn=lambda url: ["plugins/weather.py"],
        merge_fn=bad_merge,
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Safe change"})
    assert result.is_error
    assert "Auto-merge failed" in result.content


async def test_auto_merge_load_status_in_result(tmp_path):
    """Auto-merge result carries load status from self_dev_load."""
    async def fake_restart():
        pass

    plugin = _make(
        tmp_path,
        diff_fn=lambda url: ["plugins/weather.py"],
        merge_fn=lambda url: None,
        pull_fn=lambda root: (True, "fast-forward"),
        restart_fn=fake_restart,
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Safe change"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["merge_decision"] == "auto_merge"
    assert "load" in data
    assert data["load"]["status"] == "restarting"


async def test_mixed_guardrail_and_safe_escalates(tmp_path):
    """A diff with both safe and guardrail files must escalate."""
    merge_calls = []

    plugin = _make(
        tmp_path,
        diff_fn=lambda url: ["plugins/weather.py", "cerebral/main.py"],
        merge_fn=lambda url: merge_calls.append(url),
    )
    result = await plugin.call_tool("self_dev", {"change_description": "Mixed change"})

    assert not result.is_error, result.content
    data = json.loads(result.content)
    assert data["merge_decision"] == "escalate"
    assert not merge_calls
