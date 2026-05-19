"""
Google OAuth2 installed-app consent flow — Issue #113, ADR-0005.

A cerebral-core producer slice (no UI of its own — issue C/#114 drives it
from the tray). Given a profile's ``client_id``/``client_secret`` already
persisted in the #112 ``CredentialStore``, it runs the OAuth2
**installed-app** dance (local-loopback redirect, PKCE S256 + ``state``),
obtains a **refresh token** for the requested Gmail/Calendar scopes, and
writes ``refresh_token`` + initial ``access_token`` back into the store for
the given profile. It also exchanges a stored ``refresh_token`` for a fresh
``access_token`` so plugins (#115/#117) can call Google APIs.

Hand-rolled against Google's token endpoint with **stdlib only** (no
``google-auth-oauthlib``): ``urllib`` for the token POST, ``http.server``
for the one-shot loopback listener, ``webbrowser`` for the opener,
``secrets``/``hashlib``/``base64`` for PKCE+state. The HTTP transport, the
browser opener and the loopback listener are all injectable so the consent
+ exchange are fully unit-testable with no real browser, socket or network.

Secret material (``client_secret``, ``refresh_token``, ``access_token``) is
read/written only through the #112 store (keyring-backed) and is never
logged. Storage is owned entirely by #112 — this module never touches the
keyring or SQLite directly.

Public interface::

    flow = GoogleOAuthFlow(store)                       # production
    flow = GoogleOAuthFlow(store, fetch_fn=…, browser_opener=…,
                           redirect_handler=…)           # tests

    flow.start_consent(profile_id, scopes=["https://.../gmail.readonly"])
    flow.refresh_access_token(profile_id)                # → access_token str
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import urllib.parse
import urllib.request
from typing import Any, Callable

from cerebral.db.credentials import CredentialStore

logger = logging.getLogger(__name__)

_PROVIDER = "google"
_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_DEFAULT_CONSENT_TIMEOUT = 300  # seconds the loopback listener waits


class GoogleOAuthError(RuntimeError):
    """Any failure of the consent / refresh flow. Raised with a clear
    message; nothing partial is persisted when this is raised."""


# ── default stdlib transports (injectable; replaced by stubs in tests) ────────

def _default_fetch(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    """POST/GET JSON via stdlib urllib. Returns the parsed JSON body.
    Raises on any transport/HTTP error (caller maps to GoogleOAuthError)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=body, method=method, headers=headers or {}
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (fixed Google host)
        return json.loads(resp.read().decode())


def _default_browser_opener(url: str) -> None:
    import webbrowser  # stdlib; deferred so a headless host import is cheap

    webbrowser.open(url)


def _default_redirect_handler(
    build_auth_url: Callable[[str], str],
    *,
    state: str,
    browser_opener: Callable[[str], None],
    timeout: int,
) -> dict:
    """Bind an ephemeral loopback port, open the browser at the auth URL,
    and serve exactly one request (the OAuth callback). Returns
    ``{"code","state","redirect_uri"}`` or ``{"error":...}``.

    Localhost loopback with an OS-assigned port is the installed-app
    redirect Google registers for desktop clients.
    """
    import http.server

    captured: dict[str, str] = {}

    class _OneShot(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API name)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                b"OpenMind: consent received. You can close this tab."
            )

        def log_message(self, *_: Any) -> None:
            pass  # never log query strings (may carry the code)

    server = http.server.HTTPServer(("127.0.0.1", 0), _OneShot)
    server.timeout = timeout
    try:
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}/"
        browser_opener(build_auth_url(redirect_uri))
        server.handle_request()  # blocks until the one callback (or timeout)
    finally:
        server.server_close()

    if "error" in captured:
        return {"error": captured["error"]}
    if "code" not in captured:
        return {"error": "no authorization code received (consent timed out)"}
    return {
        "code": captured["code"],
        "state": captured.get("state", ""),
        "redirect_uri": redirect_uri,
    }


# ── flow ──────────────────────────────────────────────────────────────────────

class GoogleOAuthFlow:
    def __init__(
        self,
        store: CredentialStore,
        *,
        fetch_fn: Callable[..., dict] | None = None,
        browser_opener: Callable[[str], None] | None = None,
        redirect_handler: Callable[..., dict] | None = None,
        consent_timeout: int = _DEFAULT_CONSENT_TIMEOUT,
    ) -> None:
        self._store = store
        self._fetch = fetch_fn or _default_fetch
        self._open_browser = browser_opener or _default_browser_opener
        self._redirect = redirect_handler or _default_redirect_handler
        self._consent_timeout = consent_timeout

    # ── consent ───────────────────────────────────────────────────────────────

    def start_consent(self, profile_id: int, *, scopes: list[str]) -> dict:
        """Run the installed-app consent flow for ``profile_id`` and persist
        the resulting tokens. Returns a connection-status dict on success;
        raises :class:`GoogleOAuthError` (persisting nothing) on any failure.
        """
        client_id, client_secret = self._read_client(profile_id)

        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        def build_auth_url(redirect_uri: str) -> str:
            params = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
            }
            return f"{_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"

        result = self._redirect(
            build_auth_url,
            state=state,
            browser_opener=self._open_browser,
            timeout=self._consent_timeout,
        )
        if "error" in result:
            raise GoogleOAuthError(f"consent failed: {result['error']}")
        if result.get("state") != state:
            raise GoogleOAuthError("consent failed: state mismatch")

        try:
            tokens = self._fetch(
                _TOKEN_ENDPOINT,
                method="POST",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": result["code"],
                    "code_verifier": verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": result["redirect_uri"],
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as exc:  # transport/HTTP/JSON — never leak the code
            raise GoogleOAuthError("token exchange failed") from exc

        refresh_token = tokens.get("refresh_token")
        access_token = tokens.get("access_token")
        if not refresh_token or not access_token:
            raise GoogleOAuthError(
                "token exchange returned no refresh/access token"
            )

        # Persist only after a fully successful exchange (no partial writes).
        self._store.set_secret(
            profile_id, _PROVIDER, "refresh_token", refresh_token
        )
        self._store.set_secret(
            profile_id, _PROVIDER, "access_token", access_token
        )
        # Re-pass the existing client_id/email so the #112 upsert does not
        # blank them (set_credential overwrites every column it is given).
        existing = self._store.get_credential(profile_id, _PROVIDER) or {}
        self._store.set_credential(
            profile_id,
            _PROVIDER,
            client_id=existing.get("client_id", ""),
            email=existing.get("email", ""),
            scopes=scopes,
            status="connected",
        )
        logger.info(
            "[google_oauth] consent connected profile=%d scopes=%d",
            profile_id, len(scopes),
        )
        return {
            "status": "connected",
            "profile_id": profile_id,
            "scopes": scopes,
        }

    # ── refresh ───────────────────────────────────────────────────────────────

    def refresh_access_token(self, profile_id: int) -> str:
        """Exchange the stored ``refresh_token`` for a fresh
        ``access_token``, update #112, and return it. Raises
        :class:`GoogleOAuthError` (persisting nothing) on any failure."""
        client_id, client_secret = self._read_client(profile_id)
        refresh_token = self._store.get_secret(
            profile_id, _PROVIDER, "refresh_token"
        )
        if not refresh_token:
            raise GoogleOAuthError(
                f"no refresh_token stored for profile {profile_id}"
            )

        try:
            tokens = self._fetch(
                _TOKEN_ENDPOINT,
                method="POST",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception as exc:
            raise GoogleOAuthError("token refresh failed") from exc

        access_token = tokens.get("access_token")
        if not access_token:
            raise GoogleOAuthError("token refresh returned no access token")

        self._store.set_secret(
            profile_id, _PROVIDER, "access_token", access_token
        )
        logger.info(
            "[google_oauth] access token refreshed profile=%d", profile_id
        )
        return access_token

    # ── shared ────────────────────────────────────────────────────────────────

    def _read_client(self, profile_id: int) -> tuple[str, str]:
        """Read the precondition client_id (metadata) + client_secret
        (keyring) set earlier by #114. Raises if either is absent."""
        meta = self._store.get_credential(profile_id, _PROVIDER)
        client_id = (meta or {}).get("client_id") or ""
        client_secret = self._store.get_secret(
            profile_id, _PROVIDER, "client_secret"
        )
        if not client_id or not client_secret:
            raise GoogleOAuthError(
                f"no client_id/client_secret stored for profile {profile_id}"
            )
        return client_id, client_secret
