"""
ProfileACL tests — Issue #45.

Covers the 5-step resolution order, the post-ACL passive escalation, per-tool
override paths, the new-profile snapshot inheritance, RAM-grant lifecycle on
profile switch, and the SQLite persistence layer (`profile_acl` table).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import Profile, ProfileManager
from cerebral.security import (
    Capability,
    CallFlags,
    DEFAULT_POLICY,
    Decision,
    ProfileACL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm(tmp_path) -> ProfileManager:
    db = tmp_path / "openmind.db"
    return ProfileManager(db_path=db)


@pytest.fixture
def profile(pm: ProfileManager) -> Profile:
    return pm.create(name="Alice", wake_name="felix", voice_id="af_heart")


@pytest.fixture
def acl(pm: ProfileManager, profile: Profile) -> ProfileACL:
    return ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )


# ---------------------------------------------------------------------------
# Slice 1 — snapshot inheritance at profile creation
# ---------------------------------------------------------------------------


def test_new_profile_inherits_full_default_policy_snapshot(profile):
    snapshot = profile.acl_defaults_snapshot
    assert isinstance(snapshot, dict)
    assert set(snapshot) == {c.value for c in Capability}
    for cap, expected in DEFAULT_POLICY.items():
        assert snapshot[cap.value] == expected.value


def test_subsequent_default_changes_do_not_propagate(pm, profile):
    """The profile's snapshot is a frozen copy. Mutating the dict on the
    Profile dataclass (which lives in the snapshot column as JSON) doesn't
    leak back into the SQLite row, and creating a new profile with
    explicit override defaults doesn't affect the original."""
    captured = dict(profile.acl_defaults_snapshot)

    # A fresh profile created with explicit defaults gets its own row.
    new_defaults = {cap.value: "deny" for cap in Capability}
    later = pm.create(name="Bob", acl_defaults_snapshot=new_defaults)
    assert later.acl_defaults_snapshot[Capability.FS_READ.value] == "deny"
    # The original profile's stored row is unchanged.
    refreshed = pm.get(profile.id)
    assert refreshed.acl_defaults_snapshot == captured


def test_legacy_profile_without_snapshot_falls_back_to_default_policy(pm):
    """A profile whose row predates the snapshot column resolves via the
    live DEFAULT_POLICY — the ACL constructor injects it when the
    snapshot dict is empty."""
    # Simulate a legacy row: insert directly with empty snapshot
    pm._con.execute(
        "INSERT INTO profiles (name, acl_defaults_snapshot) VALUES (?, ?)",
        ("Legacy", "{}"),
    )
    pm._con.commit()
    row = pm._con.execute(
        "SELECT id FROM profiles WHERE name='Legacy'"
    ).fetchone()
    legacy_id = row["id"]
    acl = ProfileACL(profile_id=legacy_id, profile_manager=pm, defaults_snapshot={})
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY
    assert acl.resolve(Capability.FS_READ, "read_file") is Decision.SILENT


# ---------------------------------------------------------------------------
# Slice 2 — fall-through to default policy (step 5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cap", list(Capability))
def test_resolve_falls_through_to_snapshot_default(acl, cap):
    assert acl.resolve(cap, "tool_name") is DEFAULT_POLICY[cap]


# ---------------------------------------------------------------------------
# Slice 3 — per-tool override wins over class default (step 1)
# ---------------------------------------------------------------------------


def test_tool_override_beats_class_default(pm, acl):
    # fs_write defaults to ASK. Pin "write_file" to silent.
    acl.set_tool_override("write_file", Decision.SILENT)
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.SILENT
    # Another tool in the same class still uses the class default.
    assert acl.resolve(Capability.FS_WRITE, "create_file") is Decision.ASK


def test_tool_override_beats_persistent_class_grant(acl):
    acl.set_persistent_class(Capability.FS_WRITE, Decision.DENY)
    acl.set_tool_override("write_file", Decision.SILENT)
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.SILENT


def test_tool_override_can_deny_a_class_grant(acl):
    acl.set_persistent_class(Capability.FS_READ, Decision.SILENT)
    acl.set_tool_override("dangerous_read", Decision.DENY)
    assert acl.resolve(Capability.FS_READ, "dangerous_read") is Decision.DENY
    assert acl.resolve(Capability.FS_READ, "harmless_read") is Decision.SILENT


def test_revoke_tool_override_returns_to_class_resolution(acl):
    acl.set_tool_override("write_file", Decision.SILENT)
    revoked = acl.revoke_tool_override("write_file")
    assert revoked is True
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.ASK


def test_revoke_unknown_tool_override_returns_false(acl):
    assert acl.revoke_tool_override("never_set") is False


# ---------------------------------------------------------------------------
# Slice 4 — persistent class grant (step 2)
# ---------------------------------------------------------------------------


def test_persistent_class_grant_overrides_default(acl):
    # shell_exec defaults to DENY; user opts in persistently.
    acl.set_persistent_class(Capability.SHELL_EXEC, Decision.ASK)
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.ASK


def test_persistent_class_grant_persists_across_acl_instances(pm, profile):
    acl1 = ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl1.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)
    # A new ACL instance (e.g. after Cerebral restart) sees the same row.
    acl2 = ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    assert acl2.resolve(Capability.FS_WRITE, "write_file") is Decision.SILENT


def test_revoke_persistent_class_returns_to_default(acl):
    acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)
    assert acl.revoke_persistent_class(Capability.FS_WRITE) is True
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.ASK


def test_revoke_unknown_persistent_class_returns_false(acl):
    assert acl.revoke_persistent_class(Capability.FS_WRITE) is False


# ---------------------------------------------------------------------------
# Slice 5 — session class grant (step 3, RAM-only)
# ---------------------------------------------------------------------------


def test_session_class_grant_beats_default(acl):
    acl.grant_session(Capability.SHELL_EXEC, Decision.SILENT)
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.SILENT


def test_session_class_grant_loses_to_persistent_class_grant(acl):
    """Issue body step order: persistent (step 2) is consulted before session
    (step 3). Persistent wins when both are set."""
    acl.set_persistent_class(Capability.FS_WRITE, Decision.DENY)
    acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.DENY


def test_session_class_grant_loses_to_tool_override(acl):
    acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    acl.set_tool_override("write_file", Decision.DENY)
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.DENY


def test_session_grant_does_not_persist_to_new_acl_instance(pm, profile):
    acl1 = ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl1.grant_session(Capability.SHELL_EXEC, Decision.SILENT)
    acl2 = ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    assert acl2.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY


def test_revoke_session_grant(acl):
    acl.grant_session(Capability.SHELL_EXEC, Decision.SILENT)
    assert acl.revoke_session(Capability.SHELL_EXEC) is True
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY


# ---------------------------------------------------------------------------
# Slice 6 — once class grant (step 4, RAM-only, consumed on use)
# ---------------------------------------------------------------------------


def test_once_grant_consumed_after_first_call(acl):
    acl.grant_once(Capability.SHELL_EXEC, Decision.SILENT)
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.SILENT
    # Second call: once was consumed, fall back to default DENY.
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY


def test_multiple_once_grants_queue_in_order(acl):
    acl.grant_once(Capability.SHELL_EXEC, Decision.SILENT)
    acl.grant_once(Capability.SHELL_EXEC, Decision.DENY)
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.SILENT
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY
    # Queue empty, back to default.
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY


def test_once_grant_loses_to_persistent_class_grant(acl):
    acl.set_persistent_class(Capability.FS_WRITE, Decision.DENY)
    acl.grant_once(Capability.FS_WRITE, Decision.SILENT)
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.DENY
    # The persistent grant absorbed the call — once is NOT consumed.
    # (Per the issue body, step 2 short-circuits before step 4.)
    # So a subsequent revocation of persistent surfaces the queued once.
    acl.revoke_persistent_class(Capability.FS_WRITE)
    assert acl.resolve(Capability.FS_WRITE, "write_file") is Decision.SILENT


def test_once_grant_does_not_persist_across_instances(pm, profile):
    acl1 = ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    acl1.grant_once(Capability.SHELL_EXEC, Decision.SILENT)
    acl2 = ProfileACL(
        profile_id=profile.id, profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    assert acl2.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY


# ---------------------------------------------------------------------------
# Slice 7 — passive escalation runs AFTER ACL resolution
# ---------------------------------------------------------------------------


def test_passive_escalates_silent_default_to_ask(acl):
    flags = CallFlags(passive=True)
    assert acl.resolve(Capability.FS_READ, "read_file", flags) is Decision.ASK


def test_passive_escalates_ask_default_to_deny(acl):
    flags = CallFlags(passive=True)
    assert acl.resolve(Capability.FS_WRITE, "write_file", flags) is Decision.DENY


def test_passive_escalates_persistent_silent_grant_to_ask(acl):
    """Critical regression guard: a session/persistent SILENT grant must NOT
    let a passive (queue-originated) call through silently."""
    acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)
    flags = CallFlags(passive=True)
    assert acl.resolve(Capability.FS_WRITE, "write_file", flags) is Decision.ASK


def test_passive_escalates_session_silent_grant_to_ask(acl):
    acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    flags = CallFlags(passive=True)
    assert acl.resolve(Capability.FS_WRITE, "write_file", flags) is Decision.ASK


def test_passive_escalates_tool_override_silent_to_ask(acl):
    """Even a per-tool persistent override at SILENT must be escalated when
    the call is queue-originated — defeats targeted bypasses."""
    acl.set_tool_override("write_file", Decision.SILENT)
    flags = CallFlags(passive=True)
    assert acl.resolve(Capability.FS_WRITE, "write_file", flags) is Decision.ASK


def test_passive_deny_stays_deny(acl):
    flags = CallFlags(passive=True)
    assert acl.resolve(Capability.SHELL_EXEC, "run_command", flags) is Decision.DENY


def test_non_passive_calls_unaffected(acl):
    flags = CallFlags(passive=False)
    assert acl.resolve(Capability.FS_READ, "read_file", flags) is Decision.SILENT


# ---------------------------------------------------------------------------
# Slice 8 — clear_transient() drops once+session but keeps persistent
# ---------------------------------------------------------------------------


def test_clear_transient_drops_session_grants(acl):
    acl.grant_session(Capability.SHELL_EXEC, Decision.SILENT)
    acl.clear_transient()
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY


def test_clear_transient_drops_once_grants(acl):
    acl.grant_once(Capability.SHELL_EXEC, Decision.SILENT)
    acl.clear_transient()
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.DENY


def test_clear_transient_keeps_persistent_grants(acl):
    acl.set_persistent_class(Capability.SHELL_EXEC, Decision.ASK)
    acl.clear_transient()
    assert acl.resolve(Capability.SHELL_EXEC, "run_command") is Decision.ASK


# ---------------------------------------------------------------------------
# Slice 9 — type / argument validation
# ---------------------------------------------------------------------------


def test_resolve_rejects_non_capability(acl):
    with pytest.raises(TypeError):
        acl.resolve("shell_exec", "run_command")  # type: ignore[arg-type]


def test_resolve_handles_missing_flags(acl):
    # Calling without flags should not raise and uses passive=False.
    assert acl.resolve(Capability.FS_READ, "read_file") is Decision.SILENT


# ---------------------------------------------------------------------------
# Slice 10 — ProfileManager grant CRUD edge cases
# ---------------------------------------------------------------------------


def test_set_acl_grant_rejects_unknown_scope(pm, profile):
    with pytest.raises(ValueError):
        pm.set_acl_grant(profile.id, scope="invalid", target="x", policy="silent")


def test_set_acl_grant_rejects_unknown_policy(pm, profile):
    with pytest.raises(ValueError):
        pm.set_acl_grant(profile.id, scope="class", target="fs_read", policy="maybe")


def test_set_acl_grant_overwrites_existing_row(pm, profile):
    pm.set_acl_grant(profile.id, scope="class", target="fs_read", policy="silent")
    pm.set_acl_grant(profile.id, scope="class", target="fs_read", policy="deny")
    rows = pm.list_acl_grants(profile.id)
    matching = [r for r in rows if r["scope"] == "class" and r["target"] == "fs_read"]
    assert len(matching) == 1
    assert matching[0]["policy"] == "deny"


def test_list_acl_grants_returns_all_rows(pm, profile):
    pm.set_acl_grant(profile.id, scope="class", target="fs_read", policy="silent")
    pm.set_acl_grant(profile.id, scope="tool", target="write_file", policy="deny")
    grants = pm.list_acl_grants(profile.id)
    assert len(grants) == 2
    scopes = {g["scope"] for g in grants}
    assert scopes == {"class", "tool"}


def test_acl_rows_isolated_per_profile(pm):
    alice = pm.create(name="Alice")
    bob = pm.create(name="Bob")
    pm.set_acl_grant(alice.id, scope="class", target="fs_read", policy="deny")
    assert pm.list_acl_grants(bob.id) == []


def test_deleting_profile_cascades_acl_rows(pm):
    """profile_acl has FOREIGN KEY ... ON DELETE CASCADE — deleting the
    profile must take its grants with it."""
    pm._con.execute("PRAGMA foreign_keys = ON")
    p = pm.create(name="Temp")
    pm.set_acl_grant(p.id, scope="class", target="fs_read", policy="deny")
    pm.delete(p.id)
    assert pm.list_acl_grants(p.id) == []


# ---------------------------------------------------------------------------
# Slice 11 — plugin_flags table (Issue #51, ADR-0005)
# ---------------------------------------------------------------------------


def test_plugin_flag_defaults_to_false_when_no_row(pm):
    assert pm.get_plugin_new_flag("weatherbug") is False


def test_set_plugin_new_flag_persists(pm):
    pm.set_plugin_new_flag("weatherbug", True)
    assert pm.get_plugin_new_flag("weatherbug") is True


def test_set_plugin_new_flag_overwrites_existing(pm):
    pm.set_plugin_new_flag("weatherbug", True)
    pm.set_plugin_new_flag("weatherbug", False)
    assert pm.get_plugin_new_flag("weatherbug") is False


def test_remove_plugin_flag_drops_row(pm):
    pm.set_plugin_new_flag("weatherbug", True)
    assert pm.remove_plugin_flag("weatherbug") is True
    assert pm.get_plugin_new_flag("weatherbug") is False
    # Second remove finds nothing.
    assert pm.remove_plugin_flag("weatherbug") is False


def test_list_plugin_flags_returns_only_rows_present(pm):
    pm.set_plugin_new_flag("weatherbug", True)
    pm.set_plugin_new_flag("ambient", False)
    listed = pm.list_plugin_flags()
    assert listed == {"weatherbug": True, "ambient": False}


# ---------------------------------------------------------------------------
# Slice 12 — uninstall ACL cleanup (Issue #51, ADR-0005)
# ---------------------------------------------------------------------------


def test_remove_plugin_acl_rows_drops_only_per_tool_rows(pm):
    alice = pm.create(name="Alice")
    bob = pm.create(name="Bob")

    # Alice grants class-scope FS_WRITE persistently, plus per-tool overrides
    # for two tools that belong to "weatherbug".
    pm.set_acl_grant(alice.id, scope="class", target="fs_write", policy="silent")
    pm.set_acl_grant(alice.id, scope="tool", target="weatherbug_ping", policy="silent")
    pm.set_acl_grant(alice.id, scope="tool", target="weatherbug_report", policy="deny")
    # Plus an unrelated per-tool override on a different plugin.
    pm.set_acl_grant(alice.id, scope="tool", target="other_tool", policy="deny")
    # Bob has a per-tool override on a weatherbug tool too.
    pm.set_acl_grant(bob.id, scope="tool", target="weatherbug_ping", policy="silent")

    dropped = pm.remove_plugin_acl_rows(
        "weatherbug", tool_names=["weatherbug_ping", "weatherbug_report"],
    )
    # 3 rows: alice's two weatherbug overrides + bob's one weatherbug override.
    assert dropped == 3

    alice_rows = pm.list_acl_grants(alice.id)
    alice_targets = {(r["scope"], r["target"]) for r in alice_rows}
    # Class-scope FS_WRITE survives — per the sharpener, class-scope rows
    # live across plugins.
    assert ("class", "fs_write") in alice_targets
    # The unrelated tool override survives.
    assert ("tool", "other_tool") in alice_targets
    # Weatherbug overrides are gone.
    assert ("tool", "weatherbug_ping") not in alice_targets
    assert ("tool", "weatherbug_report") not in alice_targets

    bob_rows = pm.list_acl_grants(bob.id)
    bob_targets = {(r["scope"], r["target"]) for r in bob_rows}
    assert ("tool", "weatherbug_ping") not in bob_targets


def test_remove_plugin_acl_rows_returns_zero_when_no_match(pm):
    alice = pm.create(name="Alice")
    pm.set_acl_grant(alice.id, scope="class", target="fs_read", policy="silent")
    assert pm.remove_plugin_acl_rows("weatherbug", tool_names=[]) == 0
    assert pm.remove_plugin_acl_rows("weatherbug", tool_names=["never_existed"]) == 0
    # Class grant untouched.
    assert pm.list_acl_grants(alice.id) == [
        {"scope": "class", "target": "fs_read", "policy": "silent",
         "granted_at": pm.list_acl_grants(alice.id)[0]["granted_at"]},
    ]


# ---------------------------------------------------------------------------
# Slice 13 — ProfileACL new-plugin-flag carve-out (Issue #51, ADR-0005)
# ---------------------------------------------------------------------------


def test_new_plugin_flag_skips_persistent_class_grant(pm, profile):
    """While a plugin's new_plugin flag is set, ProfileACL.resolve ignores
    the per-tool override, persistent class grant, session, and once grants
    on that tool's class — every call asks fresh."""
    pm.set_acl_grant(profile.id, scope="class", target="fs_write", policy="silent")
    pm.set_plugin_new_flag("weatherbug", True)

    # ACL hook: weatherbug_ping belongs to weatherbug (which is new-flagged).
    def _new_flag(tool_name: str) -> bool:
        return tool_name.startswith("weatherbug_") and pm.get_plugin_new_flag("weatherbug")

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
        new_plugin_flag_for_tool=_new_flag,
    )
    # With the flag on, the persistent SILENT grant is invisible: we fall
    # through to the default policy for FS_WRITE which is ASK.
    assert acl.resolve(Capability.FS_WRITE, "weatherbug_ping") is Decision.ASK


def test_new_plugin_flag_does_not_affect_other_plugins(pm, profile):
    pm.set_acl_grant(profile.id, scope="class", target="fs_write", policy="silent")
    pm.set_plugin_new_flag("weatherbug", True)

    def _new_flag(tool_name: str) -> bool:
        return tool_name.startswith("weatherbug_") and pm.get_plugin_new_flag("weatherbug")

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
        new_plugin_flag_for_tool=_new_flag,
    )
    # A tool from a *different* plugin sees the normal class grant.
    assert acl.resolve(Capability.FS_WRITE, "files_write") is Decision.SILENT


def test_new_plugin_flag_off_means_normal_resolution(pm, profile):
    """Hand-authored plugins (no flag set) resolve through the full chain."""
    pm.set_acl_grant(profile.id, scope="class", target="fs_write", policy="silent")
    # No flag set for "files" plugin.

    def _new_flag(tool_name: str) -> bool:
        return False  # nothing is flagged

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
        new_plugin_flag_for_tool=_new_flag,
    )
    assert acl.resolve(Capability.FS_WRITE, "files_write") is Decision.SILENT


def test_new_plugin_flag_skips_per_tool_override_too(pm, profile):
    pm.set_acl_grant(profile.id, scope="tool", target="weatherbug_ping", policy="silent")
    pm.set_plugin_new_flag("weatherbug", True)

    def _new_flag(tool_name: str) -> bool:
        return tool_name.startswith("weatherbug_") and pm.get_plugin_new_flag("weatherbug")

    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
        new_plugin_flag_for_tool=_new_flag,
    )
    # Per-tool SILENT override is also bypassed.
    assert acl.resolve(Capability.FS_WRITE, "weatherbug_ping") is Decision.ASK


def test_new_plugin_flag_resolver_default_hook_is_no_op(pm, profile):
    """Omitting new_plugin_flag_for_tool keeps pre-#51 resolution behaviour."""
    pm.set_acl_grant(profile.id, scope="class", target="fs_write", policy="silent")
    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    assert acl.resolve(Capability.FS_WRITE, "weatherbug_ping") is Decision.SILENT


# ---------------------------------------------------------------------------
# Slice 11 — list_session_grants (Issue #53; surfaced for the Permissions UI)
# ---------------------------------------------------------------------------


def test_list_session_grants_returns_empty_for_fresh_acl(acl):
    assert acl.list_session_grants() == []


def test_list_session_grants_after_grant_session(acl):
    acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    rows = acl.list_session_grants()
    assert rows == [{"capability": "fs_write", "policy": "silent"}]


def test_list_session_grants_does_not_include_once_grants(acl):
    """Once-grants are consumed on next call; surfacing them in a UI
    list would race the consumer. Only session class grants appear."""
    acl.grant_once(Capability.FS_WRITE, Decision.SILENT)
    acl.grant_session(Capability.FS_READ, Decision.DENY)
    rows = {row["capability"] for row in acl.list_session_grants()}
    assert rows == {"fs_read"}


def test_list_session_grants_clears_after_revoke(acl):
    acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    acl.revoke_session(Capability.FS_WRITE)
    assert acl.list_session_grants() == []


def test_list_session_grants_clears_on_clear_transient(acl):
    acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    acl.grant_session(Capability.SHELL_EXEC, Decision.ASK)
    acl.clear_transient()
    assert acl.list_session_grants() == []


# ---------------------------------------------------------------------------
# Slice 12 — shell_exec_unlocked profile column (Issue #53)
# ---------------------------------------------------------------------------


def test_profile_shell_exec_locked_by_default(pm):
    p = pm.create(name="LockedAlice")
    assert p.shell_exec_unlocked is False


def test_unlock_shell_exec_persists(pm):
    p = pm.create(name="ShellAlice")
    pm.unlock_shell_exec(p.id)
    refreshed = pm.get(p.id)
    assert refreshed.shell_exec_unlocked is True


def test_unlock_shell_exec_idempotent(pm):
    p = pm.create(name="DoubleAlice")
    pm.unlock_shell_exec(p.id)
    pm.unlock_shell_exec(p.id)
    assert pm.get(p.id).shell_exec_unlocked is True


def test_unlock_shell_exec_only_affects_target_profile(pm):
    a = pm.create(name="A")
    b = pm.create(name="B")
    pm.unlock_shell_exec(a.id)
    assert pm.get(a.id).shell_exec_unlocked is True
    assert pm.get(b.id).shell_exec_unlocked is False
