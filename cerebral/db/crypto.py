"""Encryption-at-rest for conversation turn content.

Uses Fernet symmetric encryption. The key is stored in the OS keyring
so it survives restarts but never touches the filesystem. Decryption
is non-destructive: invalid tokens are returned as-is, making existing
plaintext rows readable without migration.
"""

import binascii
import cryptography.fernet
import keyring

KEYRING_SERVICE = "openmind"
KEYRING_KEY = "conversation_content_key"

_fernet_instance: cryptography.fernet.Fernet | None = None


def get_or_create_key() -> bytes:
    raw = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
    if raw is not None:
        return raw.encode("utf-8")
    new_key = cryptography.fernet.Fernet.generate_key()
    keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, new_key.decode("utf-8"))
    return new_key


def _fernet() -> cryptography.fernet.Fernet:
    nonlocal _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = cryptography.fernet.Fernet(get_or_create_key())
    return _fernet_instance


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (cryptography.fernet.InvalidToken, binascii.Error, ValueError):
        return token
