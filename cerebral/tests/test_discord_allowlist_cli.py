"""Tests for scripts/discord_user_allowlist.py -- Issue #177.

Drives the CLI's ``main(argv)`` against a per-test SQLite file so the
real production DB is never touched. We monkeypatch ``ProfileManager``
in the CLI module to point at the throwaway file.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

from cerebral.db.profiles import ProfileManager


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "discord_user_allowlist.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location(
        "discord_user_allowlist_cli", SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


CLI = _load_cli_module()


@pytest.fixture
def tmp_pm(monkeypatch):
    tmp_db = Path(tempfile.mkdtemp()) / "openmind.db"
    pm = ProfileManager(db_path=tmp_db)
    monkeypatch.setattr(CLI, "ProfileManager", lambda: pm)
    return pm


def test_add_then_list_then_remove(tmp_pm, capsys):
    profile = tmp_pm.create(name="Iggy")
    tmp_pm.set_active(profile.id)

    assert CLI.main(["add", "USER_42", "--note", "partner"]) == 0
    out = capsys.readouterr().out
    assert "added USER_42" in out

    assert CLI.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "USER_42" in out
    assert "partner" in out

    assert CLI.main(["remove", "USER_42"]) == 0
    out = capsys.readouterr().out
    assert "removed USER_42" in out


def test_list_on_empty_allowlist_returns_clean_marker(tmp_pm, capsys):
    profile = tmp_pm.create(name="Iggy")
    tmp_pm.set_active(profile.id)
    assert CLI.main(["list"]) == 0
    assert "empty" in capsys.readouterr().out.lower()


def test_remove_unknown_warns_and_returns_nonzero(tmp_pm, capsys):
    profile = tmp_pm.create(name="Iggy")
    tmp_pm.set_active(profile.id)
    rc = CLI.main(["remove", "GHOST"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "not on the allowlist" in err


def test_settings_show_lists_every_default(tmp_pm, capsys):
    profile = tmp_pm.create(name="Iggy")
    tmp_pm.set_active(profile.id)
    assert CLI.main(["settings", "show"]) == 0
    out = capsys.readouterr().out
    assert "rate_limit_max" in out
    assert "delay_min_s" in out
    assert "default" in out


def test_settings_set_then_clear(tmp_pm, capsys):
    profile = tmp_pm.create(name="Iggy")
    tmp_pm.set_active(profile.id)
    assert CLI.main(["settings", "set", "rate_limit_max", "7"]) == 0
    assert tmp_pm.get_discord_setting(profile.id, "rate_limit_max") == "7"
    assert CLI.main(["settings", "clear", "rate_limit_max"]) == 0
    assert tmp_pm.get_discord_setting(profile.id, "rate_limit_max") is None


def test_settings_set_rejects_unknown_key(tmp_pm, capsys):
    profile = tmp_pm.create(name="Iggy")
    tmp_pm.set_active(profile.id)
    rc = CLI.main(["settings", "set", "bogus_key", "1"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown setting key" in err


def test_explicit_profile_id_overrides_active(tmp_pm, capsys):
    active = tmp_pm.create(name="Active")
    other = tmp_pm.create(name="Other")
    tmp_pm.set_active(active.id)
    # Add to OTHER explicitly.
    assert CLI.main(["--profile", str(other.id), "add", "USER_42"]) == 0
    assert tmp_pm.is_discord_allowlisted(other.id, "USER_42") is True
    assert tmp_pm.is_discord_allowlisted(active.id, "USER_42") is False


def test_no_active_profile_errors_out(tmp_pm, capsys):
    # Fresh PM with no profiles -- _resolve_profile sys.exits with code 2.
    with pytest.raises(SystemExit) as ei:
        CLI.main(["list"])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "no active profile" in err
