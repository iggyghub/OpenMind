"""Context pruner -- H1-S2 tool-result pruning tests.

All hermetic: real SpillStore on a tmp_path DB, no model / net.
Mirrors test_spill_store.py style.
"""

import pytest

from cerebral.llm.context_pruner import prune_prior_steps, would_prune, _assembled_text
from cerebral.llm.spill_store import SpillStore


def _store(tmp_path, threshold=50) -> SpillStore:
    return SpillStore(db_path=tmp_path / "spill.db", threshold=threshold)


def _step(name: str, result: str, is_error: bool = False) -> dict:
    return {"name": name, "result": result, "is_error": is_error}


def test_prunes_biggest_first_until_under_budget(tmp_path):
    store = _store(tmp_path, threshold=50)
    a = "a" * 4000
    b = "b" * 40
    c = "c" * 2000
    steps = [_step("a", a), _step("b", b), _step("c", c)]
    context_window = 1000

    steps, pruned = prune_prior_steps(steps, store, context_window)

    assert pruned >= 1
    # The biggest eligible result ('a') was pruned first
    assert steps[0]["result"].startswith("[Tool output too large")
    # Round-trip the locator
    locator = steps[0]["result"].split("stored as ", 1)[1].split(".", 1)[0].strip()
    assert store.retrieve(locator) == a
    # Small 'b' untouched
    assert steps[1]["result"] == b


def test_under_budget_is_a_noop(tmp_path):
    store = _store(tmp_path, threshold=50)
    steps = [_step("x", "a" * 100), _step("y", "b" * 100)]
    context_window = 100000

    steps, pruned = prune_prior_steps(steps, store, context_window)

    assert pruned == 0
    assert _assembled_text(steps) == "a" * 100 + "b" * 100


def test_stops_when_nothing_prunable(tmp_path):
    store = _store(tmp_path, threshold=1000)
    steps = [_step("x", "a" * 40), _step("y", "b" * 40)]
    context_window = 1  # tiny window forces loop, but nothing prunable

    steps, pruned = prune_prior_steps(steps, store, context_window)

    assert pruned == 0
    assert _assembled_text(steps) == "a" * 40 + "b" * 40


def test_error_results_are_never_spilled(tmp_path):
    store = _store(tmp_path, threshold=50)
    err_result = "E" * 4000
    ok_result = "O" * 4000
    steps = [_step("err", err_result, is_error=True), _step("ok", ok_result)]
    context_window = 500

    steps, pruned = prune_prior_steps(steps, store, context_window)

    assert pruned >= 1
    # Error stays raw
    assert steps[0]["result"] == err_result
    # Success is spilled
    assert steps[1]["result"].startswith("[Tool output too large")
    locator = steps[1]["result"].split("stored as ", 1)[1].split(".", 1)[0].strip()
    assert store.retrieve(locator) == ok_result


def test_would_prune_matches_threshold(tmp_path):
    store = _store(tmp_path, threshold=50)
    big_steps = [_step("big", "x" * 5000)]
    small_steps = [_step("small", "x" * 10)]
    context_window = 1000

    assert would_prune(big_steps, context_window) is True
    assert would_prune(small_steps, context_window) is False
