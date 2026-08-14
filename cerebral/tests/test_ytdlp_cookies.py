"""Tests for the shared yt-dlp cookie helper (ADR-0017).

Every yt-dlp path authenticates with Felix's google_web profile so YouTube
stops 403-ing anonymous requests. Best-effort: no profile -> empty/None.
"""
from __future__ import annotations

import cerebral.video.ytdlp_cookies as ck


def _make_profile(tmp_path, name="profile_4"):
    d = tmp_path / "browser" / name / "google_web" / "Default" / "Network"
    d.mkdir(parents=True)
    (d / "Cookies").write_text("")  # presence is all the helper checks
    return tmp_path / "browser" / name / "google_web"


def test_none_when_no_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(ck, "data_dir", lambda: tmp_path)
    assert ck.browser_profile_dir() is None
    assert ck.cookiesfrombrowser_tuple() is None
    assert ck.cookies_cli_args() == []


def test_finds_profile_and_formats_both(tmp_path, monkeypatch):
    prof = _make_profile(tmp_path)
    monkeypatch.setattr(ck, "data_dir", lambda: tmp_path)
    assert ck.browser_profile_dir() == str(prof)
    assert ck.cookiesfrombrowser_tuple() == ("chrome", str(prof), None, None)
    assert ck.cookies_cli_args() == ["--cookies-from-browser", f"chrome:{prof}"]


def test_profile_without_cookie_db_is_ignored(tmp_path, monkeypatch):
    # A google_web dir with no Network/Cookies must not be picked (would 403).
    (tmp_path / "browser" / "profile_9" / "google_web").mkdir(parents=True)
    monkeypatch.setattr(ck, "data_dir", lambda: tmp_path)
    assert ck.browser_profile_dir() is None


def test_apply_auth_sets_player_client_and_cookies(tmp_path, monkeypatch):
    prof = _make_profile(tmp_path)
    monkeypatch.setattr(ck, "data_dir", lambda: tmp_path)
    opts = {"format": "bestaudio/best"}
    ck.apply_auth(opts)
    # android must be first -- it's the client that serves a downloadable audio
    # format when the web client's media URL 403s.
    assert opts["extractor_args"]["youtube"]["player_client"][0] == "android"
    assert opts["cookiesfrombrowser"] == ("chrome", str(prof), None, None)


def test_apply_auth_player_client_without_profile(tmp_path, monkeypatch):
    # No cookies available, but player_client still set (the 403 fix is independent).
    monkeypatch.setattr(ck, "data_dir", lambda: tmp_path)
    opts = {}
    ck.apply_auth(opts)
    assert "android" in opts["extractor_args"]["youtube"]["player_client"]
    assert "cookiesfrombrowser" not in opts


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
