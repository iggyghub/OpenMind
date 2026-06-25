"""
Set the dedicated browser-automation web-login credentials (ADR-0005
amendment 2026-06-25).

Stores a secondary web account's email + password for the browser-automation
harness under the `google_web` provider, scoped to the active profile:

  - email    -> connected_account_credentials.email  (non-secret, SQLite)
  - password -> OS keyring  (profile_<id>/google_web/password)

The password is read via getpass, so it never lands in shell history, a
.env file, or any committed file. It is never echoed back or logged.

Run from a normal terminal (no env vars needed):

    python scripts/set_browser_credentials.py

NOTE: storing a raw password is the *unattended re-login fallback*. The
recommended path is to log in by hand once into the Playwright persistent
context and reuse the saved session (storageState) -- see the 2026-06-25
ADR-0005 amendment. Only set a password here if Felix must re-login with
nobody at the keyboard.
"""

import getpass
import sys
from pathlib import Path

# Allow `import cerebral...` when run as `python scripts/set_browser_credentials.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cerebral.db.credentials import CredentialStore  # noqa: E402
from cerebral.db.profiles import ProfileManager       # noqa: E402

PROVIDER = "google_web"


def main() -> int:
    pm = ProfileManager()
    profile = pm.get_active()
    if profile is None:
        print("No profile exists yet. Create one (run Cerebral once) before "
              "setting browser credentials.")
        return 1

    print(f"Setting browser-automation credentials for profile "
          f"{profile.id} ({profile.name!r}), provider '{PROVIDER}'.")
    print("This should be a DEDICATED secondary account, never your primary "
          "identity.\n")

    email = input("Account email: ").strip()
    if not email:
        print("Email is required. Aborted.")
        return 1

    password = getpass.getpass("Account password (input hidden): ")
    if not password:
        print("Empty password. Aborted (nothing written).")
        return 1
    confirm = getpass.getpass("Re-enter password to confirm: ")
    if password != confirm:
        print("Passwords did not match. Aborted (nothing written).")
        return 1

    store = CredentialStore()
    try:
        # Secret first: if the keyring is unavailable this raises before we
        # write a metadata row that would imply a stored secret that isn't.
        store.set_secret(profile.id, PROVIDER, "password", password)
    except RuntimeError as exc:
        print(f"\nCould not store the password: {exc}")
        return 1

    # Explicit full row -- set_credential defaults omitted columns to ""/[]
    # and would silently blank a future metadata extension (the #112
    # upsert-blanking trap).
    store.set_credential(
        profile.id, PROVIDER,
        client_id="", email=email, scopes=[], status="connected",
    )

    print(f"\nSUCCESS: stored email + password for '{PROVIDER}' "
          f"(profile {profile.id}). The password is in the OS keyring; "
          f"it was not echoed or logged.")
    print("To remove later: CredentialStore().delete_credential("
          f"{profile.id}, '{PROVIDER}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
