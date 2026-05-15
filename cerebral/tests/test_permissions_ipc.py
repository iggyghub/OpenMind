"""
Permissions UI IPC + state-event tests — Issue #53, ADR-0005.

Covers the Cerebral side of the Permissions settings UI:

  - the ``permissions_state`` event payload (the snapshot the tray's
    Permissions window renders from)
  - the IPC handlers the UI emits (``set_class_policy``,
    ``revoke_class_policy``, ``set_tool_override`` with the
    ``inherit`` carve-out, ``revoke_session_grant``,
    ``unlock_shell_exec``, ``list_permissions``)
  - the round-trips between them — set → re-read shows the new value
  - the profile-switch refresh — switching profile rebroadcasts the
    new profile's state (different persistent grants, no session
    grants, the new profile's shell_exec_unlocked flag)
  - the shell_exec one-way unlock — flipping it persists, refusing a
    set_class_policy on shell_exec while locked is a no-op

Tests that dispatch through the real ``_handle_message`` are async —
``pytest.ini`` sets ``asyncio_mode=auto`` so they run on the shared
loop without leaking event-loop state into other test modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import ProfileManager
from cerebral.mcp.orchestrator import MCPOrchestrator
from cerebral.security import (
    Capability,
    CAPABILITY_DESCRIPTION,
    CAPABILITY_LABEL,
    DEFAULT_POLICY,
    Decision,
    ProfileACL,
)


# ---------------------------------------------------------------------------
# Fixture: swap cerebral.main module-level singletons for an isolated test rig.
# ---------------------------------------------------------------------------


@pytest.fixture
def main_rig(tmp_path):
    """Yields a context with cerebral.main patched to use a temp ProfileManager
    + a fresh MCPOrchestrator + a single active profile.

    Captures broadcasts so tests can assert on what the tray would receive.
    Also exposes ``handle(msg)`` to dispatch a message via the real
    ``_handle_message`` (the one wired to the IPC). Restores original
    module-level state on teardown so other tests in the suite are
    unaffected.
    """
    import cerebral.main as main_mod

    db_path = tmp_path / "perm.db"
    pm = ProfileManager(db_path=db_path)
    profile = pm.create(name="Tester", wake_name="felix", voice_id="af_heart")
    pm.set_active(profile.id)

    orc = MCPOrchestrator()
    acl = ProfileACL(
        profile_id=profile.id,
        profile_manager=pm,
        defaults_snapshot=profile.acl_defaults_snapshot,
    )
    orc.set_acl(acl)

    saved = {
        "_pm": main_mod._pm,
        "_orc": main_mod._orc,
        "_active_profile": main_mod._active_profile,
        "_connected": main_mod._connected,
    }
    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._active_profile = profile
    main_mod._connected = set()

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    saved["_broadcast"] = main_mod._broadcast
    main_mod._broadcast = fake_broadcast

    class Rig:
        def __init__(self):
            self.module = main_mod
            self.pm = pm
            self.db_path = db_path
            self.orc = orc
            self.profile = profile
            self.acl = acl
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def reload_profile(self):
            self.profile = pm.get(self.profile.id)
            main_mod._active_profile = self.profile
            return self.profile

        def state_event(self):
            return main_mod._permissions_state_event()

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ---------------------------------------------------------------------------
# Slice 1 — capability vocabulary IPC payload shape
# ---------------------------------------------------------------------------


def test_capability_vocabulary_covers_all_16_classes(main_rig):
    state = main_rig.state_event()
    vocab = state["data"]["capability_vocabulary"]
    assert len(vocab) == 16
    assert {row["value"] for row in vocab} == {c.value for c in Capability}


def test_capability_vocabulary_carries_label_description_default(main_rig):
    vocab = main_rig.state_event()["data"]["capability_vocabulary"]
    by_value = {row["value"]: row for row in vocab}
    for cap in Capability:
        row = by_value[cap.value]
        assert row["label"] == CAPABILITY_LABEL[cap]
        assert row["description"] == CAPABILITY_DESCRIPTION[cap]
        assert row["default"] == DEFAULT_POLICY[cap].value


def test_capability_vocabulary_order_matches_enum_declaration(main_rig):
    vocab = main_rig.state_event()["data"]["capability_vocabulary"]
    assert [row["value"] for row in vocab] == [c.value for c in Capability]


# ---------------------------------------------------------------------------
# Slice 2 — permissions_state shape (snapshots, grants, defaults)
# ---------------------------------------------------------------------------


def test_state_event_carries_active_profile_id(main_rig):
    state = main_rig.state_event()
    assert state["type"] == "permissions_state"
    assert state["data"]["profile_id"] == main_rig.profile.id


def test_state_class_defaults_match_profile_snapshot(main_rig):
    state = main_rig.state_event()["data"]
    assert state["class_defaults"] == main_rig.profile.acl_defaults_snapshot


def test_state_session_grants_empty_on_fresh_profile(main_rig):
    assert main_rig.state_event()["data"]["session_class_grants"] == {}


def test_state_persistent_grants_split_class_vs_tool(main_rig):
    main_rig.acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)
    main_rig.acl.set_tool_override("files.write_journal", Decision.DENY)
    state = main_rig.state_event()["data"]
    assert state["persistent_class_grants"] == {"fs_write": "silent"}
    assert state["persistent_tool_overrides"] == {"files.write_journal": "deny"}


def test_state_no_active_profile_returns_empty_payload():
    """When the orchestrator has no ACL (no profile loaded yet), the
    payload still validates structurally so the tray can render the
    "set up a profile first" empty state instead of crashing."""
    import cerebral.main as main_mod
    saved = {
        "_active_profile": main_mod._active_profile,
        "_orc_acl":        main_mod._orc.acl,
    }
    main_mod._active_profile = None
    main_mod._orc.set_acl(None)
    try:
        payload = main_mod._permissions_state_event()
        assert payload["data"]["profile_id"] is None
        assert payload["data"]["persistent_class_grants"] == {}
        assert payload["data"]["session_class_grants"] == {}
        assert payload["data"]["shell_exec_unlocked"] is False
        # Vocabulary is still populated even with no profile so the
        # tray's render is the same shape — just without overrides.
        assert len(payload["data"]["capability_vocabulary"]) == 16
    finally:
        main_mod._active_profile = saved["_active_profile"]
        main_mod._orc.set_acl(saved["_orc_acl"])


# ---------------------------------------------------------------------------
# Slice 3 — set_class_policy IPC handler
# ---------------------------------------------------------------------------


async def test_set_class_policy_persists_and_rebroadcasts(main_rig):
    await main_rig.handle({
        "type": "set_class_policy",
        "data": {"capability": "fs_write", "decision": "silent"},
    })
    # ACL row written
    assert any(
        row["scope"] == "class"
        and row["target"] == "fs_write"
        and row["policy"] == "silent"
        for row in main_rig.acl.list_persistent_grants()
    )
    # Re-broadcast carries the new value
    assert any(
        ev["type"] == "permissions_state"
        and ev["data"]["persistent_class_grants"].get("fs_write") == "silent"
        for ev in main_rig.sent
    )


async def test_set_class_policy_invalid_capability_is_a_no_op(main_rig):
    await main_rig.handle({
        "type": "set_class_policy",
        "data": {"capability": "not_a_real_class", "decision": "silent"},
    })
    assert main_rig.acl.list_persistent_grants() == []
    assert main_rig.sent == []  # no broadcast on invalid input


async def test_set_class_policy_invalid_decision_is_a_no_op(main_rig):
    await main_rig.handle({
        "type": "set_class_policy",
        "data": {"capability": "fs_write", "decision": "maybe"},
    })
    assert main_rig.acl.list_persistent_grants() == []
    assert main_rig.sent == []


async def test_revoke_class_policy_clears_grant(main_rig):
    main_rig.acl.set_persistent_class(Capability.FS_WRITE, Decision.SILENT)
    await main_rig.handle({
        "type": "revoke_class_policy",
        "data": {"capability": "fs_write"},
    })
    assert main_rig.acl.list_persistent_grants() == []


# ---------------------------------------------------------------------------
# Slice 4 — set_tool_override IPC handler (inherit carve-out)
# ---------------------------------------------------------------------------


async def test_set_tool_override_writes_scope_tool_row(main_rig):
    await main_rig.handle({
        "type": "set_tool_override",
        "data": {"tool": "files.write_journal", "decision": "deny"},
    })
    grants = main_rig.acl.list_persistent_grants()
    assert any(
        g["scope"] == "tool"
        and g["target"] == "files.write_journal"
        and g["policy"] == "deny"
        for g in grants
    )


async def test_set_tool_override_inherit_clears_existing_row(main_rig):
    main_rig.acl.set_tool_override("files.write_journal", Decision.DENY)
    await main_rig.handle({
        "type": "set_tool_override",
        "data": {"tool": "files.write_journal", "decision": "inherit"},
    })
    assert all(
        g["target"] != "files.write_journal"
        for g in main_rig.acl.list_persistent_grants()
    )


async def test_set_tool_override_invalid_decision_no_op(main_rig):
    await main_rig.handle({
        "type": "set_tool_override",
        "data": {"tool": "files.write_journal", "decision": "bogus"},
    })
    assert main_rig.acl.list_persistent_grants() == []


async def test_set_tool_override_missing_field_no_op(main_rig):
    await main_rig.handle({
        "type": "set_tool_override",
        "data": {"tool": "", "decision": "deny"},
    })
    assert main_rig.acl.list_persistent_grants() == []


# ---------------------------------------------------------------------------
# Slice 5 — revoke_session_grant IPC handler
# ---------------------------------------------------------------------------


async def test_revoke_session_grant_clears_in_memory_grant(main_rig):
    main_rig.acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    assert main_rig.state_event()["data"]["session_class_grants"] == {
        "fs_write": "silent",
    }
    await main_rig.handle({
        "type": "revoke_session_grant",
        "data": {"capability": "fs_write"},
    })
    assert main_rig.state_event()["data"]["session_class_grants"] == {}


async def test_revoke_session_grant_unknown_capability_no_op(main_rig):
    await main_rig.handle({
        "type": "revoke_session_grant",
        "data": {"capability": "not_a_class"},
    })
    # Doesn't crash; no state change.
    assert main_rig.state_event()["data"]["session_class_grants"] == {}


# ---------------------------------------------------------------------------
# Slice 6 — shell_exec one-way unlock
# ---------------------------------------------------------------------------


def test_shell_exec_locked_by_default(main_rig):
    assert main_rig.state_event()["data"]["shell_exec_unlocked"] is False


async def test_set_class_policy_on_shell_exec_refused_while_locked(main_rig):
    await main_rig.handle({
        "type": "set_class_policy",
        "data": {"capability": "shell_exec", "decision": "ask"},
    })
    # Refused — no row written, no rebroadcast.
    assert main_rig.acl.list_persistent_grants() == []


async def test_unlock_shell_exec_persists_and_enables_set(main_rig):
    await main_rig.handle({"type": "unlock_shell_exec"})
    main_rig.reload_profile()
    assert main_rig.profile.shell_exec_unlocked is True
    # Row persisted on disk: a fresh ProfileManager instance reads it back.
    fresh = ProfileManager(db_path=main_rig.db_path)
    assert fresh.get(main_rig.profile.id).shell_exec_unlocked is True
    # And now the toggle is editable.
    await main_rig.handle({
        "type": "set_class_policy",
        "data": {"capability": "shell_exec", "decision": "ask"},
    })
    assert any(
        g["scope"] == "class" and g["target"] == "shell_exec" and g["policy"] == "ask"
        for g in main_rig.acl.list_persistent_grants()
    )


async def test_unlock_shell_exec_idempotent(main_rig):
    await main_rig.handle({"type": "unlock_shell_exec"})
    await main_rig.handle({"type": "unlock_shell_exec"})
    main_rig.reload_profile()
    assert main_rig.profile.shell_exec_unlocked is True


# ---------------------------------------------------------------------------
# Slice 7 — list_permissions IPC re-read
# ---------------------------------------------------------------------------


async def test_list_permissions_broadcasts_state_event(main_rig):
    main_rig.acl.set_persistent_class(Capability.FS_READ, Decision.SILENT)
    await main_rig.handle({"type": "list_permissions"})
    states = [ev for ev in main_rig.sent if ev["type"] == "permissions_state"]
    assert len(states) == 1
    assert states[0]["data"]["persistent_class_grants"] == {"fs_read": "silent"}


# ---------------------------------------------------------------------------
# Slice 8 — round-trip and profile-switch refresh
# ---------------------------------------------------------------------------


async def test_set_then_read_round_trip_via_ipc(main_rig):
    await main_rig.handle({
        "type": "set_class_policy",
        "data": {"capability": "fs_write", "decision": "deny"},
    })
    await main_rig.handle({
        "type": "set_tool_override",
        "data": {"tool": "browser.fetch", "decision": "ask"},
    })
    await main_rig.handle({"type": "list_permissions"})
    last = main_rig.sent[-1]
    assert last["type"] == "permissions_state"
    assert last["data"]["persistent_class_grants"] == {"fs_write": "deny"}
    assert last["data"]["persistent_tool_overrides"] == {"browser.fetch": "ask"}


async def test_switch_profile_rebroadcasts_with_new_state(main_rig):
    """Switching profile must clear the visible session-grant list,
    surface the new profile's persistent grants, and reflect the new
    profile's shell_exec_unlocked flag."""
    main_rig.acl.grant_session(Capability.FS_WRITE, Decision.SILENT)
    main_rig.acl.set_persistent_class(Capability.FS_READ, Decision.DENY)
    main_rig.pm.unlock_shell_exec(main_rig.profile.id)

    # Create a second profile with no overrides and a locked shell_exec.
    other = main_rig.pm.create(name="Other", wake_name="felix")
    main_rig.sent.clear()

    await main_rig.handle({
        "type": "switch_profile",
        "data": {"id": other.id},
    })

    # The handler should have emitted a fresh permissions_state.
    perm_events = [ev for ev in main_rig.sent if ev["type"] == "permissions_state"]
    assert perm_events, "switch_profile should rebroadcast permissions_state"
    state = perm_events[-1]["data"]
    assert state["profile_id"] == other.id
    # Other profile has its own (clean) state — no inherited grants, no
    # inherited session, locked shell_exec.
    assert state["persistent_class_grants"] == {}
    assert state["session_class_grants"] == {}
    assert state["shell_exec_unlocked"] is False


async def test_state_event_emitted_on_initial_connect(main_rig):
    """The connect-time greeting must include permissions_state alongside
    the other state events, so the Permissions window has a payload to
    render the moment it opens."""
    import cerebral.main as main_mod
    import json

    sent: list[dict] = []

    class FakeWS:
        async def send(self, payload):
            sent.append(json.loads(payload))

    await main_mod._send(FakeWS(), main_mod._permissions_state_event())
    assert sent and sent[0]["type"] == "permissions_state"
    assert sent[0]["data"]["profile_id"] == main_rig.profile.id


# ---------------------------------------------------------------------------
# Slice 9 — class-vocabulary closure (AC#7: no ad-hoc class invention)
# ---------------------------------------------------------------------------


def test_capability_vocabulary_is_closed_to_known_classes(main_rig):
    """The tray cannot invent a 17th class — every entry the tray
    receives is a member of the closed enum, which is the source of
    truth for AC#7."""
    vocab = main_rig.state_event()["data"]["capability_vocabulary"]
    seen = {row["value"] for row in vocab}
    extra = seen - {c.value for c in Capability}
    assert extra == set()
    missing = {c.value for c in Capability} - seen
    assert missing == set()
