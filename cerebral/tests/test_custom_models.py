"""
Custom (remote) model registry tests — persistence + keyring secret handling.

The registry table stores only non-secret config; the api_key rides in the
keyring via CredentialStore under provider="custom_model/<slug>",
field="api_token" (a canonical SECRET_FIELD, so delete_credential sweeps it).
"""
import sqlite3

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


def test_supports_vision_roundtrips():
    s = _store()
    s.add(1, id="custom/vl", kind="openai", url="http://a", model="qwen-vl",
          label="VL", is_cloud=True, dynamic=True, supports_vision=True)
    s.add(1, id="custom/text", kind="openai", url="http://b", model="m",
          label="Text", is_cloud=True)  # default False
    rows = {r["id"]: r for r in s.list(1)}
    assert rows["custom/vl"]["supports_vision"] is True
    assert rows["custom/text"]["supports_vision"] is False


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


# ── S3 (#525) -- dynamic (server-first) flag ────────────────────────────────

def test_dynamic_flag_roundtrips_with_blank_model():
    """model="" + dynamic=1 persists and round-trips (auto-resolve marker)."""
    s = _store()
    s.add(1, id="custom/bonsai", kind="openai", url="http://s",
          model="", label="bonsai", is_cloud=True, dynamic=True)
    rows = s.list(1)
    assert len(rows) == 1
    assert rows[0]["dynamic"] is True
    assert rows[0]["model"] == ""


def test_dynamic_defaults_false():
    s = _store()
    s.add(1, id="custom/x", kind="ollama", url="http://h", model="m",
          label="X", is_cloud=False)
    assert s.list(1)[0]["dynamic"] is False


def test_dynamic_upsert_updates_cached_model():
    """`model` doubles as the last-resolved cache; upsert refreshes it."""
    s = _store()
    s.add(1, id="custom/x", kind="openai", url="http://s", model="",
          label="X", is_cloud=True, dynamic=True)
    s.add(1, id="custom/x", kind="openai", url="http://s", model="gpt-a",
          label="X", is_cloud=True, dynamic=True)
    assert s.list(1)[0]["model"] == "gpt-a"
    assert s.list(1)[0]["dynamic"] is True


def test_api_key_lives_in_keyring_and_deletes_cleanly():
    kr = FakeKeyring()
    cs = CredentialStore(db_path=":memory:", keyring_backend=kr)
    cs.set_secret(1, "custom_model/x", "api_token", "sk-secret-123")
    assert cs.get_secret(1, "custom_model/x", "api_token") == "sk-secret-123"
    # remove_custom_model path uses delete_credential to sweep the ref.
    cs.delete_credential(1, "custom_model/x")
    assert cs.get_secret(1, "custom_model/x", "api_token") is None


# ── context_window (#760) ────────────────────────────────────────────────────

def test_context_window_roundtrips():
    s = _store()
    s.add(1, id="custom/bonsai", kind="openai", url="http://s", model="gpt",
          label="Bonsai", is_cloud=True, context_window=131072)
    rows = s.list(1)
    assert len(rows) == 1
    assert rows[0]["context_window"] == 131072


def test_context_window_defaults_zero_when_unset():
    """0 is the 'unset' sentinel -- the router applies its own 8192 floor
    for it, this store layer just doesn't invent a value."""
    s = _store()
    s.add(1, id="custom/x", kind="ollama", url="http://h", model="m",
          label="X", is_cloud=False)
    assert s.list(1)[0]["context_window"] == 0


def test_context_window_upsert_updates_in_place():
    s = _store()
    s.add(1, id="custom/x", kind="openai", url="http://a", model="gpt",
          label="A", is_cloud=True, context_window=8192)
    s.add(1, id="custom/x", kind="openai", url="http://a", model="gpt",
          label="A", is_cloud=True, context_window=200000)
    assert s.list(1)[0]["context_window"] == 200000


def test_existing_db_created_before_context_window_column_still_opens(tmp_path):
    """A pre-#760 DB has the custom_models table but no context_window
    column. Opening it must migrate cleanly (ALTER TABLE, caught
    OperationalError on re-run) rather than crash, and existing rows must
    still be readable with the new column defaulting to 0.

    Uses pytest's tmp_path (session-swept, not deleted on test exit) rather
    than tempfile.TemporaryDirectory -- sqlite3 keeps the file handle open
    for the life of the CustomModelStore, and an immediate context-manager
    rmtree() 32s the open db file on Windows."""
    db_path = tmp_path / "old.db"
    # Build the pre-#760 schema by hand (mirrors what a real old DB on disk
    # looks like -- no context_window column at all).
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE custom_models (
            profile_id INTEGER NOT NULL,
            id         TEXT    NOT NULL,
            kind       TEXT    NOT NULL,
            url        TEXT    NOT NULL DEFAULT '',
            model      TEXT    NOT NULL,
            label      TEXT    NOT NULL DEFAULT '',
            is_cloud   INTEGER NOT NULL DEFAULT 0,
            secret_ref TEXT    NOT NULL DEFAULT '',
            dynamic    INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (profile_id, id)
        );
    """)
    con.execute(
        "INSERT INTO custom_models (profile_id, id, kind, url, model, label, is_cloud) "
        "VALUES (1, 'custom/legacy', 'openai', 'http://s', 'gpt', 'Legacy', 1)"
    )
    con.commit()
    con.close()

    # Opening via CustomModelStore should migrate the column in place.
    s = CustomModelStore(db_path=db_path)
    rows = s.list(1)
    assert len(rows) == 1
    assert rows[0]["id"] == "custom/legacy"
    assert rows[0]["context_window"] == 0  # migrated column defaults to 0

    # And the store is fully usable afterward.
    s.add(1, id="custom/legacy", kind="openai", url="http://s", model="gpt",
          label="Legacy", is_cloud=True, context_window=32000)
    assert s.list(1)[0]["context_window"] == 32000
