"""
n8n credential health-check — Issue #19.

Verifies that the required OAuth credentials (Google, Zoom) are configured
in the local n8n instance. Used by Felix/Cerebral to gate downstream cloud
workflows and by the human operator as a one-shot diagnostic.

Usage:
    python scripts/n8n_check_credentials.py

Exit codes:
    0 — all required credentials present
    1 — one or more credentials missing, or n8n unreachable

Environment:
    N8N_API_KEY — n8n API key (default: "changeme")
"""
import asyncio
import os
import sys
from typing import Any, Awaitable, Callable

FetchFn = Callable[..., Awaitable[dict]]

_DEFAULT_BASE_URL = "http://localhost:5678"
_DEFAULT_API_KEY = "changeme"

_REQUIRED_TYPES = ["googleOAuth2Api", "zoomOAuth2Api"]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None, json: dict | None = None) -> dict:
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, json=json) as resp:
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers, json=json)
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed")


async def check_credentials(
    fetch_fn: FetchFn | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Check whether required OAuth credentials exist in n8n.

    Returns a dict with keys:
        ok          — True if all required credential types are present
        credentials — list of credential objects from n8n
        missing     — list of credential type strings that were not found
        error       — present (non-empty string) when n8n was unreachable
    """
    fetch = fetch_fn or _default_fetch
    base = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    key = api_key or os.environ.get("N8N_API_KEY", _DEFAULT_API_KEY)
    headers = {"X-N8N-API-KEY": key}
    url = f"{base}/api/v1/credentials"

    try:
        resp = await fetch("GET", url, headers=headers)
    except Exception as exc:
        return {"ok": False, "credentials": [], "missing": list(_REQUIRED_TYPES), "error": str(exc)}

    credentials = resp.get("data", [])
    present_types = {c.get("type") for c in credentials}
    missing = [t for t in _REQUIRED_TYPES if t not in present_types]

    return {
        "ok": len(missing) == 0,
        "credentials": credentials,
        "missing": missing,
    }


def main() -> None:
    result = asyncio.run(check_credentials())

    if result.get("error"):
        print(f"ERROR: Could not reach n8n — {result['error']}")
        print("Is n8n running at http://localhost:5678?")
        sys.exit(1)

    print(f"n8n credentials check — {len(result['credentials'])} credential(s) found")
    for cred in result["credentials"]:
        marker = "✓" if cred.get("type") in _REQUIRED_TYPES else " "
        print(f"  [{marker}] {cred.get('name', '?')}  ({cred.get('type', '?')})")

    if result["missing"]:
        print("\nMISSING required credentials:")
        for t in result["missing"]:
            print(f"  ✗ {t}")
        print("\nRun: docs/setup/n8n-credentials.md to configure them.")
        sys.exit(1)

    print("\nAll required credentials are configured.")
    sys.exit(0)


if __name__ == "__main__":
    main()
