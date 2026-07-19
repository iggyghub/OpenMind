"""#427 — the answer-bank seams route through _get_memory() and only accept
prefixed, close hits (zero-guessed rule: no conversation memory may be
auto-filled into a job form)."""
from __future__ import annotations

import cerebral.main as main
from cerebral.memory.manager import Memory


def _mem(fact: str, distance: float) -> Memory:
    return Memory(id="m", fact=fact, profile_id=1, created_at="", distance=distance)


class _FakeMgr:
    def __init__(self, hits):
        self.hits = hits
        self.remembered: list[str] = []

    async def recall(self, query, n_results=5):
        return self.hits

    async def remember(self, fact):
        self.remembered.append(fact)
        return "id"


async def test_recall_returns_close_prefixed_answer(monkeypatch):
    mgr = _FakeMgr([_mem("Job application answer — Do you need sponsorship?: No", 0.1)])
    monkeypatch.setattr(main, "_get_memory", lambda: mgr)

    assert await main._jobs_recall(1, "Will you require visa sponsorship?") == "No"


async def test_recall_rejects_distant_and_unprefixed_hits(monkeypatch):
    mgr = _FakeMgr([
        _mem("Job application answer — Sponsorship?: No", 0.9),   # too far
        _mem("User likes hiking on weekends", 0.05),              # not answer bank
    ])
    monkeypatch.setattr(main, "_get_memory", lambda: mgr)

    assert await main._jobs_recall(1, "Sponsorship?") is None


async def test_recall_none_manager_returns_none(monkeypatch):
    monkeypatch.setattr(main, "_get_memory", lambda: None)
    assert await main._jobs_recall(1, "anything") is None


async def test_index_answer_remembers_with_prefix(monkeypatch):
    mgr = _FakeMgr([])
    monkeypatch.setattr(main, "_get_memory", lambda: mgr)

    await main._jobs_index_answer(1, "Sponsorship?", "No")

    assert mgr.remembered == ["Job application answer — Sponsorship?: No"]
