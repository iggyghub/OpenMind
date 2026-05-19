"""
GoogleOAuthFlow tests — Issue #113, ADR-0005.

Hand-rolled installed-app OAuth2 flow. Unit tests inject the #112
CredentialStore (in-memory SQLite + dict keyring stub) plus a stub HTTP
transport, a no-op browser opener and a fake loopback redirect handler —
no real browser, socket, network or OS keyring.

Slices:
  1.  auth URL is correct (scopes/PKCE-S256/state/redirect/offline/consent)
  2.  happy-path consent persists refresh_token+access_token+metadata
  3.  consent preserves the precondition client_id (no #112 upsert blanking)
  4.  refresh_access_token reads refresh_token → updates access_token
  5.  consent: user denies → GoogleOAuthError, nothing persisted
  6.  consent: token-exchange transport error → error, nothing persisted
  7.  consent: missing client_id/client_secret → error, nothing persisted
  8.  consent: state mismatch → error, nothing persisted
  9.  consent: exchange returns no refresh/access token → error, nothing
  10. refresh: no refresh_token stored → error
  11. refresh: missing client creds → error
  12. per-profile isolation (profile X consent never touches Y)
  13. secret material (client_secret/refresh/access) is never logged
"""
import logging

import pytest

from cerebral.db.credentials import CredentialStore
from cerebral.db.google_oauth import GoogleOAuthError, GoogleOAuthFlow


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


# ── stub transports ──────────────────────────────────────────────────────────

class StubFetch:
    """Records token-endpoint calls; returns a canned body or raises."""

    def __init__(self, body: dict | None = None, exc: Exception | None = None):
        self.body = body if body is not None else {}
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, url, *, method="GET", data=None, headers=None) -> dict:
        self.calls.append(
            {"url": url, "method": method, "data": data, "headers": headers}
        )
        if self.exc is not None:
            raise self.exc
        return self.body


class FakeRedirect:
    """Stands in for the loopback listener. Exercises build_auth_url +
    browser_opener (so those code paths are covered) then returns a
    scripted result without binding a socket."""

    def __init__(self, result_kind="ok", *, state_override=None):
        self.kind = result_kind
        self.state_override = state_override
        self.auth_url: str | None = None
        self.redirect_uri = "http://localhost:9999/"

    def __call__(self, build_auth_url, *, state, browser_opener, timeout):
        self.auth_url = build_auth_url(self.redirect_uri)
        browser_opener(self.auth_url)
        if self.kind == "deny":
            return {"error": "access_denied"}
        return {
            "code": "AUTH_CODE",
            "state": self.state_override if self.state_override else state,
            "redirect_uri": self.redirect_uri,
        }


def _store() -> tuple[CredentialStore, FakeKeyring]:
    kr = FakeKeyring()
    return CredentialStore(db_path=":memory:", keyring_backend=kr), kr


def _seed_client(store: CredentialStore, pid: int) -> None:
    store.set_credential(pid, "google", client_id="CID-123", email="me@x.com")
    store.set_secret(pid, "google", "client_secret", "CSECRET")


def _flow(store, fetch, redirect):
    opened: list[str] = []
    flow = GoogleOAuthFlow(
        store,
        fetch_fn=fetch,
        browser_opener=opened.append,
        redirect_handler=redirect,
    )
    return flow, opened


# ── 1. auth URL correctness ───────────────────────────────────────────────────

def test_auth_url_has_pkce_state_scopes_and_installed_app_params():
    import urllib.parse

    store, _ = _store()
    _seed_client(store, 1)
    fetch = StubFetch({"refresh_token": "R", "access_token": "A"})
    redirect = FakeRedirect("ok")
    flow, opened = _flow(store, fetch, redirect)

    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    flow.start_consent(1, scopes=scopes)

    assert redirect.auth_url == opened[0]  # browser opened at the auth URL
    parsed = urllib.parse.urlparse(redirect.auth_url)
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "https" and "accounts.google.com" in parsed.netloc
    assert q["client_id"] == ["CID-123"]
    assert q["redirect_uri"] == ["http://localhost:9999/"]
    assert q["response_type"] == ["code"]
    assert q["scope"] == [scopes[0]]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and "=" not in q["code_challenge"][0]
    assert q["state"] and len(q["state"][0]) >= 20
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]


# ── 2-3. happy-path consent ───────────────────────────────────────────────────

def test_consent_persists_tokens_and_metadata():
    store, _ = _store()
    _seed_client(store, 7)
    fetch = StubFetch({"refresh_token": "RT", "access_token": "AT"})
    flow, _ = _flow(store, fetch, FakeRedirect("ok"))

    out = flow.start_consent(7, scopes=["s1", "s2"])

    assert out == {"status": "connected", "profile_id": 7, "scopes": ["s1", "s2"]}
    assert store.get_secret(7, "google", "refresh_token") == "RT"
    assert store.get_secret(7, "google", "access_token") == "AT"
    meta = store.get_credential(7, "google")
    assert meta["status"] == "connected"
    assert meta["scopes"] == ["s1", "s2"]
    # token exchange used the authorization_code grant + PKCE verifier
    sent = fetch.calls[0]["data"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "AUTH_CODE"
    assert sent["code_verifier"] and sent["client_secret"] == "CSECRET"


def test_consent_preserves_precondition_client_id():
    store, _ = _store()
    _seed_client(store, 1)
    flow, _ = _flow(
        store, StubFetch({"refresh_token": "R", "access_token": "A"}),
        FakeRedirect("ok"),
    )
    flow.start_consent(1, scopes=["s"])
    meta = store.get_credential(1, "google")
    assert meta["client_id"] == "CID-123"  # not blanked by the upsert
    assert meta["email"] == "me@x.com"


# ── 4. refresh ────────────────────────────────────────────────────────────────

def test_refresh_access_token_updates_and_returns():
    store, _ = _store()
    _seed_client(store, 3)
    store.set_secret(3, "google", "refresh_token", "RT-OLD")
    store.set_secret(3, "google", "access_token", "AT-OLD")
    fetch = StubFetch({"access_token": "AT-NEW"})
    flow, _ = _flow(store, fetch, FakeRedirect("ok"))

    got = flow.refresh_access_token(3)

    assert got == "AT-NEW"
    assert store.get_secret(3, "google", "access_token") == "AT-NEW"
    assert store.get_secret(3, "google", "refresh_token") == "RT-OLD"
    sent = fetch.calls[0]["data"]
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "RT-OLD"


# ── 5-9. consent failure paths persist nothing ────────────────────────────────

def test_consent_user_denied_persists_nothing():
    store, _ = _store()
    _seed_client(store, 1)
    flow, _ = _flow(store, StubFetch(), FakeRedirect("deny"))
    with pytest.raises(GoogleOAuthError, match="access_denied"):
        flow.start_consent(1, scopes=["s"])
    assert store.get_secret(1, "google", "refresh_token") is None
    assert store.get_secret(1, "google", "access_token") is None


def test_consent_token_exchange_error_persists_nothing():
    store, _ = _store()
    _seed_client(store, 1)
    fetch = StubFetch(exc=RuntimeError("HTTP 400"))
    flow, _ = _flow(store, fetch, FakeRedirect("ok"))
    with pytest.raises(GoogleOAuthError, match="token exchange failed"):
        flow.start_consent(1, scopes=["s"])
    assert store.get_secret(1, "google", "refresh_token") is None
    assert store.get_secret(1, "google", "access_token") is None


def test_consent_missing_client_creds_persists_nothing():
    store, _ = _store()  # nothing seeded
    flow, opened = _flow(
        store, StubFetch({"refresh_token": "R", "access_token": "A"}),
        FakeRedirect("ok"),
    )
    with pytest.raises(GoogleOAuthError, match="no client_id/client_secret"):
        flow.start_consent(1, scopes=["s"])
    assert opened == []  # browser never opened
    assert store.get_secret(1, "google", "refresh_token") is None


def test_consent_state_mismatch_persists_nothing():
    store, _ = _store()
    _seed_client(store, 1)
    redirect = FakeRedirect("ok", state_override="TAMPERED")
    flow, _ = _flow(
        store, StubFetch({"refresh_token": "R", "access_token": "A"}), redirect
    )
    with pytest.raises(GoogleOAuthError, match="state mismatch"):
        flow.start_consent(1, scopes=["s"])
    assert store.get_secret(1, "google", "refresh_token") is None


def test_consent_no_tokens_returned_persists_nothing():
    store, _ = _store()
    _seed_client(store, 1)
    flow, _ = _flow(store, StubFetch({}), FakeRedirect("ok"))
    with pytest.raises(GoogleOAuthError, match="no refresh/access token"):
        flow.start_consent(1, scopes=["s"])
    assert store.get_secret(1, "google", "refresh_token") is None
    assert store.get_secret(1, "google", "access_token") is None


# ── 10-11. refresh failure paths ──────────────────────────────────────────────

def test_refresh_without_stored_refresh_token_errors():
    store, _ = _store()
    _seed_client(store, 1)
    flow, _ = _flow(store, StubFetch({"access_token": "X"}), FakeRedirect("ok"))
    with pytest.raises(GoogleOAuthError, match="no refresh_token stored"):
        flow.refresh_access_token(1)


def test_refresh_missing_client_creds_errors():
    store, _ = _store()
    flow, _ = _flow(store, StubFetch({"access_token": "X"}), FakeRedirect("ok"))
    with pytest.raises(GoogleOAuthError, match="no client_id/client_secret"):
        flow.refresh_access_token(1)


# ── 12. per-profile isolation ─────────────────────────────────────────────────

def test_consent_is_per_profile_isolated():
    store, _ = _store()
    _seed_client(store, 1)
    _seed_client(store, 2)
    flow, _ = _flow(
        store, StubFetch({"refresh_token": "R1", "access_token": "A1"}),
        FakeRedirect("ok"),
    )
    flow.start_consent(1, scopes=["s"])
    assert store.get_secret(1, "google", "refresh_token") == "R1"
    assert store.get_secret(2, "google", "refresh_token") is None
    assert store.get_credential(2, "google")["status"] == ""


# ── 13. secret material is never logged ───────────────────────────────────────

def test_secret_material_never_logged(caplog):
    store, _ = _store()
    _seed_client(store, 1)
    fetch = StubFetch(
        {"refresh_token": "RT-SENTINEL", "access_token": "AT-SENTINEL"}
    )
    flow, _ = _flow(store, fetch, FakeRedirect("ok"))
    with caplog.at_level(logging.DEBUG):
        flow.start_consent(1, scopes=["s"])
        flow.refresh_access_token(1)
    assert "RT-SENTINEL" not in caplog.text
    assert "AT-SENTINEL" not in caplog.text
    assert "CSECRET" not in caplog.text
