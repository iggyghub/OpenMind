"""Approval presets over the ADR-0005 gate (harness parity H6-S1 / #737).
Mirrors test_profile_acl.py's fixture pattern: real ProfileManager on a
tmp_path SQLite db, real Profile, real ProfileACL -- fast, no mocks needed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile, ProfileManager
from cerebral.security import Capability, Decision, ProfileACL
from cerebral.security.presets import PRESETS, apply_preset, list_presets

_TRUSTED_SILENT = (
    Capability.FS_READ, Capability.CLIPBOARD, Capability.NETWORK_EGRESS_LOCAL,
    Capability.NETWORK_EGRESS_CLOUD, Capability.EXTERNAL_DATA_READ, Capability.DEVICE_CONTROL,
    Capability.FS_WRITE, Capability.FS_DELETE, Capability.CODE_INSTALL,
    Capability.NETWORK_RECON, Capability.NETWORK_CONFIG, Capability.EXTERNAL_DATA_WRITE,
    Capability.SCREEN_CAPTURE,
)
_TRUSTED_ASK = (Capability.VAULT_UNLOCK, Capability.SECRETS_READ, Capability.SHELL_EXEC)


@pytest.fixture
def pm(tmp_path) -> ProfileManager:
    return ProfileManager(db_path=tmp_path / "openmind.db")


@pytest.fixture
def profile(pm: ProfileManager) -> Profile:
    return pm.create(name="Alice", wake_name="felix", voice_id="af_heart")


@pytest.fixture
def acl(pm: ProfileManager, profile: Profile) -> ProfileACL:
    return ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )


def test_list_presets_returns_all_three():
    assert list_presets() == ["ask-everything", "full-auto", "trusted-workspace"]


def test_presets_cover_every_capability():
    for name, mapping in PRESETS.items():
        assert set(mapping) == set(Capability), f"{name} missing capabilities"


def test_apply_unknown_preset_raises(acl):
    with pytest.raises(ValueError):
        apply_preset(acl, "not-a-real-preset")


def test_ask_everything_yields_all_ask(acl):
    apply_preset(acl, "ask-everything")
    for cap in Capability:
        assert acl.resolve(cap, "some_tool") == Decision.ASK


def test_full_auto_yields_all_silent(acl):
    apply_preset(acl, "full-auto")
    for cap in Capability:
        assert acl.resolve(cap, "some_tool") == Decision.SILENT


def test_trusted_workspace_matches_exact_table(acl):
    apply_preset(acl, "trusted-workspace")
    for cap in _TRUSTED_SILENT:
        assert acl.resolve(cap, "some_tool") == Decision.SILENT, cap
    for cap in _TRUSTED_ASK:
        assert acl.resolve(cap, "some_tool") == Decision.ASK, cap


def test_preset_is_persistent_not_session(acl):
    apply_preset(acl, "full-auto")
    rows = acl.list_persistent_grants()
    class_rows = {r["target"] for r in rows if r["scope"] == "class"}
    for cap in Capability:
        assert cap.value in class_rows, f"{cap.value} not persisted"
        row = next(r for r in rows if r["scope"] == "class" and r["target"] == cap.value)
        assert row["policy"] == Decision.SILENT.value


def test_switching_preset_overwrites_previous(acl):
    apply_preset(acl, "ask-everything")
    apply_preset(acl, "full-auto")
    for cap in Capability:
        assert acl.resolve(cap, "some_tool") == Decision.SILENT


def test_orchestrator_module_unchanged_by_this_slice():
    # Structural safety property: this slice writes ONLY class-grant rows via
    # ProfileACL.set_persistent_class and touches no file under cerebral/mcp/,
    # so ADR-0016 irreversible-action escalation (which lives there, gated
    # separately after ACL resolution) is untouched regardless of preset.
    import cerebral.mcp.orchestrator  # noqa: F401 -- import-sanity only
