"""
Unit tests for cerebral/harness_channels.py -- Issue #299 / S16.
"""
from __future__ import annotations

import json

import pytest

from cerebral.harness_channels import HarnessChannelStore


CHANNELS = ["WhatsApp", "Telegram", "Discord", "Slack", "Teams"]


class _FakeKeyring:
    """Dict-backed stub matching the keyring lib's narrow contract."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture
def store(tmp_path):
    return HarnessChannelStore(
        channels=CHANNELS,
        path=tmp_path / "felix-harness.json",
        keyring_backend=_FakeKeyring(),
    )


def test_default_status_all_disabled_no_secret(store):
    snap = store.status()
    assert [c["name"] for c in snap] == CHANNELS
    assert all(c["enabled"] is False for c in snap)
    assert all(c["secret_set"] is False for c in snap)


def test_set_enabled_persists_and_reflects_in_status(tmp_path):
    path = tmp_path / "felix-harness.json"
    s = HarnessChannelStore(CHANNELS, path=path, keyring_backend=_FakeKeyring())
    s.set_enabled("Telegram", True)

    snap = {c["name"]: c["enabled"] for c in s.status()}
    assert snap["Telegram"] is True
    assert snap["Discord"] is False

    # Reload from disk -- enabled state survives a process restart.
    s2 = HarnessChannelStore(CHANNELS, path=path, keyring_backend=_FakeKeyring())
    assert s2.is_enabled("Telegram") is True
    assert s2.is_enabled("Discord") is False

    # JSON file payload is just the enabled list -- never secrets.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {"enabled_channels": ["Telegram"]}


def test_set_enabled_unknown_channel_raises(store):
    with pytest.raises(ValueError, match="Unknown channel"):
        store.set_enabled("Carrier Pigeon", True)


def test_set_secret_marks_secret_set_but_status_never_echoes_value(store):
    store.set_secret("Discord", "super-secret-bot-token")

    snap = {c["name"]: c for c in store.status()}
    assert snap["Discord"]["secret_set"] is True

    # The plaintext secret never appears anywhere in the status payload.
    payload = json.dumps(store.status())
    assert "super-secret-bot-token" not in payload


def test_set_secret_unknown_channel_raises(store):
    with pytest.raises(ValueError, match="Unknown channel"):
        store.set_secret("Carrier Pigeon", "tok")


def test_set_secret_empty_raises(store):
    with pytest.raises(ValueError, match="non-empty string"):
        store.set_secret("Telegram", "")


def test_clear_secret_flips_marker(store):
    store.set_secret("Slack", "abc")
    assert store.has_secret("Slack") is True
    store.clear_secret("Slack")
    assert store.has_secret("Slack") is False


def test_no_keyring_set_secret_raises_runtime_error(tmp_path):
    s = HarnessChannelStore(
        CHANNELS,
        path=tmp_path / "felix-harness.json",
        keyring_backend=None,
    )
    # Force the soft-import path to also report "unavailable" so this
    # test exercises the documented degraded mode.
    import cerebral.harness_channels as mod
    saved = mod._KEYRING_AVAILABLE
    mod._KEYRING_AVAILABLE = False
    try:
        with pytest.raises(RuntimeError, match="keyring not installed"):
            s.set_secret("Teams", "tok")
        assert s.has_secret("Teams") is False
    finally:
        mod._KEYRING_AVAILABLE = saved


def test_malformed_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "felix-harness.json"
    path.write_text("{not json", encoding="utf-8")
    s = HarnessChannelStore(CHANNELS, path=path, keyring_backend=_FakeKeyring())
    assert all(c["enabled"] is False for c in s.status())


# ── WS IPC tests (S16) ───────────────────────────────────────────────────────


@pytest.fixture
def harness_rig(tmp_path):
    """Rig that patches _harness_channels, OpenClaw lifecycle and
    _broadcast in cerebral.main."""
    import cerebral.main as main_mod

    store = HarnessChannelStore(
        channels=main_mod._HARNESS_CHANNELS,
        path=tmp_path / "felix-harness.json",
        keyring_backend=_FakeKeyring(),
    )

    sent: list[dict] = []
    daemon_state = {"running": False, "events": []}

    async def fake_start():
        daemon_state["events"].append("start")
        daemon_state["running"] = True

    async def fake_stop():
        daemon_state["events"].append("stop")
        daemon_state["running"] = False

    def fake_running():
        return daemon_state["running"]

    async def fake_broadcast(event):
        sent.append(event)

    saved = {
        "_harness_channels":          main_mod._harness_channels,
        "_broadcast":                 main_mod._broadcast,
        "_connected":                 main_mod._connected,
        "_start_openclaw_subscriber": main_mod._start_openclaw_subscriber,
        "_stop_openclaw_subscriber":  main_mod._stop_openclaw_subscriber,
        "_openclaw_subscriber_running": main_mod._openclaw_subscriber_running,
    }

    main_mod._harness_channels            = store
    main_mod._broadcast                   = fake_broadcast
    main_mod._connected                   = set()
    main_mod._start_openclaw_subscriber   = fake_start
    main_mod._stop_openclaw_subscriber    = fake_stop
    main_mod._openclaw_subscriber_running = fake_running

    class Rig:
        def __init__(self):
            self.store        = store
            self.sent         = sent
            self.daemon_state = daemon_state

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        def status_events(self):
            return [e for e in sent if e["type"] == "harness_status"]

        def last_status(self):
            return self.status_events()[-1]["data"]

    try:
        yield Rig()
    finally:
        for k, v in saved.items():
            setattr(main_mod, k, v)


async def test_start_openclaw_daemon_broadcasts_running(harness_rig):
    await harness_rig.handle({"type": "start_openclaw_daemon"})
    assert harness_rig.daemon_state["events"] == ["start"]
    assert harness_rig.last_status()["daemon_running"] is True


async def test_stop_openclaw_daemon_broadcasts_down(harness_rig):
    harness_rig.daemon_state["running"] = True
    await harness_rig.handle({"type": "stop_openclaw_daemon"})
    assert harness_rig.daemon_state["events"] == ["stop"]
    assert harness_rig.last_status()["daemon_running"] is False


async def test_restart_openclaw_daemon_does_stop_then_start(harness_rig):
    harness_rig.daemon_state["running"] = True
    await harness_rig.handle({"type": "restart_openclaw_daemon"})
    assert harness_rig.daemon_state["events"] == ["stop", "start"]
    assert harness_rig.last_status()["daemon_running"] is True


async def test_set_channel_enabled_persists_and_broadcasts(harness_rig):
    await harness_rig.handle({
        "type": "set_channel_enabled",
        "data": {"channel": "Telegram", "enabled": True},
    })
    assert harness_rig.store.is_enabled("Telegram") is True
    snap = {c["name"]: c for c in harness_rig.last_status()["channels"]}
    assert snap["Telegram"]["enabled"] is True
    assert snap["Discord"]["enabled"] is False


async def test_channel_without_secret_never_reads_connected(harness_rig):
    """A channel with no secret must not show 'connected' just because the
    shared daemon is running -- the user hasn't signed in yet."""
    harness_rig.daemon_state["running"] = True
    # Give one channel a secret; leave another without.
    await harness_rig.handle({
        "type": "set_channel_secret",
        "data": {"channel": "Discord", "secret": "tok"},
    })
    await harness_rig.handle({
        "type": "set_channel_enabled",
        "data": {"channel": "Discord", "enabled": True},
    })
    snap = {c["name"]: c for c in harness_rig.last_status()["channels"]}
    assert snap["Discord"]["state"] == "connected"      # secret + enabled + up
    assert snap["Telegram"]["state"] == "not signed in"  # no secret


async def test_set_channel_enabled_unknown_channel_no_broadcast(harness_rig):
    await harness_rig.handle({
        "type": "set_channel_enabled",
        "data": {"channel": "Carrier Pigeon", "enabled": True},
    })
    assert harness_rig.status_events() == []


async def test_set_channel_secret_marks_set_but_never_echoes(harness_rig):
    await harness_rig.handle({
        "type": "set_channel_secret",
        "data": {"channel": "Discord", "secret": "bot-token-xyz"},
    })

    # Status reflects "set" without echoing the value.
    snap = {c["name"]: c for c in harness_rig.last_status()["channels"]}
    assert snap["Discord"]["secret_set"] is True

    # The plaintext secret never appears in ANY broadcast event payload.
    full = json.dumps(harness_rig.sent)
    assert "bot-token-xyz" not in full


async def test_set_channel_secret_unknown_channel_no_broadcast(harness_rig):
    await harness_rig.handle({
        "type": "set_channel_secret",
        "data": {"channel": "Carrier Pigeon", "secret": "abc"},
    })
    assert harness_rig.status_events() == []


async def test_clear_channel_secret_resets_marker(harness_rig):
    await harness_rig.handle({
        "type": "set_channel_secret",
        "data": {"channel": "Slack", "secret": "abc"},
    })
    await harness_rig.handle({
        "type": "clear_channel_secret",
        "data": {"channel": "Slack"},
    })
    assert harness_rig.store.has_secret("Slack") is False
    snap = {c["name"]: c for c in harness_rig.last_status()["channels"]}
    assert snap["Slack"]["secret_set"] is False
