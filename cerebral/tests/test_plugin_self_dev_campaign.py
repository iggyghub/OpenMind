"""
self_dev_campaign tests -- ADR-0015 SD-5 (Issue #807).

Tests the driver-file parser functions (module-level, unit-testable with
tmp_path) and the self_dev_campaign orchestration (with _run stubbed).

ALL side effects are injected:
  - issue_fn: fake, never calls real gh
  - _run stubbed via a subclass override, never exercises clone/edit/test/pr
  - no real git, no gh, no network, no Cerebral
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plugins.self_dev import (
    SelfDevPlugin,
    _advance_driver,
    _set_driver_status,
    parse_driver_active,
    parse_driver_model,
    parse_driver_status,
)
from cerebral.mcp.orchestrator import ToolResult
from cerebral.llm.step_ledger import StepLedger


# ---------------------------------------------------------------------------
# Sample driver-file bodies
# ---------------------------------------------------------------------------

_DRIVER_BOOKS = """\
# BOOKS.md -- campaign driver

## Status: ready

## Next slice -- start here

- **Active:** S2 -- #798
- **Model:** sonnet

## Queue

- [x] S1 -- #797 -- book_ingest core
- [ ] S2 -- #798 -- book metadata
- [ ] S3 -- #799 -- concept extraction

## Landed PRs

- PR #806 -- S1 book_ingest core (squash-merged 2026-08-21)

## Notes

Per-slice model: sonnet unless the queue entry says otherwise.
"""

_DRIVER_DONE = _DRIVER_BOOKS.replace("## Status: ready", "## Status: done")

_DRIVER_BLOCKED = _DRIVER_BOOKS.replace(
    "## Status: ready", "## Status: blocked -- PR #900 escalated"
)

_DRIVER_BARE_STATUS = """\
Status: ready

## Next slice -- start here

- **Active:** SD-5 -- #807
- **Model:** sonnet

## Queue

- [ ] SD-5 -- #807 -- self_dev_campaign

## Landed PRs

"""

_DRIVER_MODEL_OVERRIDE = """\
## Status: ready

## Next slice -- start here

- **Active:** S1 -- #101
- **Model:** sonnet

## Queue

- [ ] S1 -- #101 -- first slice
- [ ] S2 -- #102 -- second slice  Model: opus

## Landed PRs

"""

_DRIVER_SINGLE = """\
## Status: ready

## Next slice -- start here

- **Active:** S1 -- #101
- **Model:** sonnet

## Queue

- [ ] S1 -- #101 -- only slice

## Landed PRs

"""


# ---------------------------------------------------------------------------
# parse_driver_status
# ---------------------------------------------------------------------------

def test_status_ready():
    assert parse_driver_status(_DRIVER_BOOKS) == "ready"


def test_status_done():
    assert parse_driver_status(_DRIVER_DONE) == "done"


def test_status_blocked():
    assert parse_driver_status(_DRIVER_BLOCKED).startswith("blocked")


def test_status_bare_no_hash():
    # `Status: ready` without markdown `##` prefix
    assert parse_driver_status(_DRIVER_BARE_STATUS) == "ready"


def test_status_default_when_absent():
    assert parse_driver_status("# No status line here\n\nSome text.") == "ready"


def test_status_case_insensitive():
    assert parse_driver_status("## STATUS: Done\n") == "done"


# ---------------------------------------------------------------------------
# parse_driver_active
# ---------------------------------------------------------------------------

def test_active_normal():
    label, issue = parse_driver_active(_DRIVER_BOOKS)
    assert label == "S2"
    assert issue == 798


def test_active_sd_label():
    label, issue = parse_driver_active(_DRIVER_BARE_STATUS)
    assert label == "SD-5"
    assert issue == 807


def test_active_none_when_absent():
    label, issue = parse_driver_active("## Queue\n- [x] S1 -- #1\n")
    assert label is None
    assert issue is None


def test_active_case_insensitive():
    text = "- **ACTIVE:** S3 -- #999\n"
    label, issue = parse_driver_active(text)
    assert label == "S3"
    assert issue == 999


# ---------------------------------------------------------------------------
# parse_driver_model
# ---------------------------------------------------------------------------

def test_model_bolded():
    # '- **Model:** sonnet' form
    assert parse_driver_model(_DRIVER_BOOKS) == "sonnet"


def test_model_bare():
    text = "Model: opus\n## Queue\n- [ ] S1 -- #1 -- desc\n"
    assert parse_driver_model(text) == "opus"


def test_model_default_when_absent():
    assert parse_driver_model("# Nothing\n") == "sonnet"


def test_model_queue_line_not_matched():
    # The queue entry line contains 'Model: opus' but it's a checkbox line --
    # must be skipped; the top-level Model (sonnet) must be returned.
    text = (
        "- **Model:** sonnet\n"
        "## Queue\n"
        "- [ ] S2 -- #102 -- desc  Model: opus\n"
    )
    assert parse_driver_model(text) == "sonnet"


def test_model_queue_line_only_returns_default():
    # Only a queue line has Model: -- no top-level Model field exists.
    text = "## Queue\n- [ ] S1 -- #1 -- desc  Model: haiku\n"
    assert parse_driver_model(text) == "sonnet"


def test_model_prose_line_not_matched():
    # 'Per-slice model: sonnet ...' appears mid-sentence at line start --
    # must NOT be picked up (starts with 'Per-', not a markdown prefix).
    text = "- **Model:** haiku\nPer-slice model: sonnet unless the queue says otherwise.\n"
    assert parse_driver_model(text) == "haiku"


# ---------------------------------------------------------------------------
# _set_driver_status
# ---------------------------------------------------------------------------

def test_set_status_updates_existing():
    result = _set_driver_status(_DRIVER_BOOKS, "blocked", "PR #9 escalated")
    assert "Status: blocked" in result
    assert "ready" not in result.split("Status:")[1].split("\n")[0]


def test_set_status_done_no_reason():
    result = _set_driver_status(_DRIVER_BOOKS, "done")
    assert "Status: done" in result


def test_set_status_prepends_when_absent():
    text = "# Title\n\nNo status here.\n"
    result = _set_driver_status(text, "blocked", "bad")
    assert result.startswith("Status: blocked")


# ---------------------------------------------------------------------------
# _advance_driver
# ---------------------------------------------------------------------------

def test_advance_ticks_active_queue_entry():
    result = _advance_driver(_DRIVER_BOOKS, "S2", 798, "https://github.com/x/y/pull/100")
    # S2 queue line must be ticked
    assert "- [x] S2 -- #798" in result


def test_advance_updates_active_line():
    result = _advance_driver(_DRIVER_BOOKS, "S2", 798, "https://github.com/x/y/pull/100")
    assert "Active:** S3 -- #799" in result or "Active: S3 -- #799" in result


def test_advance_appends_pr_to_landed():
    result = _advance_driver(_DRIVER_BOOKS, "S2", 798, "https://github.com/x/y/pull/100")
    assert "PR #100" in result
    assert "S2" in result


def test_advance_sets_done_when_last_slice():
    result = _advance_driver(_DRIVER_SINGLE, "S1", 101, "https://github.com/x/y/pull/50")
    assert parse_driver_status(result) == "done"


def test_advance_model_override_applied():
    result = _advance_driver(
        _DRIVER_MODEL_OVERRIDE, "S1", 101, "https://github.com/x/y/pull/200"
    )
    # After advancing past S1, the next entry S2 has 'Model: opus'
    assert parse_driver_model(result) == "opus"


def test_advance_preserves_rest_of_file():
    result = _advance_driver(_DRIVER_BOOKS, "S2", 798, "https://github.com/x/y/pull/100")
    # The Landed PRs section header must still be there
    assert "Landed PRs" in result
    # The already-ticked S1 must still be there
    assert "[x] S1 -- #797" in result


# ---------------------------------------------------------------------------
# self_dev_campaign orchestration (with _run stubbed)
# ---------------------------------------------------------------------------

class _FakeSandbox:
    pass


_PR_URL = "https://github.com/iggyghub/OpenMind/pull/100"


def _make_plugin(tmp_path: Path, run_results: list, issue_texts: dict | None = None) -> SelfDevPlugin:
    """Build a SelfDevPlugin whose _run() returns pre-configured ToolResults."""
    _run_iter = iter(run_results)

    class _CampaignPlugin(SelfDevPlugin):
        async def _run(self, args: dict) -> ToolResult:
            return next(_run_iter)

    if issue_texts is None:
        issue_texts = {}

    def _fake_issue_fn(n: int) -> str:
        return issue_texts.get(n, f"# Issue {n}\n\nBody for issue {n}")

    return _CampaignPlugin(
        sandbox=_FakeSandbox(),
        issue_fn=_fake_issue_fn,
        sandbox_root=tmp_path / "self_dev",
        ledger=StepLedger(db_path=tmp_path / "ledger.db"),
    )


def _auto_merge_result(pr_url: str = _PR_URL) -> ToolResult:
    return ToolResult(content=json.dumps({
        "run_id": "test-run",
        "clone_dir": "/tmp/clone",
        "branch": "selfdev/test",
        "test_passed": True,
        "test_summary": "5 passed",
        "pr_url": pr_url,
        "merge_decision": "auto_merge",
        "load": {"status": "restarting"},
    }))


def _escalate_result(pr_url: str = _PR_URL, reason: str = "guardrail hit") -> ToolResult:
    return ToolResult(content=json.dumps({
        "run_id": "test-run",
        "clone_dir": "/tmp/clone",
        "branch": "selfdev/test",
        "test_passed": True,
        "test_summary": "5 passed",
        "pr_url": pr_url,
        "merge_decision": "escalate",
        "escalation_reason": reason,
    }))


def _error_result(msg: str = "clone failed") -> ToolResult:
    return ToolResult(content=msg, is_error=True)


async def test_campaign_done_status_short_circuits(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_DONE, encoding="utf-8")
    plugin = _make_plugin(tmp_path, [])
    result = await plugin.call_tool("self_dev_campaign", {"driver_file": str(driver)})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["status"] == "done"
    assert data["slices_run"] == 0


async def test_campaign_blocked_status_short_circuits(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BLOCKED, encoding="utf-8")
    plugin = _make_plugin(tmp_path, [])
    result = await plugin.call_tool("self_dev_campaign", {"driver_file": str(driver)})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["status"].startswith("blocked")
    assert data["slices_run"] == 0


async def test_campaign_auto_merge_advances_and_loops(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BOOKS, encoding="utf-8")
    plugin = _make_plugin(tmp_path, [_auto_merge_result(), _auto_merge_result()])
    result = await plugin.call_tool(
        "self_dev_campaign", {"driver_file": str(driver), "max_slices": 2}
    )
    assert not result.is_error
    data = json.loads(result.content)
    assert data["slices_run"] == 2
    # Both slices were auto-merged
    for r in data["results"]:
        assert r["merge_decision"] == "auto_merge"
    # Driver should have advanced
    driver_text = driver.read_text(encoding="utf-8")
    assert "[x] S2 -- #798" in driver_text
    assert "[x] S3 -- #799" in driver_text


async def test_campaign_escalate_stops_and_sets_blocked(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BOOKS, encoding="utf-8")
    plugin = _make_plugin(tmp_path, [_escalate_result(reason="security guardrail")])
    result = await plugin.call_tool("self_dev_campaign", {"driver_file": str(driver)})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["status"] == "blocked"
    assert data["slices_run"] == 1
    # Driver must show Status: blocked
    driver_text = driver.read_text(encoding="utf-8")
    assert parse_driver_status(driver_text) == "blocked"


async def test_campaign_error_stops_and_sets_blocked(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BOOKS, encoding="utf-8")
    plugin = _make_plugin(tmp_path, [_error_result("sandbox unavailable")])
    result = await plugin.call_tool("self_dev_campaign", {"driver_file": str(driver)})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["status"] == "blocked"
    assert data["results"][0]["is_error"] is True
    driver_text = driver.read_text(encoding="utf-8")
    assert parse_driver_status(driver_text) == "blocked"


async def test_campaign_max_slices_respected(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BOOKS, encoding="utf-8")
    # Provide enough results to hit the cap
    plugin = _make_plugin(tmp_path, [_auto_merge_result()] * 5)
    result = await plugin.call_tool(
        "self_dev_campaign", {"driver_file": str(driver), "max_slices": 1}
    )
    assert not result.is_error
    data = json.loads(result.content)
    assert data["slices_run"] == 1
    assert data["status"] == "max_slices_reached"


async def test_campaign_issue_fn_called_with_correct_number(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BOOKS, encoding="utf-8")
    called_with = []

    class _IssuePlugin(SelfDevPlugin):
        async def _run(self, args: dict) -> ToolResult:
            return _auto_merge_result()

    def _fake_issue(n: int) -> str:
        called_with.append(n)
        return f"# Issue {n}\n\nBody"

    plugin = _IssuePlugin(
        sandbox=_FakeSandbox(),
        issue_fn=_fake_issue,
        sandbox_root=tmp_path / "self_dev",
        ledger=StepLedger(db_path=tmp_path / "ledger.db"),
    )
    await plugin.call_tool("self_dev_campaign", {"driver_file": str(driver), "max_slices": 1})
    # S2 (#798) is the active slice in _DRIVER_BOOKS
    assert called_with == [798]


async def test_campaign_issue_fn_failure_sets_blocked(tmp_path):
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BOOKS, encoding="utf-8")

    class _BadIssuePlugin(SelfDevPlugin):
        async def _run(self, args: dict) -> ToolResult:
            return _auto_merge_result()

    def _bad_issue(n: int) -> str:
        raise RuntimeError("gh: not authenticated")

    plugin = _BadIssuePlugin(
        sandbox=_FakeSandbox(),
        issue_fn=_bad_issue,
        sandbox_root=tmp_path / "self_dev",
        ledger=StepLedger(db_path=tmp_path / "ledger.db"),
    )
    result = await plugin.call_tool("self_dev_campaign", {"driver_file": str(driver)})
    assert not result.is_error
    data = json.loads(result.content)
    assert data["status"] == "blocked"
    driver_text = driver.read_text(encoding="utf-8")
    assert parse_driver_status(driver_text) == "blocked"


async def test_campaign_driver_not_found(tmp_path):
    plugin = _make_plugin(tmp_path, [])
    result = await plugin.call_tool(
        "self_dev_campaign", {"driver_file": str(tmp_path / "MISSING.md")}
    )
    assert result.is_error
    assert "not found" in result.content.lower()


async def test_campaign_missing_driver_file_arg(tmp_path):
    plugin = _make_plugin(tmp_path, [])
    result = await plugin.call_tool("self_dev_campaign", {})
    assert result.is_error
    assert "driver_file" in result.content


async def test_campaign_run_id_uses_slug(tmp_path):
    """run_id must be 'campaign-<slug>-s<n>' where slug is the driver stem."""
    driver = tmp_path / "BOOKS.md"
    driver.write_text(_DRIVER_BOOKS, encoding="utf-8")
    captured_args: list[dict] = []

    class _SpyPlugin(SelfDevPlugin):
        async def _run(self, args: dict) -> ToolResult:
            captured_args.append(dict(args))
            return _auto_merge_result()

    plugin = _SpyPlugin(
        sandbox=_FakeSandbox(),
        issue_fn=lambda n: f"# Issue {n}\n\nBody",
        sandbox_root=tmp_path / "self_dev",
        ledger=StepLedger(db_path=tmp_path / "ledger.db"),
    )
    await plugin.call_tool("self_dev_campaign", {"driver_file": str(driver), "max_slices": 1})
    assert captured_args[0]["run_id"] == "campaign-books-s1"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def test_self_dev_campaign_in_tool_list(tmp_path):
    plugin = SelfDevPlugin(
        sandbox=_FakeSandbox(),
        sandbox_root=tmp_path / "self_dev",
    )
    names = [t.name for t in plugin.list_tools()]
    assert "self_dev_campaign" in names


def test_self_dev_campaign_schema_has_required_driver_file(tmp_path):
    plugin = SelfDevPlugin(
        sandbox=_FakeSandbox(),
        sandbox_root=tmp_path / "self_dev",
    )
    tool = next(t for t in plugin.list_tools() if t.name == "self_dev_campaign")
    assert "driver_file" in tool.schema["properties"]
    assert "driver_file" in tool.schema["required"]


def test_required_capabilities_include_fs_read():
    from plugins.self_dev import REQUIRED_CAPABILITIES
    assert "fs_read" in REQUIRED_CAPABILITIES
