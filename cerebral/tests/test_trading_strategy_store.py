"""Tests for cerebral/trading/strategy_store.py's lineage table (S16/#861).

strategy_specs (the dispatcher's pointer at the currently-dispatched
version) already had its own coverage via test_trading_gauntlet.py /
test_plugin_scheduler.py -- these tests are specifically about
strategy_versions: the append-only history alongside it.
"""
import json

from cerebral.trading.strategy_store import StrategyStore, StrategySpec


def _store(tmp_path):
    return StrategyStore(db_path=tmp_path / "specs.db")


def test_save_is_append_only_not_overwriting(tmp_path):
    """The exact bug strategy_specs has always had (INSERT OR REPLACE
    silently discarding history) -- strategy_versions must not repeat it."""
    store = _store(tmp_path)
    store.save(StrategySpec("s1", "AAPL", "def strategy(data): return [0]"))
    store.save(StrategySpec("s1", "AAPL", "def strategy(data): return [1]"))

    rows = store._con.execute(
        "SELECT version, code FROM strategy_versions WHERE strategy_id = ? ORDER BY version",
        ("s1",),
    ).fetchall()

    assert len(rows) == 2
    assert rows[0]["version"] == 1
    assert rows[0]["code"] == "def strategy(data): return [0]"
    assert rows[1]["version"] == 2
    assert rows[1]["code"] == "def strategy(data): return [1]"

    # strategy_specs stays a pointer at the latest -- that part is
    # deliberately unchanged from before S16.
    assert store.get("s1").code == "def strategy(data): return [1]"


def test_get_current_version_returns_the_latest(tmp_path):
    store = _store(tmp_path)
    store.save(StrategySpec("s1", "AAPL", "v1 code"), hypothesis="first")
    store.save(StrategySpec("s1", "AAPL", "v2 code"), hypothesis="second")

    row = store.get_current_version("s1")

    assert row["version"] == 2
    assert row["code"] == "v2 code"
    assert row["hypothesis"] == "second"


def test_get_current_version_none_for_unknown_strategy(tmp_path):
    store = _store(tmp_path)
    assert store.get_current_version("never-saved") is None


def test_render_provenance_generated(tmp_path):
    store = _store(tmp_path)
    store.save(
        StrategySpec("s1", "AAPL", "code"),
        origin="generated", provenance_json={"source": "book: Alpha Quant ch 3"},
        hypothesis="Buy on low volume",
    )

    text = store.render_provenance(store.get_current_version("s1"))

    assert text == "book: Alpha Quant ch 3 (v1)"


def test_render_provenance_user_edited_composes_not_replaces(tmp_path):
    """The Honesty rule this table exists to serve: an edit's provenance
    must still name the original source, not just say "user edit"."""
    store = _store(tmp_path)
    store.save(
        StrategySpec("s1", "AAPL", "code"),
        origin="user_edited", provenance_json={"source": "book: Alpha Quant ch 3"},
    )

    text = store.render_provenance(store.get_current_version("s1"))

    assert "book: Alpha Quant ch 3" in text
    assert "modified by user" in text


def test_render_provenance_mixed_does_not_crash(tmp_path):
    """S18 (mix) hasn't landed yet -- this just proves the origin branch
    is reachable and doesn't raise (the real bug this test replaces: the
    original diff called row.get(...) on a sqlite3.Row, which has no
    .get() method and would have raised AttributeError on first use)."""
    store = _store(tmp_path)
    store.save(
        StrategySpec("s1", "AAPL", "code"),
        origin="mixed", components_json=["a@v1", "b@v2"],
    )

    text = store.render_provenance(store.get_current_version("s1"))

    assert "v1" in text  # doesn't crash; exact wording is S18's to finalize


def test_render_provenance_unknown_origin_falls_back_gracefully(tmp_path):
    """Defensive: render_provenance must not crash on a row shape it
    doesn't recognize -- a strategy_versions row always has SOME origin
    per the CHECK constraint, but the function's own fallback path should
    still be exercised, not just assumed correct."""
    store = _store(tmp_path)
    row = {"version": 1, "origin": "something_else", "provenance_json": None, "components_json": None}

    text = store.render_provenance(row)

    assert text == "strategy (v1)"
