"""
Discord auto-reply allowlist + settings CLI -- Issue #177, ADR-0006.

Talks straight to ``cerebral/db/profiles.py``'s SQLite store. Runs
fine while Cerebral is running -- SQLite handles concurrent readers;
the auto-reply controller re-reads the allowlist on every inbound DM
so changes apply immediately without restart.

Usage
-----

  python scripts/discord_user_allowlist.py list
      List the allowlisted senders for the active profile.

  python scripts/discord_user_allowlist.py add <sender_id> [--note "..."]
      Allowlist a sender. ``sender_id`` is the Discord user-id snowflake
      (find via discord_get_messages / discord_list_conversations).

  python scripts/discord_user_allowlist.py remove <sender_id>
      Drop a sender from the allowlist.

  python scripts/discord_user_allowlist.py settings show
      Print every detection-mitigation setting + its effective value
      (default merged with any override).

  python scripts/discord_user_allowlist.py settings set <key> <value>
      Override one setting. See the keys listed by 'settings show'.

  python scripts/discord_user_allowlist.py settings clear <key>
      Remove an override (fall back to the default).

Use ``--profile <id>`` to target a specific profile id instead of the
active one. The "active profile" is the one Cerebral last switched
to via the tray (matches ``ProfileManager.get_active``).

Detection-mitigation defaults are all "on" except sleep-hours, which
is off by default and configurable per profile (Issue #177 acceptance
criterion).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cerebral.db.profiles import ProfileManager
from cerebral.discord_auto_reply import (  # noqa: E402
    ALLOWED_SETTING_KEYS as AUTO_REPLY_KEYS,
    _DEFAULTS as AUTO_REPLY_DEFAULTS,
    settings_from_overrides,
)
from cerebral.discord_presence import (  # noqa: E402
    PRESENCE_ALLOWED_SETTING_KEYS,
    _DEFAULTS as PRESENCE_DEFAULTS,
    presence_settings_from_overrides,
)

# Slice 2 (#177) + slice 3 (#178) settings share the discord_user_settings
# table; the CLI surfaces both namespaces in one ``settings`` subcommand.
ALLOWED_SETTING_KEYS = AUTO_REPLY_KEYS | PRESENCE_ALLOWED_SETTING_KEYS
SETTING_DEFAULTS: dict[str, str] = {**AUTO_REPLY_DEFAULTS, **PRESENCE_DEFAULTS}


def _resolve_profile(pm: ProfileManager, explicit: int | None):
    if explicit is not None:
        p = pm.get(explicit)
        if p is None:
            print(f"error: no profile with id={explicit}", file=sys.stderr)
            sys.exit(2)
        return p
    active = pm.get_active()
    if active is None:
        print(
            "error: no active profile -- create one via the tray first, "
            "or pass --profile <id>",
            file=sys.stderr,
        )
        sys.exit(2)
    return active


def cmd_list(pm: ProfileManager, args) -> int:
    profile = _resolve_profile(pm, args.profile)
    rows = pm.list_discord_allowlist(profile.id)
    if not rows:
        print(
            f"(allowlist for profile {profile.name!r} (id={profile.id}) is empty)"
        )
        return 0
    print(f"Discord auto-reply allowlist for profile {profile.name!r} (id={profile.id}):")
    width = max(len(r["sender_id"]) for r in rows)
    for r in rows:
        note = f"  -- {r['note']}" if r["note"] else ""
        print(f"  {r['sender_id'].ljust(width)}  added={r['added_at']}{note}")
    return 0


def cmd_add(pm: ProfileManager, args) -> int:
    profile = _resolve_profile(pm, args.profile)
    pm.add_discord_allowlist(profile.id, args.sender_id, note=args.note or "")
    print(
        f"added {args.sender_id} to allowlist for profile "
        f"{profile.name!r} (id={profile.id})"
    )
    return 0


def cmd_remove(pm: ProfileManager, args) -> int:
    profile = _resolve_profile(pm, args.profile)
    if pm.remove_discord_allowlist(profile.id, args.sender_id):
        print(
            f"removed {args.sender_id} from allowlist for profile "
            f"{profile.name!r}"
        )
        return 0
    print(
        f"warning: {args.sender_id} was not on the allowlist for profile "
        f"{profile.name!r}",
        file=sys.stderr,
    )
    return 1


def cmd_settings_show(pm: ProfileManager, args) -> int:
    profile = _resolve_profile(pm, args.profile)
    overrides = pm.list_discord_settings(profile.id)
    auto_reply_settings = settings_from_overrides(overrides)
    presence_settings = presence_settings_from_overrides(overrides)
    print(
        f"Discord settings for profile {profile.name!r} "
        f"(id={profile.id}):"
    )
    rows: list[tuple[str, str, str, str]] = []  # (key, effective, default, source)
    for key, default in SETTING_DEFAULTS.items():
        effective_raw = overrides.get(key, default)
        source = "override" if key in overrides else "default"
        rows.append((key, effective_raw, default, source))
    kw = max(len(r[0]) for r in rows)
    for key, eff, default, source in rows:
        line = f"  {key.ljust(kw)}  {eff!r:<10}  ({source}; default={default!r})"
        print(line)
    print()
    print("Parsed (auto-reply):")
    for field_name, value in auto_reply_settings.__dict__.items():
        print(f"  {field_name:<30} {value!r}")
    print("Parsed (presence):")
    for field_name, value in presence_settings.__dict__.items():
        print(f"  {field_name:<30} {value!r}")
    return 0


def cmd_settings_set(pm: ProfileManager, args) -> int:
    profile = _resolve_profile(pm, args.profile)
    if args.key not in ALLOWED_SETTING_KEYS:
        print(
            f"error: unknown setting key {args.key!r}; valid keys: "
            f"{sorted(ALLOWED_SETTING_KEYS)}",
            file=sys.stderr,
        )
        return 2
    pm.set_discord_setting(profile.id, args.key, args.value)
    print(
        f"set {args.key}={args.value!r} for profile "
        f"{profile.name!r}"
    )
    return 0


def cmd_settings_clear(pm: ProfileManager, args) -> int:
    profile = _resolve_profile(pm, args.profile)
    if pm.clear_discord_setting(profile.id, args.key):
        print(f"cleared {args.key} override for profile {profile.name!r}")
        return 0
    print(
        f"warning: no override for {args.key} on profile {profile.name!r}",
        file=sys.stderr,
    )
    return 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="discord_user_allowlist.py",
        description=(
            "Manage the Discord auto-reply allowlist + detection-mitigation "
            "settings for plugins/discord_user.py (Issue #177)."
        ),
    )
    p.add_argument(
        "--profile", type=int, default=None,
        help="Profile id to act on (default: the active profile)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List allowlisted senders.").set_defaults(
        func=cmd_list,
    )

    add_p = sub.add_parser("add", help="Allowlist a sender by Discord user-id.")
    add_p.add_argument("sender_id")
    add_p.add_argument("--note", default="", help="Optional free-text note.")
    add_p.set_defaults(func=cmd_add)

    rm_p = sub.add_parser("remove", help="Remove a sender from the allowlist.")
    rm_p.add_argument("sender_id")
    rm_p.set_defaults(func=cmd_remove)

    settings_p = sub.add_parser(
        "settings", help="Manage detection-mitigation settings.",
    )
    settings_sub = settings_p.add_subparsers(dest="settings_cmd", required=True)
    settings_sub.add_parser(
        "show", help="Print every setting + its effective value.",
    ).set_defaults(func=cmd_settings_show)
    set_p = settings_sub.add_parser("set", help="Override a setting.")
    set_p.add_argument("key")
    set_p.add_argument("value")
    set_p.set_defaults(func=cmd_settings_set)
    clear_p = settings_sub.add_parser(
        "clear", help="Drop an override (fall back to default).",
    )
    clear_p.add_argument("key")
    clear_p.set_defaults(func=cmd_settings_clear)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    pm = ProfileManager()
    return args.func(pm, args)


if __name__ == "__main__":
    sys.exit(main())
