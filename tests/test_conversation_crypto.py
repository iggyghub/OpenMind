"""Tests for conversation content encryption-at-rest."""

import pytest
from cerebral.db import crypto

# In-memory keyring store for testing
_keyring_store: dict[str, str] = {}


def _mock_get_password(service: str, key: str) -> str | None:
    return _keyring_store.get(f"{service}:{key}")


def _mock_set_password(service: str, key: str, password: str) -> None:
    _keyring_store[f"{service}:{key}"] = password


@pytest.fixture(autouse=True)
def stub_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto.keyring, "get_password", _mock_get_password)
    monkeypatch.setattr(crypto.keyring, "set_password", _mock_set_password)
    # Reset module-level Fernet cache so each test starts with a fresh key
    crypto._fernet_instance = None
    _keyring_store.clear()


def test_round_trip_encryption():
    original = "{'text': 'Hello, World!'}"
    encrypted = crypto.encrypt(original)
    decrypted = crypto.decrypt(encrypted)
    assert decrypted == original


def test_passthrough_invalid_token():
    plaintext = "not a real token"
    result = crypto.decrypt(plaintext)
    assert result == plaintext
