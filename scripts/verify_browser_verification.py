"""
Verify the browser verification-wall escalation (harness step S8b).

Drives the REAL browser_open_session plugin against the logged-in google_web
session. It first tries the saved session unattended; when Google throws a
"verify it's you" step-up wall it escalates -- prints a notification line and
opens a VISIBLE Chromium window for you to clear the wall by hand. Once you
finish, the tool resumes and reports state + verified:true.

WHY A STANDALONE SCRIPT: a browser launched from the agent's Bash/tool
subprocess shows no visible window on the user's interactive desktop (see
.learnings/LEARNINGS.md, 2026-06-25). The escalation window can only appear
when the plugin runs in your own session -- this script, or Cerebral itself.
Run it from a normal terminal:

    python scripts/verify_browser_verification.py            # active profile
    python scripts/verify_browser_verification.py --profile 4

Expected outcomes:
  - Saved session still trusted:   REUSED   (nothing to click)
  - Reuse failed, you sign in:     verified:true (window opened, then resumed)
  - You don't finish in time:      FAILED   (re-run and complete the sign-in)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow `import cerebral...` / `import plugins...` when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cerebral.browser import BrowserSession  # noqa: E402
from cerebral.browser.session import DEFAULT_PROVIDER, PlaywrightDriver  # noqa: E402
from cerebral.db.credentials import CredentialStore  # noqa: E402
from cerebral.db.profiles import ProfileManager  # noqa: E402
from plugins.browser_session import BrowserSessionPlugin  # noqa: E402

# The escalation window must stay open for a human step-up (possibly phone 2FA).
_VERIFY_TIMEOUT = 300.0


def _resolve_profile_id(arg: int | None) -> int | None:
    if arg is not None:
        return arg
    profile = ProfileManager().get_active()
    return None if profile is None else profile.id


async def _print_notifier(title: str, body: str) -> None:
    print(f"\n[notification] {title}\n              {body}\n")


async def _verify(profile_id: int) -> int:
    store = CredentialStore()
    cred = store.get_credential(profile_id, DEFAULT_PROVIDER)
    if not cred or not cred.get("email"):
        print(
            f"No '{DEFAULT_PROVIDER}' session seeded for profile {profile_id}. "
            "Seed one first:\n    python scripts/seed_browser_login.py"
        )
        return 1

    # Each factory() call must hand back a fresh, not-yet-opened session with
    # its own driver: the plugin closes the unattended attempt (to release the
    # persistent-context dir lock) before re-opening headed for the escalation.
    def factory() -> BrowserSession:
        return BrowserSession(
            profile_id,
            driver=PlaywrightDriver(),
            store=store,
            manual_login_timeout=_VERIFY_TIMEOUT,
        )

    plugin = BrowserSessionPlugin(
        factory,
        notifier=_print_notifier,
        pause_check=lambda: True,
    )

    print(
        f"Opening browser session for profile {profile_id}, provider "
        f"'{DEFAULT_PROVIDER}' ({cred['email']}).\n"
        "If the saved session can't be reused (expired, or a 'verify it's you'\n"
        "wall), a Chromium window will open -- complete the sign-in by hand "
        f"within {_VERIFY_TIMEOUT:.0f}s. Do NOT close it yourself.\n"
    )

    result = await plugin.call_tool("browser_open_session", {})
    if plugin._open_session is not None:
        await plugin._open_session.close()

    if result.is_error:
        print(f"\nFAILED: {result.content}")
        return 1

    payload = json.loads(result.content)
    print(f"\nSUCCESS: {json.dumps(payload)}")
    if payload.get("verified"):
        print("The verification wall was cleared and the session resumed.")
    else:
        print(
            f"No wall this time (state={payload.get('state')}); the saved "
            "session is still trusted. To exercise the wall, retry after "
            "Google's next step-up."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=int, default=None,
        help="Profile id to verify (default: the active profile).",
    )
    args = parser.parse_args()

    profile_id = _resolve_profile_id(args.profile)
    if profile_id is None:
        print("No profile exists yet. Run Cerebral once before verifying.")
        return 1

    return asyncio.run(_verify(profile_id))


if __name__ == "__main__":
    raise SystemExit(main())
