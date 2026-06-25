"""Browser-automation harness for Felix (logged-in session reuse).

See ``session.py`` for the credential-aware login orchestrator. ADR-0005
amendment 2026-06-25 governs the credential storage this consumes.
"""

from cerebral.browser.session import (
    BrowserSession,
    BrowserDriver,
    LoginResult,
    LoginState,
)

__all__ = [
    "BrowserSession",
    "BrowserDriver",
    "LoginResult",
    "LoginState",
]
