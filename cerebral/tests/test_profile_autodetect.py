"""
Profile auto-detect tests — Issue #237 (C.1).

Covers the three acceptance-criteria paths:

  1. Second launch auto-selects the previously active profile with no picker.
  2. First-ever launch (or deleted last-used profile) falls back to the
     existing selection flow (get_active() returns None).
  3. Manual switch updates the remembered profile and persists across restart.

All tests exercise ProfileManager directly against a temporary SQLite DB so
that a "restart" can be simulated by opening a new instance on the same file
without touching Cerebral's module-level globals.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import ProfileManager


# ---------------------------------------------------------------------------
# Path 1 — second launch restores the previously active profile
# ---------------------------------------------------------------------------


def test_second_launch_restores_active_profile(tmp_path):
    """After first run with a single profile set active, a fresh ProfileManager
    on the same DB returns that profile from get_active()."""
    db = tmp_path / "openmind.db"
    pm1 = ProfileManager(db_path=db)
    alice = pm1.create(name="Alice", wake_name="felix", voice_id="af_heart")
    pm1.set_active(alice.id)

    pm2 = ProfileManager(db_path=db)
    restored = pm2.get_active()
    assert restored is not None
    assert restored.id == alice.id
    assert restored.name == "Alice"


def test_second_launch_with_multiple_profiles_restores_last_switched(tmp_path):
    """When the user has switched to a non-first profile, the next launch
    restores the switched-to profile, not the first-created one."""
    db = tmp_path / "openmind.db"
    pm1 = ProfileManager(db_path=db)
    alice = pm1.create(name="Alice", wake_name="felix", voice_id="af_heart")
    bob = pm1.create(name="Bob", wake_name="felix", voice_id="af_sky")
    pm1.set_active(bob.id)

    pm2 = ProfileManager(db_path=db)
    restored = pm2.get_active()
    assert restored is not None
    assert restored.id == bob.id


# ---------------------------------------------------------------------------
# Path 2 — first-ever launch / deleted last-used profile falls back
# ---------------------------------------------------------------------------


def test_first_ever_launch_returns_none(tmp_path):
    """A brand-new database has no profiles — get_active() returns None so
    Cerebral broadcasts first_run and the frontend shows the profile wizard."""
    db = tmp_path / "openmind.db"
    pm = ProfileManager(db_path=db)
    assert pm.get_active() is None


def test_deleted_sole_profile_returns_none_on_next_launch(tmp_path):
    """Deleting the only profile leaves the DB empty; get_active() returns
    None so the next launch re-enters the first-run wizard."""
    db = tmp_path / "openmind.db"
    pm1 = ProfileManager(db_path=db)
    alice = pm1.create(name="Alice", wake_name="felix", voice_id="af_heart")
    pm1.set_active(alice.id)
    pm1.delete(alice.id)

    pm2 = ProfileManager(db_path=db)
    assert pm2.get_active() is None


def test_deleted_active_profile_falls_back_to_remaining(tmp_path):
    """Deleting the active profile when a second profile exists: get_active()
    returns the remaining profile so the next launch skips the wizard."""
    db = tmp_path / "openmind.db"
    pm1 = ProfileManager(db_path=db)
    alice = pm1.create(name="Alice", wake_name="felix", voice_id="af_heart")
    bob = pm1.create(name="Bob", wake_name="felix", voice_id="af_sky")
    pm1.set_active(alice.id)
    pm1.delete(alice.id)

    pm2 = ProfileManager(db_path=db)
    restored = pm2.get_active()
    assert restored is not None
    assert restored.id == bob.id


def test_stale_active_profile_id_falls_back_gracefully(tmp_path):
    """If the settings table holds an active_profile_id for a non-existent
    profile (e.g. from a crash before cleanup), get_active() falls through
    to the MRU profile instead of raising."""
    db = tmp_path / "openmind.db"
    pm = ProfileManager(db_path=db)
    alice = pm.create(name="Alice", wake_name="felix", voice_id="af_heart")
    bob = pm.create(name="Bob", wake_name="felix", voice_id="af_sky")
    # Corrupt the setting to point at a non-existent id.
    pm._set_setting("active_profile_id", "9999")

    result = pm.get_active()
    # Falls back to an existing profile — the exact one depends on MRU
    # ordering but must be a valid, currently-present profile.
    assert result is not None
    assert result.id in {alice.id, bob.id}


# ---------------------------------------------------------------------------
# Path 3 — manual switch persists across restart
# ---------------------------------------------------------------------------


def test_manual_switch_persists_across_restart(tmp_path):
    """set_active() (called by the switch_profile IPC handler) must persist
    across restarts so the next launch auto-selects the switched-to profile."""
    db = tmp_path / "openmind.db"
    pm1 = ProfileManager(db_path=db)
    alice = pm1.create(name="Alice", wake_name="felix", voice_id="af_heart")
    bob = pm1.create(name="Bob", wake_name="felix", voice_id="af_sky")
    pm1.set_active(alice.id)

    # Simulate the user switching to Bob.
    pm1.set_active(bob.id)

    pm2 = ProfileManager(db_path=db)
    assert pm2.get_active().id == bob.id


def test_repeated_switches_track_latest(tmp_path):
    """Switching back and forth always persists the most-recent selection."""
    db = tmp_path / "openmind.db"
    pm1 = ProfileManager(db_path=db)
    alice = pm1.create(name="Alice", wake_name="felix", voice_id="af_heart")
    bob = pm1.create(name="Bob", wake_name="felix", voice_id="af_sky")

    pm1.set_active(bob.id)
    pm1.set_active(alice.id)
    pm1.set_active(bob.id)

    pm2 = ProfileManager(db_path=db)
    assert pm2.get_active().id == bob.id


def test_switch_back_to_original_profile_persists(tmp_path):
    """Switching from B back to A correctly records A as last-used."""
    db = tmp_path / "openmind.db"
    pm1 = ProfileManager(db_path=db)
    alice = pm1.create(name="Alice", wake_name="felix", voice_id="af_heart")
    bob = pm1.create(name="Bob", wake_name="felix", voice_id="af_sky")
    pm1.set_active(bob.id)
    pm1.set_active(alice.id)

    pm2 = ProfileManager(db_path=db)
    assert pm2.get_active().id == alice.id
