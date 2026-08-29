"""Tests for cerebral/trading/strategy_store.py's lineage table (S16/#861).

strategy_specs (the dispatcher's pointer at the currently-dispatched
version) already had its own coverage via test_trading_gauntlet.py /
test_plugin_scheduler.py -- these tests are specifically about
strategy_versions: the append-only history alongside it.
"""
import json

from cerebral.trading.strategy_store import (
    StrategyStore, StrategySpec, mint_expansion_strategy_id, strip_expansion_suffix,
)


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


def test_render_provenance_discovered(tmp_path):
    """S25 (#878): the origin the CHECK-constraint migration exists to
    unlock. render_provenance must name it explicitly, not fall through to
    the generic default -- that would erase the exact fact origin=
    'discovered' exists to record (the Honesty rule again)."""
    store = _store(tmp_path)
    store.save(StrategySpec("s1", "AAPL", "code"), origin="discovered")

    text = store.render_provenance(store.get_current_version("s1"))

    assert text == "discovered (v1)"


def test_save_rejects_an_invalid_origin(tmp_path):
    """S25: origin is validated in Python now, not by a SQL CHECK
    constraint (see the migration tests below for why the constraint was
    dropped) -- save() must still refuse a bad value, just earlier and
    with a readable message instead of an opaque IntegrityError."""
    store = _store(tmp_path)

    try:
        store.save(StrategySpec("s1", "AAPL", "code"), origin="not_a_real_origin")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "not_a_real_origin" in str(exc)


def test_render_provenance_unknown_origin_falls_back_gracefully(tmp_path):
    """Defensive: render_provenance must not crash on a row shape it
    doesn't recognize. Since S25 dropped the SQL CHECK constraint (origin
    is validated in Python at save() time instead -- see
    test_save_rejects_an_invalid_origin), a row's origin is no longer
    guaranteed correct by the schema alone, so this fallback path matters
    more, not less."""
    store = _store(tmp_path)
    row = {"version": 1, "origin": "something_else", "provenance_json": None, "components_json": None}

    text = store.render_provenance(row)

    assert text == "strategy (v1)"


# ── S25 (#878): CHECK-constraint migration ──────────────────────────────
#
# strategy_versions.origin used to carry a hard SQL
# CHECK(origin IN ('generated', 'user_edited', 'mixed')) inside a
# CREATE TABLE IF NOT EXISTS -- editing that DDL string to add 'discovered'
# would be a no-op on any *existing* database (IF NOT EXISTS skips
# re-creation), so a test against a fresh tmp_path db would go green while
# a real, already-populated strategy_specs.db would still raise
# IntegrityError the first time a discovered strategy was saved. These
# tests build a database with the OLD DDL first, matching what a real
# user's file looks like today -- a fresh-db test proves nothing here.

_OLD_SCHEMA_SQL = """
    CREATE TABLE strategy_specs (
        strategy_id TEXT PRIMARY KEY,
        symbol      TEXT NOT NULL,
        code        TEXT NOT NULL,
        qty         REAL NOT NULL DEFAULT 1.0,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE strategy_versions (
        strategy_id       TEXT NOT NULL,
        version           INTEGER NOT NULL,
        code              TEXT NOT NULL,
        origin            TEXT NOT NULL CHECK(origin IN ('generated', 'user_edited', 'mixed')),
        provenance_json   TEXT,
        hypothesis        TEXT,
        parent_version    INTEGER,
        components_json   TEXT,
        created_at        TEXT NOT NULL,
        PRIMARY KEY (strategy_id, version)
    );
"""


def _make_old_schema_db(tmp_path):
    """A database file built with the pre-S25 DDL (CHECK constraint
    included), with one real pre-existing row -- what a real user's
    strategy_specs.db looks like before ever opening a post-S25 Felix."""
    import sqlite3
    db_path = tmp_path / "old_specs.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_OLD_SCHEMA_SQL)
    con.execute(
        "INSERT INTO strategy_versions "
        "(strategy_id, version, code, origin, provenance_json, hypothesis, "
        "parent_version, components_json, created_at) VALUES "
        "('s1', 1, 'def strategy(data): return [1]', 'generated', NULL, "
        "'pre-existing', NULL, NULL, '2026-01-01T00:00:00')"
    )
    con.commit()
    con.close()
    return db_path


def test_migration_drops_the_check_constraint(tmp_path):
    db_path = _make_old_schema_db(tmp_path)

    store = StrategyStore(db_path=db_path)

    ddl = store._con.execute(
        "SELECT sql FROM sqlite_master WHERE name='strategy_versions'"
    ).fetchone()[0]
    assert "CHECK" not in ddl


# ── S39: strategy identity fix (expansion suffix helpers) ─────────────

def test_suffix_mint_and_strip_round_trip(tmp_path):
    """Minting a suffixed id and stripping it must recover the original claim."""
    claim = "Buy when RSI < 30"
    symbol = "TSLA"
    full_id = mint_expansion_strategy_id(claim, symbol)
    assert full_id == "Buy when RSI < 30 @TSLA"
    assert strip_expansion_suffix(full_id) == claim


def test_strip_preserves_literal_at_in_claim(tmp_path):
    """A claim containing `@` elsewhere must not be mangled by strip()."""
    claim = "Buy @ when RSI < 30"
    symbol = "AAPL"
    full_id = mint_expansion_strategy_id(claim, symbol)
    assert full_id == "Buy @ when RSI < 30 @AAPL"
    # strip should only remove the trailing suffix
    assert strip_expansion_suffix(full_id) == claim


def test_save_suffixed_and_bare_dont_collide(tmp_path):
    """A bare `strategy_id` (original symbol) and a suffixed one (expanded)
    must occupy separate PK rows in strategy_specs."""
    store = _store(tmp_path)
    bare_id = "Buy when RSI < 30"
    expanded_id = mint_expansion_strategy_id("Buy when RSI < 30", "MSFT")

    store.save(StrategySpec(bare_id, "AAPL", "code_aapl"))
    store.save(StrategySpec(expanded_id, "MSFT", "code_msft"))

    # Both must be retrievable by their exact IDs
    assert store.get(bare_id) is not None
    assert store.get(bare_id).symbol == "AAPL"
    assert store.get(expanded_id) is not None
    assert store.get(expanded_id).symbol == "MSFT"

    # List should contain both
    all_specs = store.list_all()
    assert len(all_specs) == 2


def test_migration_preserves_pre_existing_rows(tmp_path):
    db_path = _make_old_schema_db(tmp_path)

    store = StrategyStore(db_path=db_path)

    row = store._con.execute(
        "SELECT * FROM strategy_versions WHERE strategy_id = 's1'"
    ).fetchone()
    assert row["hypothesis"] == "pre-existing"
    assert row["origin"] == "generated"


def test_migration_unlocks_saving_origin_discovered(tmp_path):
    """The actual bug: against the old DDL, this raised sqlite3.IntegrityError."""
    db_path = _make_old_schema_db(tmp_path)
    store = StrategyStore(db_path=db_path)

    store.save(StrategySpec("s2", "TSLA", "code"), origin="discovered")

    row = store._con.execute(
        "SELECT origin FROM strategy_versions WHERE strategy_id = 's2'"
    ).fetchone()
    assert row["origin"] == "discovered"


def test_migration_is_idempotent_across_restarts(tmp_path):
    """A second StrategyStore against the same (already-migrated) file --
    simulating a Felix restart -- must not re-run the migration, error, or
    duplicate rows."""
    db_path = _make_old_schema_db(tmp_path)
    StrategyStore(db_path=db_path)  # first open: migrates

    store2 = StrategyStore(db_path=db_path)  # second open: already migrated

    count = store2._con.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0]
    assert count == 1  # just the original pre-existing row, not duplicated
    ddl = store2._con.execute(
        "SELECT sql FROM sqlite_master WHERE name='strategy_versions'"
    ).fetchone()[0]
    assert "CHECK" not in ddl
