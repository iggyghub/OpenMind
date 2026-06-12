"""
Static-token factory tests — Issue #148, ADR-0005 2026-05-23 amendment.

Covers the keyring-wins/env-fallback helper and each of the five
`_get_<provider>_token_provider()` factories under all four scenarios:

  - env-only (keyring empty)        → token, source="env"
  - keyring-only (env empty)        → token, source="keyring"
  - both set                        → keyring wins, source="keyring"
  - neither set                     → None,  source="none"

Plus the no-active-profile branch: CredentialStore is skipped entirely;
env is the only source consulted.

Tests use an in-memory CredentialStore patched into the helper via the
existing `_get_credential_store` seam. No real OS keyring, no real env
mutation outside monkeypatch scope.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cerebral.db.credentials import CredentialStore


class _FakeKR:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, s, u, p) -> None:
        self.store[(s, u)] = p

    def get_password(self, s, u):
        return self.store.get((s, u))

    def delete_password(self, s, u) -> None:
        self.store.pop((s, u), None)


class _Profile:
    def __init__(self, pid: int) -> None:
        self.id = pid


@pytest.fixture
def fac_rig(monkeypatch):
    """Pin a fresh CredentialStore + active profile + cleared env vars."""
    import cerebral.main as main_mod

    store = CredentialStore(db_path=":memory:", keyring_backend=_FakeKR())
    profile = _Profile(1)

    saved = {
        "_active_profile": main_mod._active_profile,
        "_get_credential_store": main_mod._get_credential_store,
    }
    main_mod._active_profile = profile
    main_mod._get_credential_store = lambda: store

    # Strip every relevant env var so per-test setenv calls have a clean
    # baseline regardless of the developer's local shell.
    for _, env_var in main_mod._STATIC_TOKEN_PROVIDERS:
        monkeypatch.delenv(env_var, raising=False)

    class Rig:
        def __init__(self):
            self.module = main_mod
            self.store = store
            self.profile = profile

        def no_profile(self) -> None:
            main_mod._active_profile = None

    try:
        yield Rig()
    finally:
        for key, value in saved.items():
            setattr(main_mod, key, value)


# ── _STATIC_TOKEN_PROVIDERS constant ──────────────────────────────────────────

def test_static_token_providers_list_canonical_order():
    """The canonical UI render order — locked at issue body §6."""
    from cerebral import main as main_mod
    assert main_mod._STATIC_TOKEN_PROVIDERS == [
        ("youtube",      "YOUTUBE_API_KEY"),
        ("google_maps",  "GOOGLE_MAPS_API_KEY"),
        ("todoist",      "TODOIST_API_TOKEN"),
        ("notion",       "NOTION_API_TOKEN"),
        ("toggl",        "TOGGL_API_TOKEN"),
        ("clockify",     "CLOCKIFY_API_KEY"),
    ]
    assert main_mod._STATIC_TOKEN_PROVIDER_NAMES == frozenset({
        "youtube", "google_maps", "todoist", "notion", "toggl", "clockify",
    })


# ── _static_token_from_store_or_env: the helper ──────────────────────────────

_PROVIDERS = ["youtube", "google_maps", "todoist", "notion", "toggl", "clockify"]
_ENV_VARS = {
    "youtube":      "YOUTUBE_API_KEY",
    "google_maps":  "GOOGLE_MAPS_API_KEY",
    "todoist":      "TODOIST_API_TOKEN",
    "notion":       "NOTION_API_TOKEN",
    "toggl":        "TOGGL_API_TOKEN",
    "clockify":     "CLOCKIFY_API_KEY",
}


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_helper_returns_none_when_neither_source(fac_rig, provider):
    tok, source = fac_rig.module._static_token_from_store_or_env(
        provider, _ENV_VARS[provider]
    )
    assert tok is None
    assert source == "none"


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_helper_returns_env_when_only_env(fac_rig, provider, monkeypatch):
    monkeypatch.setenv(_ENV_VARS[provider], f"env-{provider}")
    tok, source = fac_rig.module._static_token_from_store_or_env(
        provider, _ENV_VARS[provider]
    )
    assert tok == f"env-{provider}"
    assert source == "env"


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_helper_returns_keyring_when_only_keyring(fac_rig, provider):
    fac_rig.store.set_secret(1, provider, "api_token", f"kr-{provider}")
    tok, source = fac_rig.module._static_token_from_store_or_env(
        provider, _ENV_VARS[provider]
    )
    assert tok == f"kr-{provider}"
    assert source == "keyring"


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_helper_keyring_wins_when_both(fac_rig, provider, monkeypatch):
    monkeypatch.setenv(_ENV_VARS[provider], f"env-{provider}")
    fac_rig.store.set_secret(1, provider, "api_token", f"kr-{provider}")
    tok, source = fac_rig.module._static_token_from_store_or_env(
        provider, _ENV_VARS[provider]
    )
    assert tok == f"kr-{provider}"
    assert source == "keyring"


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_helper_no_profile_falls_back_to_env(fac_rig, provider, monkeypatch):
    """Active profile is None → skip CredentialStore, env-only path."""
    fac_rig.store.set_secret(1, provider, "api_token", f"kr-{provider}")
    monkeypatch.setenv(_ENV_VARS[provider], f"env-{provider}")
    fac_rig.no_profile()
    tok, source = fac_rig.module._static_token_from_store_or_env(
        provider, _ENV_VARS[provider]
    )
    assert tok == f"env-{provider}"
    assert source == "env"


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_helper_no_profile_no_env_returns_none(fac_rig, provider):
    fac_rig.no_profile()
    tok, source = fac_rig.module._static_token_from_store_or_env(
        provider, _ENV_VARS[provider]
    )
    assert tok is None
    assert source == "none"


# ── per-provider factories: round-trip through the helper ────────────────────

_FACTORIES = [
    ("youtube",      "_get_youtube_token_provider"),
    ("google_maps",  "_get_google_maps_token_provider"),
    ("todoist",      "_get_todoist_token_provider"),
    ("notion",       "_get_notion_token_provider"),
    ("toggl",        "_get_toggl_token_provider"),
    ("clockify",     "_get_clockify_token_provider"),
]


@pytest.mark.parametrize("provider,factory_name", _FACTORIES)
def test_factory_returns_none_when_neither_source(fac_rig, provider, factory_name):
    factory = getattr(fac_rig.module, factory_name)
    assert factory() is None


@pytest.mark.parametrize("provider,factory_name", _FACTORIES)
def test_factory_reads_env_token(fac_rig, provider, factory_name, monkeypatch):
    monkeypatch.setenv(_ENV_VARS[provider], f"env-{provider}")
    factory = getattr(fac_rig.module, factory_name)
    p = factory()
    assert p is not None
    assert p.current() == f"env-{provider}"


@pytest.mark.parametrize("provider,factory_name", _FACTORIES)
def test_factory_reads_keyring_token(fac_rig, provider, factory_name):
    fac_rig.store.set_secret(1, provider, "api_token", f"kr-{provider}")
    factory = getattr(fac_rig.module, factory_name)
    p = factory()
    assert p is not None
    assert p.current() == f"kr-{provider}"


@pytest.mark.parametrize("provider,factory_name", _FACTORIES)
def test_factory_keyring_wins_over_env(fac_rig, provider, factory_name, monkeypatch):
    monkeypatch.setenv(_ENV_VARS[provider], f"env-{provider}")
    fac_rig.store.set_secret(1, provider, "api_token", f"kr-{provider}")
    factory = getattr(fac_rig.module, factory_name)
    assert factory().current() == f"kr-{provider}"


@pytest.mark.parametrize("provider,factory_name", _FACTORIES)
def test_factory_treats_whitespace_env_as_unset(fac_rig, provider, factory_name, monkeypatch):
    monkeypatch.setenv(_ENV_VARS[provider], "   ")
    factory = getattr(fac_rig.module, factory_name)
    assert factory() is None


@pytest.mark.parametrize("provider,factory_name", _FACTORIES)
def test_factory_re_resolved_per_call(fac_rig, provider, factory_name, monkeypatch):
    """Fresh env-var picks up on the next call without restart."""
    factory = getattr(fac_rig.module, factory_name)
    assert factory() is None
    monkeypatch.setenv(_ENV_VARS[provider], "freshly-set")
    assert factory().current() == "freshly-set"


# ── secret never logged via the helper ───────────────────────────────────────

def test_helper_value_never_logged(fac_rig, caplog, monkeypatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "LOG-FORBIDDEN-ENV")
    fac_rig.store.set_secret(1, "toggl", "api_token", "LOG-FORBIDDEN-KR")
    with caplog.at_level("DEBUG"):
        fac_rig.module._static_token_from_store_or_env("notion", "NOTION_API_TOKEN")
        fac_rig.module._static_token_from_store_or_env("toggl",  "TOGGL_API_TOKEN")
    assert "LOG-FORBIDDEN-ENV" not in caplog.text
    assert "LOG-FORBIDDEN-KR"  not in caplog.text
