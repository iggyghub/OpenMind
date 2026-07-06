"""
Harness channel config store -- Issue #299 / S16.

Persists per-channel UI state (enabled/disabled flag + a "secret set"
marker) for the OpenClaw harness channels listed in
``main.py:_HARNESS_CHANNELS``. The actual channel-credential value (e.g.
a Telegram bot token) is written to the OS keyring via the same
soft-import / env-fallback posture as ``cerebral/db/credentials.py``;
the JSON file stores ONLY non-secret state.

Write-only secret invariant (Slice S16 / spec rule 3): the UI sets a
secret via ``set_channel_secret`` but the store NEVER reveals it back.
``status()`` exposes a boolean ``secret_set`` derived from the keyring
record so the UI can render "set / not set" without echoing the secret.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Soft-import keyring -- same posture as cerebral/db/credentials.py.
# A missing keyring degrades to "in-memory marker only": set_secret will
# raise so the UI can surface the one-line fix, get/has return False.
try:
    import keyring as _keyring_lib  # type: ignore[import-not-found]
    _KEYRING_AVAILABLE = True
except ImportError:
    _keyring_lib = None  # type: ignore[assignment]
    _KEYRING_AVAILABLE = False


from cerebral.paths import data_dir

_HARNESS_PATH = data_dir() / "felix-harness.json"
_KEYRING_SERVICE = "openmind-harness"


def _keyring_username(channel: str) -> str:
    return f"channel/{channel}/secret"


class HarnessChannelStore:
    """JSON-backed enabled-state + keyring-backed secret store.

    ``channels`` is the canonical channel list, supplied at construction
    so the store can validate channel names without import-cycling on
    ``cerebral.main``. Tests pass a stub ``keyring_backend`` exposing
    ``get_password`` / ``set_password`` / ``delete_password``; production
    uses the soft-imported ``keyring`` lib (or degrades when absent).
    """

    def __init__(
        self,
        channels: list[str],
        path: Path = _HARNESS_PATH,
        keyring_backend: Any = None,
    ) -> None:
        self._channels = list(channels)
        self._valid = frozenset(self._channels)
        self._path = path
        self._kr = keyring_backend
        self._data: dict[str, Any] = self._load()

    # ── public ───────────────────────────────────────────────────────────────

    def is_enabled(self, channel: str) -> bool:
        if channel not in self._valid:
            return False
        return channel in self._enabled_set()

    def set_enabled(self, channel: str, enabled: bool) -> None:
        if channel not in self._valid:
            raise ValueError(f"Unknown channel: {channel!r}")
        enabled_set = self._enabled_set()
        if enabled:
            enabled_set.add(channel)
        else:
            enabled_set.discard(channel)
        self._data["enabled_channels"] = sorted(enabled_set)
        self._save()

    def has_secret(self, channel: str) -> bool:
        if channel not in self._valid:
            return False
        kr = self._keyring()
        if kr is None:
            return False
        try:
            return bool(kr.get_password(_KEYRING_SERVICE, _keyring_username(channel)))
        except Exception:  # pragma: no cover -- defensive
            return False

    def set_secret(self, channel: str, secret: str) -> None:
        """Persist a channel secret to the keyring. Raises ``ValueError``
        on unknown channel or empty secret, ``RuntimeError`` when no
        keyring backend is available."""
        if channel not in self._valid:
            raise ValueError(f"Unknown channel: {channel!r}")
        if not isinstance(secret, str) or not secret:
            raise ValueError("secret must be a non-empty string")
        kr = self._keyring()
        if kr is None:
            raise RuntimeError(
                "keyring not installed -- run: pip install keyring"
            )
        kr.set_password(_KEYRING_SERVICE, _keyring_username(channel), secret)

    def clear_secret(self, channel: str) -> None:
        if channel not in self._valid:
            return
        kr = self._keyring()
        if kr is None:
            return
        try:
            kr.delete_password(_KEYRING_SERVICE, _keyring_username(channel))
        except Exception:  # pragma: no cover -- defensive
            pass

    def status(self) -> list[dict[str, Any]]:
        """Snapshot suitable for inclusion in the harness_status event.
        Returns a list ordered by ``self._channels`` so the UI ordering
        is stable."""
        return [
            {
                "name": ch,
                "enabled": ch in self._enabled_set(),
                "secret_set": self.has_secret(ch),
            }
            for ch in self._channels
        ]

    # ── private ──────────────────────────────────────────────────────────────

    def _keyring(self) -> Any:
        if self._kr is not None:
            return self._kr
        if _KEYRING_AVAILABLE:
            return _keyring_lib
        return None

    def _enabled_set(self) -> set[str]:
        raw = self._data.get("enabled_channels") or []
        if not isinstance(raw, list):
            return set()
        return {ch for ch in raw if ch in self._valid}

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"enabled_channels": []}
        except Exception:
            logger.warning(
                "[harness] Could not read %s; using defaults", self._path
            )
            return {"enabled_channels": []}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.warning("[harness] Could not write %s", self._path)
