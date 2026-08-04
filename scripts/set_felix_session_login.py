"""Store the Felix isolated-session Windows login in the OS credential store.

This is the credential-entry half of #604 (isolated interactive session, ADR-0016
amendment). It writes the Felix *Windows account* password to Windows Credential
Manager via keyring, under a pinned key the provisioning code reads back:

    service  = openmind-felix-session
    username = Felix   (the dedicated standard Windows user)

Nothing consumes this yet -- #604's auto-provisioning (loopback RDP ->
CreateProcessAsUser) is the reader. Storing early is safe: it is your own
password in your own vault. The password is prompted (never echoed, never in
argv/history) and never printed back.

Run via scripts/set-felix-session-login.ps1 (double-click) or directly:
    python scripts/set_felix_session_login.py [--user Felix]
"""
from __future__ import annotations

import argparse
import getpass
import sys

SERVICE = "openmind-felix-session"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="Felix",
                    help="the Windows username for Felix's session (default: Felix)")
    ap.add_argument("--service", default=SERVICE,
                    help=f"keyring service name (default: {SERVICE})")
    args = ap.parse_args()

    try:
        import keyring
    except Exception as exc:  # pragma: no cover - env guard
        print(f"FAILED: keyring not importable ({exc}). Run: pip install keyring")
        return 1

    kr = keyring.get_keyring()
    backend = f"{kr.__class__.__module__}.{kr.__class__.__name__}"
    if "Windows" not in backend and "WinVault" not in backend:
        print(f"FAILED: keyring backend is {backend}, not the Windows Credential "
              "Manager. Refusing to store the Windows login in a non-OS vault.")
        return 1

    user = args.user.strip() or "Felix"
    print(f"Storing Felix session login  service={args.service!r}  user={user!r}")
    print("(the dedicated standard Windows user -- NOT your own account)\n")

    pw = getpass.getpass(f"Windows password for '{user}': ")
    if not pw:
        print("FAILED: empty password, nothing stored.")
        return 1
    confirm = getpass.getpass("Re-enter to confirm: ")
    if pw != confirm:
        print("FAILED: passwords did not match, nothing stored.")
        return 1

    keyring.set_password(args.service, user, pw)

    # Self-check: read it back (verifies the vault actually persisted it).
    got = keyring.get_password(args.service, user)
    if got != pw:
        print("FAILED: stored the credential but read-back did not match.")
        return 1

    print(f"\nSUCCESS: Felix session login stored in Windows Credential Manager "
          f"(service={args.service!r}, user={user!r}).")
    print("#604 auto-provisioning will read it from there. It is never logged or printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
