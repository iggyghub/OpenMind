"""
Tests for persisting the user's active model on the profile — Issue #37.

The `active_model` column on `profiles` survives restart and is round-trip
read/written by ProfileManager. Migration via ALTER TABLE follows the existing
pattern in `_init_schema`.
"""
import sqlite3
from pathlib import Path

import pytest

from cerebral.db.profiles import ProfileManager


@pytest.fixture
def tmp_pm(tmp_path: Path) -> ProfileManager:
    return ProfileManager(db_path=tmp_path / "test.db")


def test_new_profile_defaults_active_model_to_empty(tmp_pm):
    p = tmp_pm.create(name="Iggy")
    assert p.active_model == ""


def test_update_active_model_round_trips(tmp_pm):
    p = tmp_pm.create(name="Iggy")
    tmp_pm.update_active_model(p.id, "ollama/gemma3:latest")
    p2 = tmp_pm.get(p.id)
    assert p2.active_model == "ollama/gemma3:latest"


def test_update_active_model_overwrites_previous_choice(tmp_pm):
    p = tmp_pm.create(name="Iggy")
    tmp_pm.update_active_model(p.id, "ollama/gemma3:latest")
    tmp_pm.update_active_model(p.id, "claude/haiku")
    assert tmp_pm.get(p.id).active_model == "claude/haiku"


def test_active_model_survives_manager_restart(tmp_path: Path):
    """Persistence: a new ProfileManager on the same DB sees the saved choice."""
    db = tmp_path / "persist.db"
    pm1 = ProfileManager(db_path=db)
    p = pm1.create(name="Iggy")
    pm1.update_active_model(p.id, "claude/sonnet")
    pm2 = ProfileManager(db_path=db)
    assert pm2.get(p.id).active_model == "claude/sonnet"


def test_migration_adds_column_to_pre_existing_db(tmp_path: Path):
    """Pre-existing DBs from before #37 get the column added without data loss."""
    db = tmp_path / "legacy.db"
    # Simulate an old DB built without active_model
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE profiles (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL,
            wake_name           TEXT NOT NULL DEFAULT 'felix',
            pronunciation_guide TEXT NOT NULL DEFAULT '',
            voice_id            TEXT NOT NULL DEFAULT 'default',
            connected_accounts  TEXT NOT NULL DEFAULT '[]',
            voice_sample        TEXT NOT NULL DEFAULT '',
            wake_sample         TEXT NOT NULL DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO profiles (name) VALUES ('LegacyUser');
    """)
    con.commit()
    con.close()

    pm = ProfileManager(db_path=db)
    profiles = pm.list_all()
    assert len(profiles) == 1
    assert profiles[0].name == "LegacyUser"
    assert profiles[0].active_model == ""

    # And it's now writable
    pm.update_active_model(profiles[0].id, "ollama/gemma3:latest")
    assert pm.get(profiles[0].id).active_model == "ollama/gemma3:latest"


def test_full_update_preserves_active_model(tmp_pm):
    """`ProfileManager.update(profile)` is a separate path — it must keep the column."""
    p = tmp_pm.create(name="Iggy")
    tmp_pm.update_active_model(p.id, "ollama/gemma3:latest")
    p_loaded = tmp_pm.get(p.id)
    p_loaded.wake_name = "buddy"
    tmp_pm.update(p_loaded)
    assert tmp_pm.get(p.id).active_model == "ollama/gemma3:latest"
