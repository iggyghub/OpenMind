"""Tests for cerebral/self_dev_io.py -- the git/gh/pytest shell-out helpers
that live outside plugins/ (so plugins/self_dev.py stays scan-clean) plus the
JSON extractor that parses untrusted model output in the edit step."""
import subprocess

from cerebral import self_dev_io as io


# ── extract_json_value: parse model replies defensively ──────────────────────

def test_extract_plain_object():
    assert io.extract_json_value('{"a.py": "x"}', "{") == {"a.py": "x"}


def test_extract_object_in_fences_and_prose():
    reply = 'Sure! Here you go:\n```json\n{"cerebral/x.py": "body"}\n```\nDone.'
    assert io.extract_json_value(reply, "{") == {"cerebral/x.py": "body"}


def test_extract_array():
    assert io.extract_json_value("files: [\"a.py\", \"b.py\"] ok", "[") == ["a.py", "b.py"]


def test_extract_malformed_returns_none():
    assert io.extract_json_value("{not valid json,,,}", "{") is None


def test_extract_missing_returns_none():
    assert io.extract_json_value("no json here", "{") is None


# ── create_branch_and_commit: real git verbs, no shell ───────────────────────

def test_create_branch_and_commit_true_on_success(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        class R:  # commit succeeds
            returncode = 0
            stdout = stderr = ""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert io.create_branch_and_commit("/clone", "selfdev/abc", "msg") is True
    assert any(c[3:5] == ["checkout", "-b"] for c in calls), "must branch"
    assert any("add" in c for c in calls), "must stage"
    assert any("commit" in c for c in calls), "must commit"


def test_create_branch_and_commit_false_when_nothing_to_commit(monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            # git commit returns non-zero when the tree is clean
            returncode = 1 if "commit" in cmd else 0
            stdout = stderr = ""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert io.create_branch_and_commit("/clone", "selfdev/abc", "msg") is False


def test_pr_fn_caps_title_at_256_and_uses_first_line(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        class R:
            returncode = 0
            stdout = "https://example/pr/1"
            stderr = ""
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    long_desc = "First line " + "x" * 400 + "\nsecond line dropped from title"
    io.pr_fn("/clone", "selfdev/abc", long_desc, True, "ok")
    gh = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
    title = gh[gh.index("--title") + 1]
    body = gh[gh.index("--body") + 1]
    assert len(title) <= 256
    assert title == ("First line " + "x" * 400)[:256]  # first line, truncated
    assert long_desc in body  # full description preserved in the body


# ── apply_search_replace: parse + apply model edit blocks ────────────────────

def _write(tmp_path, rel, body):
    fp = tmp_path / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(body, encoding="utf-8")
    return fp


def test_apply_search_replace_adds_code(tmp_path):
    fp = _write(tmp_path, "cerebral/settings.py",
                "class S:\n    def all(self):\n        return {}\n")
    reply = (
        "<<<FILE: cerebral/settings.py>>>\n<<<SEARCH>>>\n"
        "    def all(self):\n        return {}\n"
        "<<<REPLACE>>>\n"
        "    def all(self):\n        return {}\n\n"
        "    def reset(self):\n        pass\n<<<END>>>"
    )
    applied = io.apply_search_replace(tmp_path, reply)
    assert applied == ["cerebral/settings.py"]
    assert "def reset(self):" in fp.read_text(encoding="utf-8")


def test_apply_search_replace_miss_is_skipped(tmp_path):
    fp = _write(tmp_path, "a.py", "x = 1\n")
    before = fp.read_text(encoding="utf-8")
    reply = ("<<<FILE: a.py>>>\n<<<SEARCH>>>\nNOT PRESENT\n"
             "<<<REPLACE>>>\ny = 2\n<<<END>>>")
    assert io.apply_search_replace(tmp_path, reply) == []
    assert fp.read_text(encoding="utf-8") == before  # untouched


def test_apply_search_replace_path_escape_guard(tmp_path):
    # A block targeting a path outside the clone is ignored.
    reply = ("<<<FILE: ../evil.py>>>\n<<<SEARCH>>>\na\n<<<REPLACE>>>\nb\n<<<END>>>")
    assert io.apply_search_replace(tmp_path, reply) == []


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
