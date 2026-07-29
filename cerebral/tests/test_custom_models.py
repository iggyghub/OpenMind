"""
Custom (remote) model registry tests — persistence + keyring secret handling.

The registry table stores only non-secret config; the api_key rides in the
keyring via CredentialStore under provider="custom_model/<slug>",
field="api_token" (a canonical SECRET_FIELD, so delete_credential sweeps it).
"""
from cerebral.db.custom_models import CustomModelStore
from cerebral.db.credentials import CredentialStore


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


def _store():
    return CustomModelStore(db_path=":memory:")


def test_add_and_list_roundtrip():
    s = _store()
    s.add(1, id="custom/box", kind="ollama", url="http://h:11434",
          model="qwen", label="Box", is_cloud=False, secret_ref="")
    rows = s.list(1)
    assert len(rows) == 1
    assert rows[0]["id"] == "custom/box"
    assert rows[0]["kind"] == "ollama"
    assert rows[0]["is_cloud"] is False


def test_add_upserts_in_place():
    s = _store()
    s.add(1, id="custom/x", kind="openai", url="http://a", model="gpt",
          label="A", is_cloud=True)
    s.add(1, id="custom/x", kind="openai", url="http://b", model="gpt2",
          label="B", is_cloud=True)
    rows = s.list(1)
    assert len(rows) == 1
    assert rows[0]["url"] == "http://b"
    assert rows[0]["model"] == "gpt2"


def test_per_profile_isolation():
    s = _store()
    s.add(1, id="custom/x", kind="ollama", url="http://h", model="m", label="X", is_cloud=False)
    assert s.list(2) == []


def test_remove_returns_true_then_false():
    s = _store()
    s.add(1, id="custom/x", kind="ollama", url="http://h", model="m", label="X", is_cloud=False)
    assert s.remove(1, "custom/x") is True
    assert s.remove(1, "custom/x") is False
    assert s.list(1) == []


def test_table_never_stores_the_api_key():
    """The registry row carries only a secret_ref pointer, never the key."""
    s = _store()
    s.add(1, id="custom/x", kind="anthropic", url="", model="claude",
          label="X", is_cloud=True, secret_ref="custom_model/x")
    row = s.list(1)[0]
    assert "sk-secret-123" not in str(row)
    assert row["secret_ref"] == "custom_model/x"


def test_api_key_lives_in_keyring_and_deletes_cleanly():
    kr = FakeKeyring()
    cs = CredentialStore(db_path=":memory:", keyring_backend=kr)
    cs.set_secret(1, "custom_model/x", "api_token", "sk-secret-123")
    assert cs.get_secret(1, "custom_model/x", "api_token") == "sk-secret-123"
    # remove_custom_model path uses delete_credential to sweep the ref.
    cs.delete_credential(1, "custom_model/x")
    assert cs.get_secret(1, "custom_model/x", "api_token") is None
