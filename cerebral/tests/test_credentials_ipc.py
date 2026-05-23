"""
Credentials tray IPC tests — Issue #114, ADR-0005.

Exercises the WebSocket dispatcher branches behind the Credentials window:
``list_credentials`` / ``set_credential_client`` / ``connect_google`` /
``disconnect_credential`` and the ``credentials_state`` snapshot they
broadcast. The handlers operate on the **active profile** only, read
status from the #112 ``CredentialStore`` (metadata only — never a secret)
and drive #113's ``GoogleOAuthFlow.start_consent`` for the connect action.

No real browser / socket / network / OS keyring: the #112 store is a
:memory: instance with a dict-backed keyring stub and the #113 flow is a
stub injected through the same module-factory seam ``_get_memory`` uses
(``_get_credential_store`` / ``_get_oauth_flow``). Tests are ``async``
(pytest.ini asyncio_mode=auto) — never ``asyncio.run`` in a sync body
(learning #7); the background consent task is drained explicitly.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.credentials import CredentialStore
from cerebral.db.google_oauth import GoogleOAuthError


# ── dict-backed keyring stub (the #112 _cs() rig shape) ───────────────────────

class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


# ── stub #113 OAuth flow ──────────────────────────────────────────────────────

class StubFlow:
    """Stands in for GoogleOAuthFlow. ``start_consent`` either records the
    call and writes the connected metadata the real flow would, or raises
    the supplied exception (persisting nothing). Synchronous, fast — runs
    inside asyncio.to_thread exactly as the real blocking flow does."""

    def __init__(self, store: CredentialStore, *, exc: Exception | None = None):
        self._store = store
        self._exc = exc
        self.calls: list[dict] = []

    def start_consent(self, profile_id: int, *, scopes: list[str]) -> dict:
        self.calls.append({"profile_id": profile_id, "scopes": scopes})
        if self._exc is not None:
            raise self._exc
        existing = self._store.get_credential(profile_id, "google") or {}
        self._store.set_secret(profile_id, "google", "refresh_token", "rt")
        self._store.set_secret(profile_id, "google", "access_token", "at")
        self._store.set_credential(
            profile_id, "google",
            client_id=existing.get("client_id", ""),
            email="user@example.com",
            scopes=scopes, status="connected",
        )
        return {"status": "connected", "profile_id": profile_id, "scopes": scopes}


class _Profile:
    def __init__(self, pid: int) -> None:
        self.id = pid


@pytest.fixture
def cred_rig():
    import cerebral.main as main_mod

    store = CredentialStore(db_path=":memory:", keyring_backend=FakeKeyring())
    flow = StubFlow(store)
    profile = _Profile(1)

    saved = {
        "_active_profile": main_mod._active_profile,
        "_broadcast": main_mod._broadcast,
        "_connected": main_mod._connected,
        "_get_credential_store": main_mod._get_credential_store,
        "_get_oauth_flow": main_mod._get_oauth_flow,
    }

    sent: list[dict] = []

    async def fake_broadcast(event):
        sent.append(event)

    main_mod._active_profile = profile
    main_mod._broadcast = fake_broadcast
    main_mod._connected = set()
    main_mod._get_credential_store = lambda: store
    main_mod._get_oauth_flow = lambda s: flow

    class Rig:
        def __init__(self):
            self.module = main_mod
            self.store = store
            self.flow = flow
            self.profile = profile
            self.sent = sent

        async def handle(self, msg):
            await main_mod._handle_message(msg)

        async def drain(self):
            """Await the background _run_consent task (connect_google)."""
            pending = [
                t for t in asyncio.all_tasks()
                if t is not asyncio.current_task()
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        def states(self):
            return [e for e in sent if e["type"] == "credentials_state"]

        def last_google(self):
            return self.states()[-1]["data"]["google"]

        def no_profile(self):
            main_mod._active_profile = None

        def set_flow_error(self, exc):
            main_mod._get_oauth_flow = lambda s: StubFlow(store, exc=exc)

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── list_credentials (status read) ────────────────────────────────────────────

async def test_status_not_configured_when_empty(cred_rig):
    await cred_rig.handle({"type": "list_credentials"})
    assert cred_rig.last_google()["status"] == "not configured"


async def test_status_client_set_after_client_written(cred_rig):
    cred_rig.store.set_credential(1, "google", client_id="cid", status="client set")
    await cred_rig.handle({"type": "list_credentials"})
    g = cred_rig.last_google()
    assert g["status"] == "client set"
    assert g["client_id"] == "cid"


async def test_status_connected_carries_email(cred_rig):
    cred_rig.store.set_credential(
        1, "google", client_id="cid", email="me@x.com", status="connected"
    )
    await cred_rig.handle({"type": "list_credentials"})
    g = cred_rig.last_google()
    assert g["status"] == "connected"
    assert g["email"] == "me@x.com"


async def test_status_empty_when_no_profile(cred_rig):
    cred_rig.no_profile()
    await cred_rig.handle({"type": "list_credentials"})
    data = cred_rig.states()[-1]["data"]
    assert data["profile_id"] is None
    assert data["google"]["status"] == "not configured"


async def test_status_payload_never_carries_secret(cred_rig):
    cred_rig.store.set_credential(1, "google", client_id="cid", status="client set")
    cred_rig.store.set_secret(1, "google", "client_secret", "SECRET")
    await cred_rig.handle({"type": "list_credentials"})
    blob = repr(cred_rig.states()[-1])
    assert "SECRET" not in blob


# ── set_credential_client ─────────────────────────────────────────────────────

async def test_set_client_persists_id_metadata_and_secret_keyring(cred_rig):
    await cred_rig.handle({
        "type": "set_credential_client",
        "data": {"client_id": "cid-1", "client_secret": "shh"},
    })
    meta = cred_rig.store.get_credential(1, "google")
    assert meta["client_id"] == "cid-1"
    assert meta["status"] == "client set"
    assert cred_rig.store.get_secret(1, "google", "client_secret") == "shh"
    assert cred_rig.last_google()["status"] == "client set"


async def test_set_client_secret_never_broadcast(cred_rig):
    await cred_rig.handle({
        "type": "set_credential_client",
        "data": {"client_id": "cid-1", "client_secret": "topsecret"},
    })
    assert "topsecret" not in repr(cred_rig.sent)


async def test_set_client_resets_prior_connected_row(cred_rig):
    # A pre-existing connected account; entering a NEW client must not let
    # the #112 upsert silently keep the stale email/scopes/status (the
    # blanking-trap pin — status is passed explicitly as "client set").
    cred_rig.store.set_credential(
        1, "google", client_id="old", email="old@x.com",
        scopes=["s1"], status="connected",
    )
    await cred_rig.handle({
        "type": "set_credential_client",
        "data": {"client_id": "new", "client_secret": "k"},
    })
    meta = cred_rig.store.get_credential(1, "google")
    assert meta["client_id"] == "new"
    assert meta["status"] == "client set"
    assert meta["email"] == ""
    assert meta["scopes"] == []


async def test_set_client_missing_fields_no_op(cred_rig):
    await cred_rig.handle({
        "type": "set_credential_client",
        "data": {"client_id": "only-id", "client_secret": ""},
    })
    assert cred_rig.store.get_credential(1, "google") is None
    assert cred_rig.states() == []


async def test_set_client_no_profile_no_op(cred_rig):
    cred_rig.no_profile()
    await cred_rig.handle({
        "type": "set_credential_client",
        "data": {"client_id": "cid", "client_secret": "s"},
    })
    assert cred_rig.store.get_credential(1, "google") is None


# ── connect_google ────────────────────────────────────────────────────────────

async def test_connect_broadcasts_connecting_then_connected(cred_rig):
    cred_rig.store.set_credential(1, "google", client_id="cid", status="client set")
    cred_rig.store.set_secret(1, "google", "client_secret", "k")
    await cred_rig.handle({"type": "connect_google"})
    # Interim status emitted synchronously before the background task.
    assert cred_rig.states()[0]["data"]["google"]["status"] == "connecting"
    await cred_rig.drain()
    assert cred_rig.flow.calls == [
        {"profile_id": 1, "scopes": cred_rig.module._GOOGLE_SCOPES}
    ]
    assert cred_rig.last_google()["status"] == "connected"
    assert cred_rig.last_google()["email"] == "user@example.com"


async def test_connect_failure_broadcasts_error(cred_rig):
    cred_rig.store.set_credential(1, "google", client_id="cid", status="client set")
    cred_rig.set_flow_error(GoogleOAuthError("consent failed: state mismatch"))
    await cred_rig.handle({"type": "connect_google"})
    await cred_rig.drain()
    g = cred_rig.last_google()
    assert g["status"] == "error"
    assert "state mismatch" in g["detail"]


async def test_connect_no_profile_no_op(cred_rig):
    cred_rig.no_profile()
    await cred_rig.handle({"type": "connect_google"})
    await cred_rig.drain()
    assert cred_rig.flow.calls == []


# ── disconnect_credential ─────────────────────────────────────────────────────

async def test_disconnect_clears_metadata_and_secrets(cred_rig):
    cred_rig.store.set_credential(
        1, "google", client_id="cid", email="me@x.com", status="connected"
    )
    cred_rig.store.set_secret(1, "google", "refresh_token", "rt")
    await cred_rig.handle({"type": "disconnect_credential"})
    assert cred_rig.store.get_credential(1, "google") is None
    assert cred_rig.store.get_secret(1, "google", "refresh_token") is None
    assert cred_rig.last_google()["status"] == "not configured"


async def test_disconnect_idempotent_on_empty(cred_rig):
    await cred_rig.handle({"type": "disconnect_credential"})
    assert cred_rig.last_google()["status"] == "not configured"


async def test_disconnect_no_profile_no_op(cred_rig):
    cred_rig.store.set_credential(1, "google", client_id="cid", status="client set")
    cred_rig.no_profile()
    await cred_rig.handle({"type": "disconnect_credential"})
    # Untouched — the handler bailed before delete.
    assert cred_rig.store.get_credential(1, "google") is not None


# ── Issue #148 — static-token state in the credentials_state payload ─────────

def _last_static(cred_rig):
    return cred_rig.states()[-1]["data"]["static_tokens"]


@pytest.mark.parametrize("provider", [
    "youtube", "todoist", "notion", "toggl", "clockify",
])
async def test_static_tokens_payload_per_provider_shape(cred_rig, monkeypatch, provider):
    """Each provider's entry has exactly {status, source}; value is NEVER
    present. The five providers are in canonical render order."""
    # Clear every env var to isolate the no-source case.
    for _, env_var in cred_rig.module._STATIC_TOKEN_PROVIDERS:
        monkeypatch.delenv(env_var, raising=False)
    await cred_rig.handle({"type": "list_credentials"})
    static = _last_static(cred_rig)
    assert list(static.keys()) == [
        p for p, _ in cred_rig.module._STATIC_TOKEN_PROVIDERS
    ]
    entry = static[provider]
    assert set(entry.keys()) == {"status", "source"}
    assert entry["status"] == "not configured"
    assert entry["source"] == "none"


async def test_static_token_source_keyring_when_only_keyring(cred_rig, monkeypatch):
    monkeypatch.delenv("TODOIST_API_TOKEN", raising=False)
    cred_rig.store.set_secret(1, "todoist", "api_token", "k-from-keyring")
    await cred_rig.handle({"type": "list_credentials"})
    e = _last_static(cred_rig)["todoist"]
    assert e == {"status": "connected", "source": "keyring"}


async def test_static_token_source_env_when_only_env(cred_rig, monkeypatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "k-from-env")
    await cred_rig.handle({"type": "list_credentials"})
    e = _last_static(cred_rig)["notion"]
    assert e == {"status": "connected", "source": "env"}


async def test_static_token_keyring_wins_when_both_set(cred_rig, monkeypatch):
    monkeypatch.setenv("CLOCKIFY_API_KEY", "from-env")
    cred_rig.store.set_secret(1, "clockify", "api_token", "from-keyring")
    await cred_rig.handle({"type": "list_credentials"})
    e = _last_static(cred_rig)["clockify"]
    assert e["source"] == "keyring"
    assert e["status"] == "connected"


async def test_static_tokens_empty_when_no_profile(cred_rig):
    cred_rig.no_profile()
    await cred_rig.handle({"type": "list_credentials"})
    data = cred_rig.states()[-1]["data"]
    assert data["static_tokens"] == {}


async def test_static_token_payload_never_carries_value(cred_rig, monkeypatch):
    """Write-only contract: a real token in keyring or env is never
    surfaced in the state event."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "ENV-KEY-MUST-NOT-LEAK")
    cred_rig.store.set_secret(1, "toggl", "api_token", "KEYRING-KEY-MUST-NOT-LEAK")
    await cred_rig.handle({"type": "list_credentials"})
    blob = repr(cred_rig.states()[-1])
    assert "ENV-KEY-MUST-NOT-LEAK" not in blob
    assert "KEYRING-KEY-MUST-NOT-LEAK" not in blob


# ── Issue #148 — set_static_token ─────────────────────────────────────────────

async def test_set_static_token_persists_to_keyring(cred_rig):
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": "todoist", "value": "todoist-key"},
    })
    assert cred_rig.store.get_secret(1, "todoist", "api_token") == "todoist-key"


async def test_set_static_token_writes_status_metadata(cred_rig):
    """A degenerate metadata row anchors the per-profile FK cascade so a
    profile delete wipes the keyring entry too (#112 invariant)."""
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": "notion", "value": "k"},
    })
    meta = cred_rig.store.get_credential(1, "notion")
    assert meta["status"] == "connected"
    assert meta["client_id"] == ""
    assert meta["email"] == ""
    assert meta["scopes"] == []


async def test_set_static_token_broadcasts_state(cred_rig, monkeypatch):
    for _, ev in cred_rig.module._STATIC_TOKEN_PROVIDERS:
        monkeypatch.delenv(ev, raising=False)
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": "toggl", "value": "toggl-key"},
    })
    assert _last_static(cred_rig)["toggl"] == {
        "status": "connected", "source": "keyring",
    }


async def test_set_static_token_value_never_broadcast(cred_rig):
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": "clockify", "value": "WRITE-ONLY-VALUE"},
    })
    assert "WRITE-ONLY-VALUE" not in repr(cred_rig.sent)


async def test_set_static_token_value_never_logged(cred_rig, caplog):
    with caplog.at_level("INFO"):
        await cred_rig.handle({
            "type": "set_static_token",
            "data": {"provider": "youtube", "value": "LOG-FORBIDDEN-VALUE"},
        })
    assert "LOG-FORBIDDEN-VALUE" not in caplog.text


async def test_set_static_token_unknown_provider_no_op(cred_rig):
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": "googol", "value": "k"},
    })
    assert cred_rig.store.get_secret(1, "googol", "api_token") is None
    assert cred_rig.states() == []


async def test_set_static_token_empty_value_no_op(cred_rig):
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": "todoist", "value": "   "},
    })
    assert cred_rig.store.get_secret(1, "todoist", "api_token") is None
    assert cred_rig.states() == []


async def test_set_static_token_no_profile_no_op(cred_rig):
    cred_rig.no_profile()
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": "todoist", "value": "k"},
    })
    assert cred_rig.store.get_secret(1, "todoist", "api_token") is None


@pytest.mark.parametrize("provider", [
    "youtube", "todoist", "notion", "toggl", "clockify",
])
async def test_set_static_token_each_provider(cred_rig, provider):
    await cred_rig.handle({
        "type": "set_static_token",
        "data": {"provider": provider, "value": f"k-{provider}"},
    })
    assert cred_rig.store.get_secret(1, provider, "api_token") == f"k-{provider}"
    assert cred_rig.store.get_credential(1, provider)["status"] == "connected"


# ── Issue #148 — clear_static_token ───────────────────────────────────────────

async def test_clear_static_token_deletes_keyring_and_metadata(cred_rig):
    cred_rig.store.set_secret(1, "notion", "api_token", "k")
    cred_rig.store.set_credential(1, "notion", status="connected")
    await cred_rig.handle({
        "type": "clear_static_token",
        "data": {"provider": "notion"},
    })
    assert cred_rig.store.get_secret(1, "notion", "api_token") is None
    assert cred_rig.store.get_credential(1, "notion") is None


async def test_clear_static_token_idempotent(cred_rig):
    await cred_rig.handle({
        "type": "clear_static_token",
        "data": {"provider": "toggl"},
    })
    await cred_rig.handle({
        "type": "clear_static_token",
        "data": {"provider": "toggl"},
    })
    # Two state broadcasts, no exception.
    assert len(cred_rig.states()) == 2


async def test_clear_static_token_unknown_provider_no_op(cred_rig):
    cred_rig.store.set_secret(1, "todoist", "api_token", "k")
    await cred_rig.handle({
        "type": "clear_static_token",
        "data": {"provider": "googol"},
    })
    assert cred_rig.store.get_secret(1, "todoist", "api_token") == "k"
    assert cred_rig.states() == []


async def test_clear_static_token_no_profile_no_op(cred_rig):
    cred_rig.store.set_secret(1, "todoist", "api_token", "k")
    cred_rig.no_profile()
    await cred_rig.handle({
        "type": "clear_static_token",
        "data": {"provider": "todoist"},
    })
    assert cred_rig.store.get_secret(1, "todoist", "api_token") == "k"


async def test_clear_static_token_leaves_other_providers_intact(cred_rig):
    cred_rig.store.set_secret(1, "youtube", "api_token", "y-key")
    cred_rig.store.set_secret(1, "todoist", "api_token", "t-key")
    await cred_rig.handle({
        "type": "clear_static_token",
        "data": {"provider": "youtube"},
    })
    assert cred_rig.store.get_secret(1, "youtube", "api_token") is None
    assert cred_rig.store.get_secret(1, "todoist", "api_token") == "t-key"
