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


def test_store_roundtrip_encrypts_at_rest_and_decrypts_on_read(tmp_path):
    """Integration: a turn is ciphertext in the DB but readable via the store.

    This is the check the crypto-only tests miss -- it exercises append()
    (encrypt) and _row_to_turn (decrypt) together, so a missing decrypt on
    the read path fails here instead of silently blanking conversations.
    """
    from cerebral.db.conversation import ConversationStore, KIND_USER_TEXT

    store = ConversationStore(db_path=tmp_path / "conv.db")
    turn = store.append(profile_id=1, kind=KIND_USER_TEXT, content={"text": "secret hi"})

    # Stored value is ciphertext -- the plaintext must not be on disk.
    raw = store._con.execute(
        "SELECT content_json FROM conversation_turns WHERE id=?", (turn.id,)
    ).fetchone()[0]
    assert "secret hi" not in raw

    # Reading back through the store decrypts transparently.
    recent = store.list_recent(profile_id=1, limit=10)
    assert recent[-1].content == {"text": "secret hi"}
