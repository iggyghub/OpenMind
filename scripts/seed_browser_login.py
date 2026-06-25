"""
Seed the first logged-in browser session for the browser-automation harness
(ADR-0005 amendment 2026-06-25).

Opens a VISIBLE Chromium window on the provider's login page and waits for you
to complete login + 2FA by hand. The Playwright persistent context captures
the authenticated session to disk under

    cerebral/data/browser/profile_<id>/google_web/

so subsequent runs reuse it without touching the password. This is the
*attended* seed step; the stored password is only the unattended re-login
fallback.

WHY A STANDALONE SCRIPT: a browser launched from the agent's Bash/tool
subprocess shows no visible window on the user's interactive desktop (see
.learnings/LEARNINGS.md, 2026-06-25). Any flow needing a visible window must
run in the user's own session -- this script, or Cerebral itself. Run it from
a normal terminal:

    python scripts/seed_browser_login.py            # active profile
    python scripts/seed_browser_login.py --profile 4

Expected outcomes:
  - First run, after you log in:  MANUAL   (session captured)
  - Second run, no login needed:  REUSED   (session reused from disk)
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `import cerebral...` when run as `python scripts/seed_browser_login.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cerebral.browser import BrowserSession, LoginState  # noqa: E402
from cerebral.browser.session import (  # noqa: E402
    DEFAULT_PROVIDER,
    PlaywrightDriver,
)
from cerebral.db.credentials import CredentialStore  # noqa: E402
from cerebral.db.profiles import ProfileManager  # noqa: E402

# The seed window must stay open long enough for a human login + 2FA. The
# harness default (180s) is fine for a returning login; allow more here since
# a first-time login may include a phone-based 2FA prompt.
_SEED_TIMEOUT = 300.0


def _resolve_profile_id(arg: int | None) -> int | None:
    if arg is not None:
        return arg
    profile = ProfileManager().get_active()
    return None if profile is None else profile.id


async def _seed(profile_id: int) -> int:
    store = CredentialStore()
    cred = store.get_credential(profile_id, DEFAULT_PROVIDER)
    if not cred or not cred.get("email"):
        print(
            f"No '{DEFAULT_PROVIDER}' email stored for profile {profile_id}. "
            "Set it first:\n    python scripts/set_browser_credentials.py"
        )
        return 1

    email = cred["email"]
    print(
        f"Seeding browser login for profile {profile_id}, provider "
        f"'{DEFAULT_PROVIDER}' ({email}).\n"
        "A Chromium window will open. Complete the login (and 2FA) by hand.\n"
        f"You have {_SEED_TIMEOUT:.0f}s. Do NOT close the window yourself --\n"
        "it closes automatically once login is detected.\n"
    )

    session = BrowserSession(
        profile_id,
        driver=PlaywrightDriver(),
        store=store,
        manual_login_timeout=_SEED_TIMEOUT,
    )
    try:
        result = await session.ensure_logged_in(unattended=False)
    finally:
        await session.close()

    if result.state is LoginState.REUSED:
        print(
            f"\nREUSED: an authenticated session already existed on disk for "
            f"profile {profile_id} -- nothing to do. The harness is seeded."
        )
        return 0
    if result.state is LoginState.MANUAL:
        print(
            f"\nMANUAL: login completed and captured for {result.email}. "
            f"Session saved under cerebral/data/browser/profile_{profile_id}/"
            f"{DEFAULT_PROVIDER}/.\nRun this script again to confirm it returns "
            "REUSED."
        )
        return 0
    print(
        f"\nFAILED: {result.reason or 'no session established'}. "
        "Re-run and finish the login within the time limit."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=int, default=None,
        help="Profile id to seed (default: the active profile).",
    )
    args = parser.parse_args()

    profile_id = _resolve_profile_id(args.profile)
    if profile_id is None:
        print(
            "No profile exists yet. Create one (run Cerebral once) before "
            "seeding a browser login."
        )
        return 1

    return asyncio.run(_seed(profile_id))


if __name__ == "__main__":
    raise SystemExit(main())
