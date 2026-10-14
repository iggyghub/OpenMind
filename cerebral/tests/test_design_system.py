"""Tests for cerebral/design_system.py -- the base design system scan."""
from __future__ import annotations

from pathlib import Path

from cerebral.design_system import (
    Violation, check_tab_strip_compliance, create_issue, driver_status,
    queued_rule_ids, scan, sync_driver_queue,
)


def _write(tmp_path: Path, name: str, html: str) -> Path:
    p = tmp_path / "windows"
    p.mkdir(parents=True, exist_ok=True)
    f = p / name
    f.write_text(html, encoding="utf-8")
    return f


def test_flags_a_tabs_bar_missing_tab_strip(tmp_path):
    _write(tmp_path, "main.html", '<div class="lib-tabs" id="lib-tabs">\n</div>\n')
    violations = check_tab_strip_compliance(tmp_path)
    assert len(violations) == 1
    assert violations[0].rule_id == "tab-strip"
    assert violations[0].line == 1
    assert "lib-tabs" in violations[0].snippet


def test_compliant_tab_bar_is_not_flagged(tmp_path):
    _write(
        tmp_path, "main.html",
        '<div class="lib-tabs tab-strip" id="lib-tabs" data-tab-selector=".lib-tab">\n</div>\n',
    )
    assert check_tab_strip_compliance(tmp_path) == []


def test_non_tab_elements_are_ignored(tmp_path):
    _write(tmp_path, "main.html", '<div class="sidebar" id="nav">\n<button class="btn">Go</button>\n</div>\n')
    assert check_tab_strip_compliance(tmp_path) == []


def test_multiple_files_and_multiple_violations_all_reported(tmp_path):
    _write(tmp_path, "a.html", '<div class="trd-tabs">\n</div>\n')
    _write(tmp_path, "b.html", '<div class="set-tabs">\n</div>\n<div class="help-tabs">\n</div>\n')
    violations = scan(tmp_path)
    assert len(violations) == 3


def test_exempt_id_is_not_flagged(tmp_path):
    _write(tmp_path, "main.html", '<div class="ws-tabs" id="ws-tabs" role="tablist" hidden></div>\n')
    assert check_tab_strip_compliance(tmp_path) == []


def test_node_modules_is_skipped(tmp_path):
    nm = tmp_path / "node_modules" / "some-pkg"
    nm.mkdir(parents=True)
    (nm / "demo.html").write_text('<div class="lib-tabs"></div>', encoding="utf-8")
    assert check_tab_strip_compliance(tmp_path) == []


def test_create_issue_parses_the_number_from_ghs_url_output(tmp_path, monkeypatch):
    import cerebral.design_system as ds

    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "https://github.com/iggyghub/OpenMind/issues/42\n"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return FakeResult()

    monkeypatch.setattr(ds.subprocess, "run", fake_run)
    issue_no = create_issue(tmp_path, "Tab-strip gaps", "3 tab bars are missing it")

    assert issue_no == 42
    assert captured["cwd"] == str(tmp_path)  # gh auto-detects repo from cwd, no --repo needed
    assert "--label" in captured["cmd"] and "ready-for-agent" in captured["cmd"]


def test_create_issue_returns_none_on_gh_failure(tmp_path, monkeypatch):
    import cerebral.design_system as ds

    class FakeResult:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(ds.subprocess, "run", lambda *a, **k: FakeResult())
    assert create_issue(tmp_path, "x", "y") is None


# ── Driver-file bookkeeping ─────────────────────────────────────────────────

_DRIVER_TEMPLATE = """# BASE-DESIGN-SYSTEM.md

## Status: ready

## Next slice -- start here

(none queued)

## Queue

## Landed PRs
"""


def _v(rule_id="tab-strip", n=1):
    return [Violation(rule_id, f"file{i}.html", 1, "<div>", "desc") for i in range(n)]


def test_driver_status_defaults_to_ready():
    assert driver_status(_DRIVER_TEMPLATE) == "ready"
    assert driver_status("## Status: blocked -- something broke\n") == "blocked"


def test_sync_files_one_issue_per_rule_and_appends_queue_entry():
    calls = []
    def create_issue(rule_id, violations):
        calls.append((rule_id, len(violations)))
        return 42

    new_text, filed = sync_driver_queue(_DRIVER_TEMPLATE, _v(n=3), create_issue)

    assert calls == [("tab-strip", 3)]  # one issue, grouping all 3 instances
    assert filed == [{"rule_id": "tab-strip", "issue": 42, "label": "S1"}]
    assert "- [ ] S1 -- #42 -- rule:tab-strip" in new_text
    assert "- **Active:** S1 -- #42" in new_text


def test_sync_skips_a_rule_already_queued():
    text = _DRIVER_TEMPLATE.replace(
        "## Queue\n", "## Queue\n- [ ] S1 -- #10 -- rule:tab-strip\n",
    )
    assert queued_rule_ids(text) == {"tab-strip"}

    def create_issue(rule_id, violations):
        raise AssertionError("should not file a duplicate issue")

    new_text, filed = sync_driver_queue(text, _v(), create_issue)
    assert filed == []
    assert new_text == text


def test_sync_does_nothing_when_driver_is_blocked():
    text = _DRIVER_TEMPLATE.replace("Status: ready", "Status: blocked -- needs a human")

    def create_issue(rule_id, violations):
        raise AssertionError("must not file while blocked")

    new_text, filed = sync_driver_queue(text, _v(), create_issue)
    assert filed == []
    assert new_text == text


def test_sync_does_not_overwrite_an_existing_active_entry():
    text = _DRIVER_TEMPLATE.replace(
        "(none queued)", "- **Active:** S1 -- #10\n\n## Queue\n- [ ] S1 -- #10 -- rule:other-rule\n",
    )
    new_text, filed = sync_driver_queue(text, _v(), lambda r, v: 99)
    assert filed[0]["label"] == "S2"  # continues numbering past the existing entry
    assert "- **Active:** S1 -- #10" in new_text  # untouched, S1 is still active


def test_sync_returns_original_text_when_issue_creation_fails():
    new_text, filed = sync_driver_queue(_DRIVER_TEMPLATE, _v(), lambda r, v: None)
    assert filed == []
    assert new_text == _DRIVER_TEMPLATE
