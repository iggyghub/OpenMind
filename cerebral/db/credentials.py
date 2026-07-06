"""
Per-profile connected-account credential store — Issue #112, ADR-0005
Amendments (2026-05-18 OAuth + 2026-05-23 static tokens).

A **Connected account** (CONTEXT.md) is an external account a profile has
authorized Felix to act as. Its credential is split by sensitivity:

  - Secret material (OAuth ``client_secret``, ``refresh_token``,
    ``access_token`` and static-API ``api_token``) → the OS keyring
    via the ``keyring`` library, namespaced per profile:
    ``service="openmind"``,
    ``username="profile_<id>/<provider>/<field>"``.
  - Non-secret metadata (``client_id``, connected-account email, granted
    scopes, status, timestamps) → a per-profile SQLite table adjacent to
    ``profiles`` in ``cerebral/data/openmind.db``. Static-token providers
    write a degenerate row (empty ``client_id``/``email``/``scopes``,
    ``status="connected"``) per the 2026-05-23 amendment.

Credentials are per-profile, never global, never written to plaintext
``felix-settings.json``, and secret values are never logged.

The keyring dependency is injectable: production uses the real ``keyring``
library (soft-imported at module scope, so a missing keyring is handled
gracefully — Issue #157); tests pass a dict-backed stub exposing
``get_password`` / ``set_password`` / ``delete_password``.

When ``keyring`` is not installed and no stub is injected, the store
degrades to "env-only" mode: ``get_secret`` returns ``None`` so the
static-token resolver in ``cerebral/main.py`` falls through to the env-var
fallback (the documented Issue #148 / ADR-0005 ramp), and ``set_secret``
raises ``RuntimeError("keyring not installed — run: pip install keyring")``
so a tray Save click surfaces the one-line fix rather than silently
succeeding. A one-shot WARN logs on the first miss.

Public interface:
  cs = CredentialStore()                                    # production
  cs = CredentialStore(db_path=":memory:", keyring_backend=stub)  # tests

  cs.set_credential(1, "google", client_id="x", email="a@b.c",
                    scopes=["gmail.readonly"], status="connected")
  cs.get_credential(1, "google")          # → dict (NO secrets) | None
  cs.set_secret(1, "google", "refresh_token", "tok")
  cs.get_secret(1, "google", "refresh_token")   # → "tok" | None
  cs.delete_credential(1, "google")       # metadata row + all keyring entries
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Issue #157 — soft-import keyring at module scope. A missing keyring used
# to crash every static-token plugin in lockstep with an opaque
# ``ModuleNotFoundError`` instead of falling back to env vars. The flag
# lets `_keyring()` distinguish "no stub injected, real lib unavailable"
# (degrade to env-only) from "real lib available" (use it).
try:
    import keyring as _keyring_lib  # type: ignore[import-not-found]
    _KEYRING_AVAILABLE = True
except ImportError:
    _keyring_lib = None  # type: ignore[assignment]
    _KEYRING_AVAILABLE = False

# Module-level guard so the missing-keyring WARN fires exactly once across
# many resolver calls. Reset in tests via monkeypatch.
_warned_keyring_missing = False


def _warn_keyring_missing_once() -> None:
    global _warned_keyring_missing
    if _warned_keyring_missing:
        return
    _warned_keyring_missing = True
    logger.warning(
        "[cred] keyring not installed — per-profile secret storage "
        "disabled; using env vars only. To enable: pip install keyring"
    )


from cerebral.paths import data_dir

DB_PATH = data_dir() / "openmind.db"

# The canonical secret fields a Connected account stores in the keyring.
# delete_credential iterates this set to guarantee no orphaned keyring
# entries survive (the keyring API has no per-namespace enumeration), so
# set_secret rejects any field outside it — an un-deletable secret would
# break the delete-completeness invariant (ADR-0005 amendment).
# Three OAuth fields (2026-05-18) + one static-API field (2026-05-23) +
# one browser web-login field (2026-06-25); the same per-profile
# keyring/SQLite split applies to all shapes. NOTE: "password" is the lone
# *unscoped, non-revocable* secret in this set — it grants full account
# access, unlike the scoped/revocable OAuth tokens and API keys. It exists
# only for the unattended browser-automation re-login path (the dedicated
# secondary account in the `google_web` provider); see the 2026-06-25
# ADR-0005 amendment for why that trade-off is bounded.
SECRET_FIELDS: tuple[str, ...] = (
    "client_secret", "refresh_token", "access_token", "api_token", "password",
)

_KEYRING_SERVICE = "openmind"


def _keyring_username(profile_id: int, provider: str, field: str) -> str:
    return f"profile_{profile_id}/{provider}/{field}"


class CredentialStore:
    def __init__(self, db_path: str | Path = DB_PATH, keyring_backend: Any = None) -> None:
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._init_schema()
        self._kr = keyring_backend

    def _init_schema(self) -> None:
        self._con.executescript("""
            -- Per-profile Connected-account NON-SECRET metadata (Issue #112,
            -- ADR-0005 amendment). Secret material lives in the OS keyring,
            -- never here. Adjacent to `profiles`; FK cascades on profile
            -- delete, matching `profile_acl`.
            CREATE TABLE IF NOT EXISTS connected_account_credentials (
                profile_id  INTEGER NOT NULL,
                provider    TEXT    NOT NULL,
                client_id   TEXT    NOT NULL DEFAULT '',
                email       TEXT    NOT NULL DEFAULT '',
                scopes      TEXT    NOT NULL DEFAULT '[]',
                status      TEXT    NOT NULL DEFAULT '',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (profile_id, provider),
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );
        """)
        self._con.commit()

    # ── keyring backend ───────────────────────────────────────────────────────

    def _keyring(self) -> Any | None:
        """Injected stub, else the real ``keyring`` lib, else ``None``
        when the lib isn't installed.

        Callers must treat ``None`` as "keyring unavailable": reads degrade
        to ``None`` (env-fallback path), writes raise a clear RuntimeError
        pointing at ``pip install keyring`` (Issue #157)."""
        if self._kr is not None:
            return self._kr
        if not _KEYRING_AVAILABLE:
            _warn_keyring_missing_once()
            return None
        self._kr = _keyring_lib
        return self._kr

    # ── non-secret metadata (SQLite) ──────────────────────────────────────────

    def set_credential(
        self,
        profile_id: int,
        provider: str,
        *,
        client_id: str = "",
        email: str = "",
        scopes: list[str] | None = None,
        status: str = "",
    ) -> None:
        """Upsert the non-secret metadata row for (profile, provider)."""
        self._con.execute(
            """INSERT INTO connected_account_credentials
                   (profile_id, provider, client_id, email, scopes, status)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, provider)
               DO UPDATE SET client_id=excluded.client_id,
                             email=excluded.email,
                             scopes=excluded.scopes,
                             status=excluded.status,
                             updated_at=CURRENT_TIMESTAMP""",
            (profile_id, provider, client_id, email,
             json.dumps(scopes or []), status),
        )
        self._con.commit()
        logger.debug(
            "[credentials] metadata upserted profile=%d provider=%s status=%s",
            profile_id, provider, status,
        )

    def get_credential(self, profile_id: int, provider: str) -> dict | None:
        """Return the non-secret metadata row (NO secrets), or None."""
        row = self._con.execute(
            """SELECT profile_id, provider, client_id, email, scopes,
                      status, created_at, updated_at
                 FROM connected_account_credentials
                WHERE profile_id=? AND provider=?""",
            (profile_id, provider),
        ).fetchone()
        if row is None:
            return None
        try:
            scopes = json.loads(row["scopes"])
        except (ValueError, TypeError):
            scopes = []
        return {
            "profile_id": row["profile_id"],
            "provider": row["provider"],
            "client_id": row["client_id"],
            "email": row["email"],
            "scopes": scopes,
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ── secret material (OS keyring) ──────────────────────────────────────────

    def set_secret(self, profile_id: int, provider: str, field: str, value: str) -> None:
        """Store one secret field for (profile, provider) in the keyring.

        Raises ``RuntimeError`` when the ``keyring`` library is missing
        and no stub was injected (Issue #157): writes can't silently
        no-op or the tray Save click would *appear* to succeed while
        nothing persists — the error frame the user sees IS the fix."""
        if field not in SECRET_FIELDS:
            raise ValueError(
                f"field must be one of {SECRET_FIELDS}, got {field!r}"
            )
        kr = self._keyring()
        if kr is None:
            raise RuntimeError(
                "keyring not installed — run: pip install keyring"
            )
        kr.set_password(
            _KEYRING_SERVICE, _keyring_username(profile_id, provider, field), value
        )
        # value is never logged.
        logger.debug(
            "[credentials] secret set profile=%d provider=%s field=%s",
            profile_id, provider, field,
        )

    def get_secret(self, profile_id: int, provider: str, field: str) -> str | None:
        """Return one secret field from the keyring, or None if absent.

        Also returns ``None`` when the ``keyring`` library is missing and
        no stub was injected (Issue #157) so the static-token resolver in
        ``cerebral/main.py`` falls through to the env-var fallback rather
        than crashing the entire static-token chain on a missing dep."""
        if field not in SECRET_FIELDS:
            raise ValueError(
                f"field must be one of {SECRET_FIELDS}, got {field!r}"
            )
        kr = self._keyring()
        if kr is None:
            return None
        return kr.get_password(
            _KEYRING_SERVICE, _keyring_username(profile_id, provider, field)
        )

    # ── delete ────────────────────────────────────────────────────────────────

    def delete_credential(self, profile_id: int, provider: str) -> None:
        """Remove the metadata row AND every keyring entry for (profile,
        provider). Idempotent — missing keyring entries are ignored so the
        delete-completeness invariant holds even on a partial credential.

        When the ``keyring`` library is missing and no stub was injected
        (Issue #157) the keyring sweep is a no-op (there are no entries
        to orphan) and only the SQLite metadata row is removed."""
        self._con.execute(
            """DELETE FROM connected_account_credentials
                WHERE profile_id=? AND provider=?""",
            (profile_id, provider),
        )
        self._con.commit()
        kr = self._keyring()
        if kr is None:
            logger.debug(
                "[credentials] deleted profile=%d provider=%s "
                "(keyring unavailable; sqlite-only)", profile_id, provider,
            )
            return
        for field in SECRET_FIELDS:
            try:
                kr.delete_password(
                    _KEYRING_SERVICE,
                    _keyring_username(profile_id, provider, field),
                )
            except Exception:
                # keyring raises PasswordDeleteError when the entry is
                # absent; a partial credential must still fully delete.
                pass
        logger.debug(
            "[credentials] deleted profile=%d provider=%s", profile_id, provider
        )
