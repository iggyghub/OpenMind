"""Shared yt-dlp cookie helper (ADR-0017).

Every yt-dlp path -- audio download, keyframe/visual download, channel
enumerate -- must authenticate with Felix's logged-in ``google_web`` profile.
YouTube 403s / rate-blocks anonymous yt-dlp hard, and it hits each path
separately (the audio path was fixed first, then the keyframe path still 403'd).

Best-effort: if no profile with a cookie DB exists, the tuple/args are empty and
yt-dlp runs anonymously (unchanged prior behaviour).
"""
from __future__ import annotations

from cerebral.paths import data_dir


def browser_profile_dir() -> "str | None":
    """User-data-dir of Felix's logged-in google_web Chromium profile, or None."""
    try:
        root = data_dir() / "browser"
        for prof in sorted(root.glob("profile_*/google_web")):
            if (prof / "Default" / "Network" / "Cookies").exists():
                return str(prof)
    except Exception:  # noqa: BLE001 -- cookie lookup must never break a download
        pass
    return None


def cookiesfrombrowser_tuple() -> "tuple | None":
    """yt-dlp Python-API `cookiesfrombrowser` value: (browser, profile, keyring, container)."""
    d = browser_profile_dir()
    return ("chrome", d, None, None) if d else None


def cookies_cli_args() -> "list[str]":
    """yt-dlp CLI args for the subprocess paths: ['--cookies-from-browser', 'chrome:<dir>']."""
    d = browser_profile_dir()
    return ["--cookies-from-browser", f"chrome:{d}"] if d else []
