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


# YouTube 403s the default 'web' client's media URL for some videos (player
# signature / nsig churn) even with valid cookies -- info extraction succeeds
# but the audio fragment download is Forbidden. The 'android' client serves a
# directly downloadable audio format; the rest are fallbacks yt-dlp tries in
# order when android is unavailable (e.g. PO-token-gated). This bit us on the
# single-video path (#17013, "Rethinking AI Harnesses" 403'd stuck at enumerated).
_PLAYER_CLIENTS = ["android", "web_safari", "web"]


def apply_auth(opts: dict) -> dict:
    """Add cookies + a resilient player_client preference to a yt-dlp opts dict.

    Mutates and returns ``opts``. EVERY Python-API download path must funnel its
    opts through here so a single YouTube-side change is fixed in one place
    (previously each path hand-rolled cookies and none set player_client).
    """
    ck = cookiesfrombrowser_tuple()
    if ck is not None:
        opts["cookiesfrombrowser"] = ck
    yt = opts.setdefault("extractor_args", {}).setdefault("youtube", {})
    yt.setdefault("player_client", _PLAYER_CLIENTS)
    return opts
