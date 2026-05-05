"""
n8n credential health-check tests — Issue #19.

TDD vertical slices for scripts/n8n_check_credentials.py:
  - check_credentials() happy path (both types present)
  - check_credentials() missing credential → ok=False + missing list
  - check_credentials() unreachable n8n → ok=False
  - auth header sent correctly
  - correct endpoint called
  - base_url override accepted

All HTTP calls are injected via fetch_fn so no live n8n is needed.
"""
import sys
from pathlib import Path

import pytest

# Ensure repo root is importable so `scripts/` is on the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Shared fake credential responses
# ---------------------------------------------------------------------------

_BOTH_CREDS = [
    {"id": "c1", "name": "My Google Account", "type": "googleOAuth2Api"},
    {"id": "c2", "name": "My Zoom Account", "type": "zoomOAuth2Api"},
]

_GOOGLE_ONLY = [
    {"id": "c1", "name": "My Google Account", "type": "googleOAuth2Api"},
]

_EMPTY_CREDS: list = []


# ---------------------------------------------------------------------------
# Cycle 1 — ok=True when both credential types present
# ---------------------------------------------------------------------------

class TestBothCredentialsPresent:
    @pytest.mark.asyncio
    async def test_returns_ok_true_when_both_present(self):
        """check_credentials returns ok=True when googleOAuth2Api and zoomOAuth2Api are present."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": _BOTH_CREDS}

        result = await check_credentials(fake_fetch)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_result_contains_credentials_list(self):
        """check_credentials result includes a 'credentials' key listing found credentials."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": _BOTH_CREDS}

        result = await check_credentials(fake_fetch)
        assert "credentials" in result
        types = [c["type"] for c in result["credentials"]]
        assert "googleOAuth2Api" in types
        assert "zoomOAuth2Api" in types

    @pytest.mark.asyncio
    async def test_missing_list_empty_when_all_present(self):
        """'missing' list is empty when both required credential types are present."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": _BOTH_CREDS}

        result = await check_credentials(fake_fetch)
        assert result["missing"] == []


# ---------------------------------------------------------------------------
# Cycle 2 — ok=False + missing list when a credential type is absent
# ---------------------------------------------------------------------------

class TestMissingCredential:
    @pytest.mark.asyncio
    async def test_returns_ok_false_when_zoom_missing(self):
        """check_credentials returns ok=False when zoomOAuth2Api credential is absent."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": _GOOGLE_ONLY}

        result = await check_credentials(fake_fetch)
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_missing_list_contains_absent_type(self):
        """'missing' list names the absent credential type."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": _GOOGLE_ONLY}

        result = await check_credentials(fake_fetch)
        assert "zoomOAuth2Api" in result["missing"]

    @pytest.mark.asyncio
    async def test_returns_ok_false_when_all_missing(self):
        """check_credentials returns ok=False and both types in missing when vault is empty."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            return {"data": _EMPTY_CREDS}

        result = await check_credentials(fake_fetch)
        assert result["ok"] is False
        assert "googleOAuth2Api" in result["missing"]
        assert "zoomOAuth2Api" in result["missing"]


# ---------------------------------------------------------------------------
# Cycle 3 — ok=False when n8n is unreachable
# ---------------------------------------------------------------------------

class TestUnreachable:
    @pytest.mark.asyncio
    async def test_returns_ok_false_when_fetch_raises(self):
        """check_credentials returns ok=False when the HTTP call raises (n8n not running)."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise ConnectionRefusedError("Connection refused")

        result = await check_credentials(fake_fetch)
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_result_has_error_key_when_unreachable(self):
        """When n8n is unreachable, result contains an 'error' key describing the failure."""
        from scripts.n8n_check_credentials import check_credentials

        async def fake_fetch(method, url, *, headers=None, json=None):
            raise ConnectionRefusedError("Connection refused")

        result = await check_credentials(fake_fetch)
        assert "error" in result
        assert result["error"]


# ---------------------------------------------------------------------------
# Cycle 4 — sends correct auth header
# ---------------------------------------------------------------------------

class TestAuthHeader:
    @pytest.mark.asyncio
    async def test_sends_api_key_header(self):
        """check_credentials passes the API key in the X-N8N-API-KEY header."""
        from scripts.n8n_check_credentials import check_credentials

        seen_headers: dict = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_headers.update(headers or {})
            return {"data": _BOTH_CREDS}

        await check_credentials(fake_fetch, api_key="test-secret-key")
        assert seen_headers.get("X-N8N-API-KEY") == "test-secret-key"

    @pytest.mark.asyncio
    async def test_api_key_read_from_env(self, monkeypatch):
        """API key is read from N8N_API_KEY env var when not passed explicitly."""
        from scripts.n8n_check_credentials import check_credentials

        seen_headers: dict = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_headers.update(headers or {})
            return {"data": _BOTH_CREDS}

        monkeypatch.setenv("N8N_API_KEY", "env-key")
        await check_credentials(fake_fetch)
        assert seen_headers.get("X-N8N-API-KEY") == "env-key"


# ---------------------------------------------------------------------------
# Cycle 5 — hits /api/v1/credentials endpoint
# ---------------------------------------------------------------------------

class TestEndpoint:
    @pytest.mark.asyncio
    async def test_calls_credentials_endpoint(self):
        """check_credentials calls GET /api/v1/credentials on the n8n host."""
        from scripts.n8n_check_credentials import check_credentials

        seen: dict = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen["method"] = method
            seen["url"] = url
            return {"data": _BOTH_CREDS}

        await check_credentials(fake_fetch)
        assert seen["method"] == "GET"
        assert "/api/v1/credentials" in seen["url"]


# ---------------------------------------------------------------------------
# Cycle 6 — accepts base_url override
# ---------------------------------------------------------------------------

class TestBaseUrl:
    @pytest.mark.asyncio
    async def test_base_url_override_used_in_request(self):
        """check_credentials uses the base_url argument when provided."""
        from scripts.n8n_check_credentials import check_credentials

        seen_url: dict = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_url["url"] = url
            return {"data": _BOTH_CREDS}

        await check_credentials(fake_fetch, base_url="http://n8n.internal:9999")
        assert "n8n.internal:9999" in seen_url["url"]

    @pytest.mark.asyncio
    async def test_default_base_url_is_localhost_5678(self):
        """Default base_url is http://localhost:5678."""
        from scripts.n8n_check_credentials import check_credentials

        seen_url: dict = {}

        async def fake_fetch(method, url, *, headers=None, json=None):
            seen_url["url"] = url
            return {"data": _BOTH_CREDS}

        await check_credentials(fake_fetch)
        assert "localhost:5678" in seen_url["url"]
