"""Issue #349 — the jobs LLM seams route with task_type="quality".

Patches ``cerebral.main._router`` with a task_type-recording stub; no live
model calls. The apply-driver test also fakes the open browser session.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import cerebral.main as main  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent.parent


class _RecordingRouter:
    def __init__(self, response: str):
        self.response = response
        self.task_types: list[str] = []

    async def complete(self, prompt, task_type="chat"):
        self.task_types.append(task_type)
        return self.response


async def test_extract_dossier_uses_quality(monkeypatch):
    router = _RecordingRouter('{"name": "Iggy"}')
    monkeypatch.setattr(main, "_router", router)
    result = await main._extract_dossier("resume text")
    assert result == {"name": "Iggy"}
    assert router.task_types == ["quality"]


async def test_score_posting_uses_quality(monkeypatch):
    router = _RecordingRouter("7.5")
    monkeypatch.setattr(main, "_router", router)
    score = await main._score_posting({"title": "AI Eng"}, {"skills_json": []})
    assert score == 7.5
    assert router.task_types == ["quality"]


async def test_apply_driver_uses_quality(monkeypatch):
    router = _RecordingRouter("[]")
    monkeypatch.setattr(main, "_router", router)

    class _FakeView:
        text = "First name:\nEmail:\n"

    class _FakeSession:
        async def read_page(self, url):
            return _FakeView()

        async def fill_fields(self, pairs):
            pass

        async def upload_file(self, selector, path):
            pass

    from plugins import browser_session as bsp
    monkeypatch.setattr(bsp, "get_open_session", lambda: _FakeSession())

    draft = await main._jobs_apply_driver("https://ats.example/apply", {}, "cv.pdf")
    assert draft["fields"] == []
    assert router.task_types == ["quality"]


async def test_create_ats_account_uses_quality(monkeypatch):
    router = _RecordingRouter("[]")
    monkeypatch.setattr(main, "_router", router)

    class _FakeView:
        text = "Email:\nPassword:\n"

    class _FakeSession:
        async def read_page(self, url):
            return _FakeView()

        async def fill_fields(self, pairs):
            pass

        async def click(self, selector):
            pass

    from plugins import browser_session as bsp
    monkeypatch.setattr(bsp, "get_open_session", lambda: _FakeSession())

    ok = await main._jobs_create_ats_account("https://ats.example/register", "a@b.c", "pw")
    assert ok is True
    assert router.task_types == ["quality"]


def test_startup_quality_seeding_present_in_main_source():
    """Guard the one-line default seeding at startup (issue #349)."""
    src = (_ROOT / "cerebral" / "main.py").read_text(encoding="utf-8")
    assert "seed_quality_default()" in src
