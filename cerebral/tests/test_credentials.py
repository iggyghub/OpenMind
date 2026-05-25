"""
CredentialStore tests — Issue #112, ADR-0005 Amendment (2026-05-18).

Unit tests use an in-memory SQLite DB and a dict-backed keyring stub; no
disk writes, no real OS keyring, no `keyring` package required to run.

Slices:
  1.  set_credential + get_credential round-trip (metadata only, no secrets)
  2.  get_credential → None for unknown (profile, provider)
  3.  set_credential upserts (second call updates in place)
  4.  set_secret + get_secret round-trip via the injected keyring
  5.  get_secret → None for an absent secret
  6.  set_secret / get_secret reject a field outside SECRET_FIELDS
  7.  keyring namespacing: service="openmind", username="profile_<id>/<p>/<f>"
  8.  delete_credential removes the metadata row AND every keyring entry
  9.  delete_credential is idempotent / completes on a partial credential
  10. per-profile isolation (X cannot read Y's secret or metadata)
  11. scopes round-trip as a list; default is []
  12. secret material never lands in the SQLite DB
  13. secret value is never logged
"""
import logging
import sqlite3

import pytest

from cerebral.db import credentials as cred_mod
from cerebral.db.credentials import SECRET_FIELDS, CredentialStore


# ── dict-backed keyring stub (duck-types keyring.get/set/delete_password) ──────

class _PasswordDeleteError(Exception):
    """Mirrors keyring.errors.PasswordDeleteError (raised when absent)."""


class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.store[(service, username)]
        except KeyError:
            raise _PasswordDeleteError(username)


def _cs(kr: FakeKeyring | None = None) -> tuple[CredentialStore, FakeKeyring]:
    kr = kr or FakeKeyring()
    return CredentialStore(db_path=":memory:", keyring_backend=kr), kr


# ── 1–3 metadata ──────────────────────────────────────────────────────────────

def test_set_get_credential_roundtrip():
    cs, _ = _cs()
    cs.set_credential(1, "google", client_id="cid", email="a@b.c",
                      scopes=["gmail.readonly"], status="connected")
    row = cs.get_credential(1, "google")
    assert row["profile_id"] == 1
    assert row["provider"] == "google"
    assert row["client_id"] == "cid"
    assert row["email"] == "a@b.c"
    assert row["scopes"] == ["gmail.readonly"]
    assert row["status"] == "connected"
    assert row["created_at"] and row["updated_at"]


def test_get_credential_unknown_returns_none():
    cs, _ = _cs()
    assert cs.get_credential(1, "google") is None


def test_set_credential_upserts():
    cs, _ = _cs()
    cs.set_credential(1, "google", client_id="old", status="pending")
    cs.set_credential(1, "google", client_id="new", status="connected")
    row = cs.get_credential(1, "google")
    assert row["client_id"] == "new"
    assert row["status"] == "connected"
    # Still a single row for (profile, provider).
    n = cs._con.execute(
        "SELECT COUNT(*) c FROM connected_account_credentials"
    ).fetchone()["c"]
    assert n == 1


# ── 4–7 secrets via keyring ───────────────────────────────────────────────────

def test_set_get_secret_roundtrip():
    cs, _ = _cs()
    cs.set_secret(1, "google", "refresh_token", "rt-value")
    assert cs.get_secret(1, "google", "refresh_token") == "rt-value"


def test_get_secret_absent_returns_none():
    cs, _ = _cs()
    assert cs.get_secret(1, "google", "access_token") is None


@pytest.mark.parametrize("bad", ["api_key", "", "password", "client_secretx"])
def test_secret_rejects_unknown_field(bad):
    cs, _ = _cs()
    with pytest.raises(ValueError):
        cs.set_secret(1, "google", bad, "v")
    with pytest.raises(ValueError):
        cs.get_secret(1, "google", bad)


def test_keyring_namespacing():
    cs, kr = _cs()
    cs.set_secret(7, "google", "client_secret", "s")
    assert ("openmind", "profile_7/google/client_secret") in kr.store
    assert kr.store[("openmind", "profile_7/google/client_secret")] == "s"


# ── 8–9 delete ────────────────────────────────────────────────────────────────

def test_delete_removes_metadata_and_all_secrets():
    cs, kr = _cs()
    cs.set_credential(1, "google", client_id="cid", status="connected")
    for f in SECRET_FIELDS:
        cs.set_secret(1, "google", f, f"{f}-val")
    cs.delete_credential(1, "google")
    assert cs.get_credential(1, "google") is None
    assert kr.store == {}
    for f in SECRET_FIELDS:
        assert cs.get_secret(1, "google", f) is None


def test_delete_is_idempotent_and_completes_on_partial_credential():
    cs, kr = _cs()
    # Only one secret set; metadata never written.
    cs.set_secret(1, "google", "refresh_token", "rt")
    cs.delete_credential(1, "google")          # missing entries ignored
    assert kr.store == {}
    cs.delete_credential(1, "google")          # second call: no error


# ── 10 per-profile isolation ──────────────────────────────────────────────────

def test_per_profile_isolation():
    cs, _ = _cs()
    cs.set_credential(1, "google", email="one@x.c")
    cs.set_secret(1, "google", "refresh_token", "secret-of-1")
    # Profile 2 has nothing under the same provider.
    assert cs.get_credential(2, "google") is None
    assert cs.get_secret(2, "google", "refresh_token") is None
    # Profile 2's own credential does not leak into profile 1.
    cs.set_secret(2, "google", "refresh_token", "secret-of-2")
    assert cs.get_secret(1, "google", "refresh_token") == "secret-of-1"
    assert cs.get_secret(2, "google", "refresh_token") == "secret-of-2"
    # Deleting profile 2 leaves profile 1 intact.
    cs.delete_credential(2, "google")
    assert cs.get_secret(1, "google", "refresh_token") == "secret-of-1"


# ── 11 scopes ─────────────────────────────────────────────────────────────────

def test_scopes_roundtrip_and_default_empty():
    cs, _ = _cs()
    cs.set_credential(1, "google")
    assert cs.get_credential(1, "google")["scopes"] == []
    cs.set_credential(1, "google", scopes=["a", "b"])
    assert cs.get_credential(1, "google")["scopes"] == ["a", "b"]


# ── 12 secrets never in the DB ────────────────────────────────────────────────

def test_secret_never_written_to_sqlite():
    cs, _ = _cs()
    cs.set_credential(1, "google", client_id="cid", status="connected")
    cs.set_secret(1, "google", "refresh_token", "TOP-SECRET-TOKEN")
    # Dump every value in every column of the credential table.
    rows = cs._con.execute(
        "SELECT * FROM connected_account_credentials"
    ).fetchall()
    blob = " ".join(str(v) for r in rows for v in tuple(r))
    assert "TOP-SECRET-TOKEN" not in blob
    # And it is not surfaced by the non-secret accessor.
    assert "TOP-SECRET-TOKEN" not in str(cs.get_credential(1, "google"))


# ── 13 secrets never logged ───────────────────────────────────────────────────

def test_secret_value_never_logged(caplog):
    cs, _ = _cs()
    with caplog.at_level("DEBUG"):
        cs.set_secret(1, "google", "refresh_token", "DO-NOT-LOG-ME")
        cs.get_secret(1, "google", "refresh_token")
        cs.delete_credential(1, "google")
    assert "DO-NOT-LOG-ME" not in caplog.text


# ── 14–17 api_token field (Issue #148, ADR-0005 2026-05-23 amendment) ────────

def test_api_token_in_secret_fields():
    """api_token is the fourth canonical secret field (after the three
    OAuth fields). delete_credential must iterate it; set/get must accept."""
    assert "api_token" in SECRET_FIELDS
    assert SECRET_FIELDS == (
        "client_secret", "refresh_token", "access_token", "api_token",
    )


@pytest.mark.parametrize("provider", [
    "youtube", "todoist", "notion", "toggl", "clockify",
])
def test_set_get_api_token_roundtrip(provider):
    cs, kr = _cs()
    cs.set_secret(1, provider, "api_token", f"key-for-{provider}")
    assert cs.get_secret(1, provider, "api_token") == f"key-for-{provider}"
    # Keyring namespacing matches the OAuth fields' shape.
    assert ("openmind", f"profile_1/{provider}/api_token") in kr.store


def test_delete_credential_wipes_api_token_alongside_oauth():
    """delete_credential iterates SECRET_FIELDS — extending the tuple to
    include api_token must keep delete-completeness for the new field."""
    cs, kr = _cs()
    cs.set_credential(1, "todoist", status="connected")
    cs.set_secret(1, "todoist", "api_token", "todoist-key")
    cs.set_secret(1, "google", "refresh_token", "rt")
    cs.set_secret(1, "google", "client_secret", "cs")
    cs.delete_credential(1, "todoist")
    # The Todoist api_token entry is gone.
    assert cs.get_secret(1, "todoist", "api_token") is None
    assert ("openmind", "profile_1/todoist/api_token") not in kr.store
    # The Google secrets under the same profile are untouched.
    assert cs.get_secret(1, "google", "refresh_token") == "rt"
    assert cs.get_secret(1, "google", "client_secret") == "cs"


def test_api_token_value_never_logged(caplog):
    cs, _ = _cs()
    with caplog.at_level("DEBUG"):
        cs.set_secret(1, "clockify", "api_token", "DO-NOT-LOG-API-KEY")
        cs.get_secret(1, "clockify", "api_token")
    assert "DO-NOT-LOG-API-KEY" not in caplog.text


def test_static_token_metadata_row_is_degenerate():
    """Static-token providers write a degenerate row: empty client_id /
    email / scopes, status='connected'. The row exists so the per-profile
    FK cascade wipes the keyring entry on profile delete."""
    cs, _ = _cs()
    cs.set_credential(1, "todoist", client_id="", email="",
                      scopes=[], status="connected")
    row = cs.get_credential(1, "todoist")
    assert row["status"] == "connected"
    assert row["client_id"] == ""
    assert row["email"] == ""
    assert row["scopes"] == []


# ── 18–22 keyring missing → env-only fallback (Issue #157) ────────────────────

@pytest.fixture
def keyring_missing(monkeypatch):
    """Simulate `keyring` not installed in Cerebral's env. Construct a
    CredentialStore *without* a stub backend so `_keyring()` exercises
    the real soft-import branch."""
    monkeypatch.setattr(cred_mod, "_KEYRING_AVAILABLE", False)
    monkeypatch.setattr(cred_mod, "_keyring_lib", None)
    monkeypatch.setattr(cred_mod, "_warned_keyring_missing", False)


def test_get_secret_returns_none_when_keyring_missing(keyring_missing):
    """The env-fallback path in `_static_token_from_store_or_env` triggers
    on `get_secret() is None`, so the keyring-missing branch must return
    None (not raise) for env-fallback to kick in."""
    cs = CredentialStore(db_path=":memory:")  # no stub injected
    assert cs.get_secret(1, "todoist", "api_token") is None


def test_set_secret_raises_clear_error_when_keyring_missing(keyring_missing):
    """The error the user sees IS the fix — silently no-op'ing would
    create a confusing UX where the tray Save click *appears* to succeed
    while nothing persists."""
    cs = CredentialStore(db_path=":memory:")  # no stub injected
    with pytest.raises(RuntimeError, match="pip install keyring"):
        cs.set_secret(1, "todoist", "api_token", "tok-value")


def test_keyring_missing_warn_fires_exactly_once(keyring_missing, caplog):
    """One module-level WARN per Cerebral lifetime regardless of how
    many resolver calls hit the keyring-missing branch."""
    cs = CredentialStore(db_path=":memory:")  # no stub injected
    with caplog.at_level(logging.WARNING, logger=cred_mod.logger.name):
        for _ in range(5):
            assert cs.get_secret(1, "todoist", "api_token") is None
    warn_lines = [
        r for r in caplog.records
        if "keyring not installed" in r.getMessage()
    ]
    assert len(warn_lines) == 1


def test_delete_credential_no_op_on_keyring_when_missing(keyring_missing):
    """Metadata row still deleted; the keyring sweep is silently skipped
    (no entries can exist to orphan when keyring isn't installed)."""
    cs = CredentialStore(db_path=":memory:")  # no stub injected
    # Insert a metadata row directly; set_secret would raise here.
    cs.set_credential(1, "todoist", status="connected")
    cs.delete_credential(1, "todoist")          # must not raise
    assert cs.get_credential(1, "todoist") is None
    # Second call is still idempotent.
    cs.delete_credential(1, "todoist")


def test_injected_stub_overrides_missing_keyring(keyring_missing):
    """The injected-backend seam still works even when the real keyring
    lib is missing — tests don't need keyring installed."""
    cs, kr = _cs()  # injects FakeKeyring
    cs.set_secret(1, "todoist", "api_token", "stubbed")
    assert cs.get_secret(1, "todoist", "api_token") == "stubbed"
    # Stub backend should NOT have triggered the missing-keyring warn.
    assert cred_mod._warned_keyring_missing is False
