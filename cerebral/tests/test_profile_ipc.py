"""
create_profile IPC guard tests -- Issue #387.

Twice in 24h the tray re-fired ``create_profile`` for a profile that
already existed (name+wake_name match) while a healthy profile was
active, silently activating a fresh empty-transcript duplicate. This
covers the Cerebral-side defence in depth added in main.py's
``create_profile`` handler:

  - a duplicate name+wake_name is refused: no new row, active profile
    untouched, a ``create_profile_error`` broadcast the tray can show
  - the same call with ``force: true`` creates the second profile as
    normal (the deliberate escape hatch)
  - first-run creation (no existing profiles) is unaffected -- the dup
    check is naturally a no-op against an empty profile list

Style follows ``test_permissions_ipc.py``'s ``main_rig`` fixture:
swap ``cerebral.main`` module-level singletons for a temp
ProfileManager + captured broadcasts, dispatch via the real
``_handle_message``.

``pytest.ini`` sets ``asyncio_mode=auto`` -- async def test bodies run
on the shared loop; never call asyncio.run inside one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.profiles import ProfileManager
from cerebral.mcp.orchestrator import MCPOrchestrator
from cerebral.security import ProfileACL


@pytest.fixture
def main_rig(tmp_path):
    """One pre-existing active profile ("Tester"/"felix"), like a normal
    running Cerebral. Mirrors test_permissions_ipc.py's rig."""
    import cerebral.main as main_mod

    db_path = tmp_path / "profiles.db"
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
            self.profile = profile
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


@pytest.fixture
def first_run_rig(tmp_path):
    """No profiles at all yet -- the state a fresh install boots into."""
    import cerebral.main as main_mod

    db_path = tmp_path / "profiles.db"
    pm = ProfileManager(db_path=db_path)
    orc = MCPOrchestrator()

    saved = {
        "_pm": main_mod._pm,
        "_orc": main_mod._orc,
        "_active_profile": main_mod._active_profile,
        "_connected": main_mod._connected,
    }
    main_mod._pm = pm
    main_mod._orc = orc
    main_mod._active_profile = None
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
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ---------------------------------------------------------------------------
# Duplicate refused
# ---------------------------------------------------------------------------


async def test_duplicate_create_profile_refused(main_rig):
    await main_rig.handle({
        "type": "create_profile",
        "data": {"name": "Tester", "wake_name": "felix"},
    })
    assert main_rig.pm.list_all() == [main_rig.profile]


async def test_duplicate_create_profile_does_not_switch_active(main_rig):
    await main_rig.handle({
        "type": "create_profile",
        "data": {"name": "Tester", "wake_name": "felix"},
    })
    assert main_rig.module._active_profile.id == main_rig.profile.id
    assert main_rig.pm.get_active().id == main_rig.profile.id


async def test_duplicate_create_profile_returns_clear_error(main_rig):
    await main_rig.handle({
        "type": "create_profile",
        "data": {"name": "Tester", "wake_name": "felix"},
    })
    errors = [e for e in main_rig.sent if e["type"] == "create_profile_error"]
    assert len(errors) == 1
    assert "Tester" in errors[0]["data"]["error"]
    assert errors[0]["data"]["existing_profile_id"] == main_rig.profile.id
    # And no profile_loaded / profiles_list churn from a phantom create.
    assert not any(e["type"] == "profile_loaded" for e in main_rig.sent)


# ---------------------------------------------------------------------------
# force flag creates the intentional second profile
# ---------------------------------------------------------------------------


async def test_duplicate_create_profile_with_force_creates(main_rig):
    await main_rig.handle({
        "type": "create_profile",
        "data": {"name": "Tester", "wake_name": "felix", "force": True},
    })
    profiles = main_rig.pm.list_all()
    assert len(profiles) == 2
    assert main_rig.module._active_profile.name == "Tester"
    assert main_rig.module._active_profile.id != main_rig.profile.id


# ---------------------------------------------------------------------------
# first-run creation is unaffected
# ---------------------------------------------------------------------------


async def test_first_run_create_profile_unaffected(first_run_rig):
    await first_run_rig.handle({
        "type": "create_profile",
        "data": {"name": "Iggy", "wake_name": "felix"},
    })
    profiles = first_run_rig.pm.list_all()
    assert len(profiles) == 1
    assert profiles[0].name == "Iggy"
    assert first_run_rig.module._active_profile.id == profiles[0].id
    assert not any(e["type"] == "create_profile_error" for e in first_run_rig.sent)
