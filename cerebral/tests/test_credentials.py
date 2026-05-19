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
import sqlite3

import pytest

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
