"""Tests for plugins/video_project.py -- V1 (ADR-0041): Shotcut + melt discovery and video_status.

Also the ADR-0034 gate file: the plugin is refused at boot unless this exact name exists.
"""
import asyncio
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
    assert REQUIRED_CAPABILITIES == frozenset({"fs_read", "fs_write"})


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


# ---------------------------------------------------------------------------
# V2: video_project_create
# ---------------------------------------------------------------------------

import os
from datetime import datetime
from fractions import Fraction

from cerebral.plugin_shared import state as _shared
from cerebral.video.mlt import ClipInfo
from plugins.documents import DocumentStore

_INFO = ClipInfo(width=1280, height=720, fps=Fraction(60), duration=30.0, has_audio=True)


@pytest.fixture
def library(tmp_path):
    store = DocumentStore(db_path=tmp_path / "docs.db", store_root=tmp_path / "store")
    saved = {k: _shared.get(k) for k in ("documents.store", "documents.profile_id", "documents.broadcast_fn")}
    _shared["documents.store"], _shared["documents.profile_id"] = store, 1
    _shared["documents.broadcast_fn"] = None
    async def _probe(_p):
        return _INFO
    vp.set_probe_fn(_probe)
    yield store
    vp.set_probe_fn(None)
    _shared.update(saved)


def _clip(tmp_path, name, day):
    p = tmp_path / name
    p.write_bytes(b"")
    ts = datetime(*day, 21, 0).timestamp()
    os.utime(p, (ts, ts))
    return str(p)


async def test_create_files_a_reel_by_capture_date(tmp_path, library):
    a = _clip(tmp_path, "a.mp4", (2026, 10, 23))
    b = _clip(tmp_path, "b.mp4", (2026, 10, 22))  # earliest clip sets the day
    tr = await create().call_tool("video_project_create", {
        "clips": [{"path": a, "start": 1, "end": 4}, {"path": b, "start": 0, "end": 2.5}]})
    out = json.loads(tr.content)
    assert not tr.is_error, tr.content
    assert (out["folder"], out["name"], out["clips"], out["duration_s"]) == (
        "Videos/2026/10-22", "Reel 2026-10-22", 2, 5.5)
    doc = library.get_doc(out["doc_id"])
    assert doc["kind"] == "mlt" and doc["path"].endswith(".mlt")
    assert open(doc["path"] + ".sha256").read() == out["fingerprint"]
    assert vp.fingerprint(open(doc["path"], encoding="utf-8").read()) == out["fingerprint"]


async def test_second_reel_same_day_gets_a_suffix(tmp_path, library):
    a = _clip(tmp_path, "a.mp4", (2026, 10, 23))
    args = {"clips": [{"path": a, "start": 0, "end": 3}]}
    first = json.loads((await create().call_tool("video_project_create", args)).content)
    second = json.loads((await create().call_tool("video_project_create", args)).content)
    assert (first["name"], second["name"]) == ("Reel 2026-10-23", "Reel 2026-10-23 (2)")
    assert first["path"] != second["path"]


async def test_create_rejects_non_video_and_missing_files(tmp_path, library):
    txt = tmp_path / "secrets.txt"
    txt.write_text("x")
    for path in (str(txt), str(tmp_path / "gone.mp4")):
        tr = await create().call_tool("video_project_create", {"clips": [{"path": path, "start": 0, "end": 1}]})
        assert tr.is_error
    assert library.list_docs(1) == []


async def test_create_reports_bad_ranges_without_saving(tmp_path, library):
    a = _clip(tmp_path, "a.mp4", (2026, 10, 23))
    tr = await create().call_tool("video_project_create", {"clips": [{"path": a, "start": 5, "end": 99}]})
    assert tr.is_error and "start < end" in tr.content
    assert library.list_docs(1) == []


def test_unique_name():
    assert vp.unique_name(set(), "Reel") == "Reel"
    assert vp.unique_name({"Reel", "Reel (2)"}, "Reel") == "Reel (3)"


# ---------------------------------------------------------------------------
# V3: video_render / video_render_status
# ---------------------------------------------------------------------------

import sys
from pathlib import Path


@pytest.fixture
def renders():
    """Fake melt: writes the output file (or fails), reporting progress. Clears job state."""
    vp._jobs.clear()
    calls = []

    async def _render(melt, mlt_path, out_path, on_progress):
        calls.append(mlt_path)
        on_progress(50)
        if "fail" in mlt_path.read_text(encoding="utf-8"):
            out_path.write_bytes(b"partial")
            raise ValueError("melt exited 1: boom")
        out_path.write_bytes(b"mp4 bytes")

    vp.set_render_fn(_render)
    yield calls
    vp.set_render_fn(None)
    vp._jobs.clear()


async def _make_project(tmp_path):
    a = _clip(tmp_path, "a.mp4", (2026, 10, 23))
    tr = await create().call_tool("video_project_create", {"clips": [{"path": a, "start": 0, "end": 3}]})
    return json.loads(tr.content)


async def _status(doc_id):
    return json.loads((await create().call_tool("video_render_status", {"doc_id": doc_id})).content)


async def test_render_files_the_mp4_beside_the_project(tmp_path, library, renders):
    proj = await _make_project(tmp_path)
    assert (await _status(proj["doc_id"]))["out_of_date"] is True  # never rendered
    tr = await create().call_tool("video_render", {"doc_id": proj["doc_id"]})
    assert json.loads(tr.content)["state"] == "rendering"
    await vp._jobs[proj["doc_id"]]["task"]
    st = await _status(proj["doc_id"])
    assert (st["state"], st["percent"], st["rendered"], st["out_of_date"]) == ("done", 100, True, False)
    assert st["render_path"].endswith("Reel 2026-10-23.mp4")
    mp4s = [d for d in library.list_docs(1) if d["kind"] == "mp4"]
    assert [(d["name"], d["folder"]) for d in mp4s] == [("Reel 2026-10-23", "Videos/2026/10-23")]
    assert list((tmp_path / "store").rglob("*.rendering.mp4")) == []


async def test_editing_the_timeline_marks_the_render_out_of_date(tmp_path, library, renders):
    proj = await _make_project(tmp_path)
    await create().call_tool("video_render", {"doc_id": proj["doc_id"]})
    await vp._jobs[proj["doc_id"]]["task"]
    mlt = Path(proj["path"])
    mlt.write_text(mlt.read_text(encoding="utf-8") + "<!-- edited in Shotcut -->\n", encoding="utf-8")
    assert (await _status(proj["doc_id"]))["out_of_date"] is True
    await create().call_tool("video_render", {"doc_id": proj["doc_id"]})  # render on request
    await vp._jobs[proj["doc_id"]]["task"]
    assert (await _status(proj["doc_id"]))["out_of_date"] is False
    assert len([d for d in library.list_docs(1) if d["kind"] == "mp4"]) == 1  # re-render replaces, not duplicates


async def test_failed_render_leaves_no_file_and_reports_why(tmp_path, library, renders):
    proj = await _make_project(tmp_path)
    mlt = Path(proj["path"])
    mlt.write_text(mlt.read_text(encoding="utf-8") + "<!-- fail -->\n", encoding="utf-8")
    await create().call_tool("video_render", {"doc_id": proj["doc_id"]})
    await vp._jobs[proj["doc_id"]]["task"]
    st = await _status(proj["doc_id"])
    assert (st["state"], st["rendered"]) == ("failed", False)
    assert "boom" in st["error"]
    assert not mlt.with_suffix(".mp4").exists()
    assert [d for d in library.list_docs(1) if d["kind"] == "mp4"] == []


async def test_one_render_at_a_time(tmp_path, library):
    vp._jobs.clear()
    gate = asyncio.Event()

    async def _slow(melt, mlt_path, out_path, on_progress):
        await gate.wait()
        out_path.write_bytes(b"x")

    vp.set_render_fn(_slow)
    try:
        proj = await _make_project(tmp_path)
        first = await create().call_tool("video_render", {"doc_id": proj["doc_id"]})
        second = await create().call_tool("video_render", {"doc_id": proj["doc_id"]})
        assert not first.is_error and second.is_error and "one at a time" in second.content
        gate.set()
        await vp._jobs[proj["doc_id"]]["task"]
    finally:
        vp.set_render_fn(None)
        vp._jobs.clear()


async def test_render_rejects_non_projects(tmp_path, library, renders):
    note = library.save_text(1, "", "note", "hello")
    for doc_id in (note["id"], 9999, "x"):
        tr = await create().call_tool("video_render", {"doc_id": doc_id})
        assert tr.is_error
    assert renders == []


async def test_render_without_shotcut_gives_the_install_hint(tmp_path, library):
    vp._jobs.clear()
    vp.set_find_shotcut_fn(lambda: None)
    proj = await _make_project(tmp_path)
    tr = await create().call_tool("video_render", {"doc_id": proj["doc_id"]})
    assert tr.is_error and "setup-shotcut.ps1" in tr.content


def test_last_percent_reads_the_latest_progress_line():
    assert vp.last_percent(b"Current Frame:  8, percentage:  2\rCurrent Frame: 14, percentage:  4\r") == 4
    assert vp.last_percent(b"no progress yet") is None


async def test_run_melt_reports_progress_and_failure(tmp_path):
    """Real subprocess plumbing: a Python script stands in for melt.exe (the script sits where the .mlt would)."""
    fake = tmp_path / "fake_melt.py"
    fake.write_text(
        "import sys\n"
        "out = [a for a in sys.argv if a.startswith('avformat:')][0][len('avformat:'):]\n"
        "for p in (10, 60, 99):\n"
        "    sys.stderr.write(f'Current Frame: {p}, percentage: {p}\\r'); sys.stderr.flush()\n"
        "if 'bad' in sys.argv[0]:\n"
        "    sys.stderr.write('Failed to load bad.mlt\\n'); sys.exit(1)\n"
        "open(out, 'wb').write(b'ok')\n",
        encoding="utf-8",
    )
    seen = []
    out = tmp_path / "out.mp4"
    # sys.executable plays melt; the script path goes where the .mlt would.
    await vp.run_melt(Path(sys.executable), fake, out, seen.append)
    assert out.read_bytes() == b"ok" and seen[-1] == 99
    bad = tmp_path / "bad.py"
    bad.write_text(fake.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to load bad.mlt"):
        await vp.run_melt(Path(sys.executable), bad, tmp_path / "x.mp4", seen.append)
