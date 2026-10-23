"""Tests for plugins/video_project.py -- V1 (ADR-0041): Shotcut + melt discovery and video_status.

Also the ADR-0034 gate file: the plugin is refused at boot unless this exact name exists.
"""
import json

import pytest

import plugins.video_project as vp
from plugins.video_project import PLUGIN_NAME, REQUIRED_CAPABILITIES, create, find_melt, find_shotcut


@pytest.fixture(autouse=True)
def _reset_finder():
    yield
    vp.set_find_shotcut_fn(None)


def test_create_registers_under_the_name_the_gate_checks():
    assert PLUGIN_NAME == "video_project"
    assert create().name == PLUGIN_NAME
    assert REQUIRED_CAPABILITIES == frozenset({"fs_read"})


def test_find_shotcut_and_melt_in_an_install_dir(tmp_path):
    (tmp_path / "shotcut.exe").write_bytes(b"")
    (tmp_path / "melt.exe").write_bytes(b"")
    shotcut = find_shotcut([tmp_path])
    assert shotcut == tmp_path / "shotcut.exe"
    assert find_melt(shotcut) == tmp_path / "melt.exe"


def test_find_shotcut_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(vp.shutil, "which", lambda _name: None)
    assert find_shotcut([tmp_path]) is None


async def test_video_status_available_when_both_found(tmp_path):
    (tmp_path / "shotcut.exe").write_bytes(b"")
    (tmp_path / "melt.exe").write_bytes(b"")
    vp.set_find_shotcut_fn(lambda: tmp_path / "shotcut.exe")
    tr = await create().call_tool("video_status", {})
    payload = json.loads(tr.content)
    assert not tr.is_error
    assert payload["available"] is True
    assert payload["melt"] == str(tmp_path / "melt.exe")
    assert "hint" not in payload


async def test_video_status_hints_install_when_missing():
    vp.set_find_shotcut_fn(lambda: None)
    payload = json.loads((await create().call_tool("video_status", {})).content)
    assert payload["available"] is False
    assert "setup-shotcut.ps1" in payload["hint"]


async def test_video_status_not_available_without_melt(tmp_path):
    (tmp_path / "shotcut.exe").write_bytes(b"")
    vp.set_find_shotcut_fn(lambda: tmp_path / "shotcut.exe")
    payload = json.loads((await create().call_tool("video_status", {})).content)
    assert payload["available"] is False
    assert payload["melt"] is None
