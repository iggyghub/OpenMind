"""Tests for the self_dev edit-prompt budget (issue #758).

``_self_dev_edit`` (cerebral/main.py) used to inline every wanted file whole
with no size cap -- reliable on small files, structurally incapable on large
ones (a 51KB file alone produced a 31.7k-token prompt that timed out every
model at 300s). These tests are hermetic: no real model, network, git, or
Cerebral -- a fake router stands in for cerebral.llm.router.ModelRouter and
cerebral.self_dev_io's git/gh calls are monkeypatched out, mirroring
test_self_dev_io.py / test_main_dispatcher.py's fake-seam style.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cerebral.main as main_mod
import cerebral.self_dev_io as sdio_mod


class _FakeRouter:
    """Stands in for ModelRouter: records every prompt sent to complete(),
    resolves to a fixed model id/context window, and returns queued replies
    (plan call first, edit call second) -- same shape _FakeRouter in
    test_main_dispatcher.py uses for context_window_for + complete."""

    def __init__(self, context_window: int, responses: list[str], model_id: str = "fake/model"):
        self.context_window = context_window
        self.model_id = model_id
        self.calls: list[tuple[str, str]] = []  # (prompt, task_type)
        self._responses = list(responses)

    def get_task_model(self, task_type: str) -> str:
        return self.model_id

    def context_window_for(self, model_id: str) -> int:
        return self.context_window

    async def complete(self, prompt: str, task_type: str = "chat") -> str:
        self.calls.append((prompt, task_type))
        if self._responses:
            return self._responses.pop(0)
        return "[]"


def _patch_sdio(monkeypatch, written=None, committed=True):
    """Stub the git/gh shell-outs _self_dev_edit calls via self_dev_io, so
    apply_search_replace/create_branch_and_commit never touch real git."""
    calls = {"apply": [], "commit": []}

    def fake_apply(clone_dir, text, allowed=None):
        calls["apply"].append(text)
        return written if written is not None else []

    def fake_commit(clone_dir, branch, message):
        calls["commit"].append((branch, message))
        return committed

    monkeypatch.setattr(sdio_mod, "apply_search_replace", fake_apply)
    monkeypatch.setattr(sdio_mod, "create_branch_and_commit", fake_commit)
    return calls


# ── small file set: unchanged behaviour ──────────────────────────────────────

async def test_small_file_set_inlined_whole_no_regression(tmp_path, monkeypatch):
    (tmp_path / "cerebral").mkdir()
    fp = tmp_path / "cerebral" / "a.py"
    fp.write_text("print('hi')\n", encoding="utf-8")

    router = _FakeRouter(
        context_window=8192,
        responses=['["cerebral/a.py"]', "<<<FILE: cerebral/a.py>>>\n<<<SEARCH>>>\nx\n<<<REPLACE>>>\ny\n<<<END>>>"],
    )
    monkeypatch.setattr(main_mod, "_router", router)
    sdio_calls = _patch_sdio(monkeypatch, written=["cerebral/a.py"], committed=True)

    result = await main_mod._self_dev_edit(str(tmp_path), "add a bye print")

    assert result["committed"] is True
    assert result["written"] == ["cerebral/a.py"]
    assert len(router.calls) == 2  # plan, then edit
    edit_prompt = router.calls[1][0]
    # Whole file present verbatim -- exactly today's behaviour for a file
    # that comfortably fits the budget.
    assert "=== FILE: cerebral/a.py ===\nprint('hi')\n" in edit_prompt
    assert "TRUNCATED to fit prompt budget" not in edit_prompt
    assert len(sdio_calls["commit"]) == 1


# ── oversized file: excerpted, not dropped ───────────────────────────────────

async def test_oversized_file_excerpted_under_budget(tmp_path, monkeypatch):
    (tmp_path / "cerebral").mkdir()
    fp = tmp_path / "cerebral" / "big.py"
    fp.write_text("x = 1\n" * 5000, encoding="utf-8")  # ~30KB, well over any per-file cap

    router = _FakeRouter(
        context_window=2000,  # budget = 1400 tokens after reserve
        responses=['["cerebral/big.py"]', "no edits"],
    )
    monkeypatch.setattr(main_mod, "_router", router)
    _patch_sdio(monkeypatch, written=[], committed=False)

    await main_mod._self_dev_edit(str(tmp_path), "trim big.py")

    edit_prompt = router.calls[1][0]
    assert "TRUNCATED to fit prompt budget" in edit_prompt
    assert "END TRUNCATED EXCERPT" in edit_prompt
    from cerebral.llm.context_budget import estimate_tokens
    prompt_budget = int(router.context_window * (1 - main_mod._SELF_DEV_RESPONSE_RESERVE))
    assert estimate_tokens(edit_prompt) <= prompt_budget


# ── impossible set: fail fast, model never called for the edit ──────────────

async def test_impossible_set_fails_fast_without_calling_edit_model(tmp_path, monkeypatch):
    (tmp_path / "cerebral").mkdir()
    wanted_paths = []
    for i in range(_SELF_DEV_MAX_FILES := main_mod._SELF_DEV_MAX_FILES):
        rel = f"cerebral/f{i}.py"
        (tmp_path / rel).write_text("y = 1\n" * 100, encoding="utf-8")  # ~600 chars, >100 tokens
        wanted_paths.append(rel)

    router = _FakeRouter(
        context_window=1000,  # budget = 700 tokens; budget_for_files ~ 470
        responses=[__import__("json").dumps(wanted_paths), "unused"],
    )
    monkeypatch.setattr(main_mod, "_router", router)
    _patch_sdio(monkeypatch)

    with pytest.raises(RuntimeError, match="cannot fit"):
        await main_mod._self_dev_edit(str(tmp_path), "rewrite everything")

    # Only the plan call happened -- the (expensive, timeout-prone) edit call
    # was never made.
    assert len(router.calls) == 1
    assert router.calls[0][1] == "self_dev"


async def test_impossible_set_error_names_offending_files(tmp_path, monkeypatch):
    rel = "cerebral/only.py"
    (tmp_path / "cerebral").mkdir()
    (tmp_path / rel).write_text("y = 1\n" * 200, encoding="utf-8")

    router = _FakeRouter(
        context_window=50,  # budget = 35 tokens, far below even fixed instructions
        responses=[f'["{rel}"]', "unused"],
    )
    monkeypatch.setattr(main_mod, "_router", router)
    _patch_sdio(monkeypatch)

    with pytest.raises(RuntimeError) as exc_info:
        await main_mod._self_dev_edit(str(tmp_path), "rewrite one file")

    assert "Narrow the change description" in str(exc_info.value)
    assert len(router.calls) == 1


# ── per-file cap: one huge file cannot crowd out the others ─────────────────

async def test_per_file_cap_leaves_room_for_other_files(tmp_path, monkeypatch):
    (tmp_path / "cerebral").mkdir()
    (tmp_path / "cerebral" / "huge.py").write_text("z = 1\n" * 10000, encoding="utf-8")  # ~60KB
    small_body = "def small():\n    return 42\n"
    (tmp_path / "cerebral" / "small.py").write_text(small_body, encoding="utf-8")

    router = _FakeRouter(
        context_window=4000,  # budget = 2800 tokens; per-file cap = 1120 tokens
        responses=['["cerebral/huge.py", "cerebral/small.py"]', "no edits"],
    )
    monkeypatch.setattr(main_mod, "_router", router)
    _patch_sdio(monkeypatch, written=[], committed=False)

    await main_mod._self_dev_edit(str(tmp_path), "touch both files")

    edit_prompt = router.calls[1][0]
    assert "=== FILE: cerebral/huge.py (TRUNCATED to fit prompt budget) ===" in edit_prompt
    # small.py must survive in full -- the per-file cap on huge.py is what
    # leaves it room, instead of huge.py alone exhausting the whole budget.
    assert f"=== FILE: cerebral/small.py ===\n{small_body}" in edit_prompt

    from cerebral.llm.context_budget import estimate_tokens
    prompt_budget = int(router.context_window * (1 - main_mod._SELF_DEV_RESPONSE_RESERVE))
    assert estimate_tokens(edit_prompt) <= prompt_budget


# ── candidate list reaches prose surfaces (skills / docs / scripts) ──────────

async def test_plan_prompt_offers_skills_docs_and_scripts(tmp_path, monkeypatch):
    """The planner must SEE non-Python surfaces, not just cerebral/plugins/tray.

    Before this, .claude/skills, docs and scripts were in the clone but never
    listed, so a slice like "write .claude/skills/<x>/SKILL.md" could only be
    driven by naming the exact path in the change description and forcing a
    NEWFILE block (how SK-4 / #363 had to be built).
    """
    (tmp_path / "cerebral").mkdir()
    (tmp_path / "cerebral" / "a.py").write_text("x = 1\n", encoding="utf-8")
    skill = tmp_path / ".claude" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("# note\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.ps1").write_text("Write-Host hi\n", encoding="utf-8")

    router = _FakeRouter(
        context_window=8192,
        responses=['[".claude/skills/demo/SKILL.md"]',
                   "<<<NEWFILE: .claude/skills/demo2/SKILL.md>>>\nbody\n<<<END>>>"],
    )
    monkeypatch.setattr(main_mod, "_router", router)
    _patch_sdio(monkeypatch, written=[".claude/skills/demo2/SKILL.md"], committed=True)

    await main_mod._self_dev_edit(str(tmp_path), "write a skill")

    plan_prompt = router.calls[0][0]
    assert ".claude/skills/demo/SKILL.md" in plan_prompt
    assert "docs/note.md" in plan_prompt
    assert "scripts/run.ps1" in plan_prompt
    # Python surfaces still offered -- this widens, it does not replace.
    assert "cerebral/a.py" in plan_prompt


# ── S28: _self_dev_review (pre-merge review gate) ────────────────────────────

async def test_review_parses_ok_verdict(monkeypatch):
    router = _FakeRouter(context_window=8192, responses=['{"ok": true, "feedback": ""}'])
    monkeypatch.setattr(main_mod, "_router", router)

    ok, feedback = await main_mod._self_dev_review("+added line\n", "add a line")
    assert ok is True
    assert feedback == ""
    assert router.calls[0][1] == "self_dev"


async def test_review_parses_flagged_verdict(monkeypatch):
    router = _FakeRouter(
        context_window=8192,
        responses=['{"ok": false, "feedback": "renamed a public function with no callers updated"}'],
    )
    monkeypatch.setattr(main_mod, "_router", router)

    ok, feedback = await main_mod._self_dev_review("-def foo():\n+def bar():\n", "rename foo")
    assert ok is False
    assert "renamed a public function" in feedback


async def test_review_fails_open_on_unparseable_reply(monkeypatch):
    router = _FakeRouter(context_window=8192, responses=["I cannot review this."])
    monkeypatch.setattr(main_mod, "_router", router)

    ok, feedback = await main_mod._self_dev_review("+x = 1\n", "add x")
    assert ok is True


async def test_review_skips_model_call_on_empty_diff(monkeypatch):
    router = _FakeRouter(context_window=8192, responses=[])
    monkeypatch.setattr(main_mod, "_router", router)

    ok, feedback = await main_mod._self_dev_review("", "no-op")
    assert ok is True
    assert router.calls == []


async def test_candidate_list_skips_node_modules(tmp_path, monkeypatch):
    """A vendored tree would blow the planner prompt; keep it excluded."""
    (tmp_path / "cerebral").mkdir()
    (tmp_path / "cerebral" / "a.py").write_text("x = 1\n", encoding="utf-8")
    vendored = tmp_path / "docs" / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "README.md").write_text("vendored\n", encoding="utf-8")

    router = _FakeRouter(
        context_window=8192,
        responses=['["cerebral/a.py"]',
                   "<<<FILE: cerebral/a.py>>>\n<<<SEARCH>>>\nx\n<<<REPLACE>>>\ny\n<<<END>>>"],
    )
    monkeypatch.setattr(main_mod, "_router", router)
    _patch_sdio(monkeypatch, written=["cerebral/a.py"], committed=True)

    await main_mod._self_dev_edit(str(tmp_path), "touch a")

    assert "node_modules" not in router.calls[0][0]
