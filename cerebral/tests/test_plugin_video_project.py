"""Tests for plugins/video_project.py -- ADR-0041, VIDEO-EDIT V1-V4.

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


# ---------------------------------------------------------------------------
# V4: Quick trim
# ---------------------------------------------------------------------------

def _levels(loud_at=None, n=60, quiet=-40.0, loud=-10.0):
    """n half-second samples at `quiet` dB, `loud` dB for the sample indexes in loud_at."""
    return [(i * 0.5, loud if loud_at and i in loud_at else quiet) for i in range(n)]


def test_parse_levels_reads_ffmpeg_metadata_and_clamps_silence():
    text = ("frame:0    pts:0       pts_time:0\nlavfi.astats.Overall.RMS_level=-51.5\n"
            "frame:1    pts:4000    pts_time:0.5\nlavfi.astats.Overall.RMS_level=-inf\n")
    assert vp.parse_levels(text) == [(0.0, -51.5), (0.5, -120.0)]


def test_loud_stretch_keeps_the_peak_plus_padding():
    assert vp.loud_stretch(_levels(loud_at={28, 29, 30, 31, 32, 33}), 30.0) == (11.0, 20.0)


def test_loud_stretch_clamps_to_the_clip():
    assert vp.loud_stretch(_levels(loud_at={0, 1}), 30.0) == (0.0, 4.0)
    assert vp.loud_stretch(_levels(loud_at={59}), 30.0) == (26.5, 30.0)


def test_loud_stretch_none_without_a_clear_peak():
    assert vp.loud_stretch(_levels(), 30.0) is None  # flat
    assert vp.loud_stretch(_levels(loud_at={10}, loud=-37.0), 30.0) is None  # only 3 dB up
    assert vp.loud_stretch([(i * 0.5, -120.0) for i in range(20)], 10.0) is None  # silence
    assert vp.loud_stretch([], 10.0) is None


@pytest.fixture
def loudness():
    """Fake level reader keyed on the clip's file name."""
    table = {}

    async def _loud(p):
        return table[Path(p).name]

    vp.set_loudness_fn(_loud)
    yield table
    vp.set_loudness_fn(None)


def _at(tmp_path, name, hour):
    p = tmp_path / name
    p.write_bytes(b"")
    ts = datetime(2026, 10, 23, hour, 0).timestamp()
    os.utime(p, (ts, ts))
    return str(p)


async def test_quick_trim_joins_clips_in_capture_order(tmp_path, library, loudness):
    late = _at(tmp_path, "late.mp4", 22)
    early = _at(tmp_path, "early.mp4", 20)
    loudness["late.mp4"] = _levels(loud_at={28, 29, 30, 31, 32, 33})
    loudness["early.mp4"] = _levels(loud_at={10})
    tr = await create().call_tool("video_quick_trim", {"paths": [late, early]})
    out = json.loads(tr.content)
    assert not tr.is_error, tr.content
    assert [(Path(t["path"]).name, t["start"], t["end"], t["flag"]) for t in out["trims"]] == [
        ("early.mp4", 2.0, 8.5, None), ("late.mp4", 11.0, 20.0, None)]
    assert out["flagged"] == [] and out["name"] == "Reel 2026-10-23" and out["duration_s"] == 15.5
    xml = open(out["path"], encoding="utf-8").read()
    assert xml.index("early.mp4") < xml.index("late.mp4")


async def test_quick_trim_keeps_and_flags_clips_without_a_clear_peak(tmp_path, library, loudness):
    flat = _at(tmp_path, "flat.mp4", 20)
    loudness["flat.mp4"] = _levels()
    out = json.loads((await create().call_tool("video_quick_trim", {"paths": [flat], "name": "Mine"})).content)
    assert out["trims"][0]["flag"] == "no_clear_peak"
    assert (out["trims"][0]["start"], out["trims"][0]["end"]) == (0.0, 30.0)
    assert out["flagged"] == [str(Path(flat).resolve())] and out["name"] == "Mine"


async def test_quick_trim_flags_silent_clips_without_measuring(tmp_path, library, loudness):
    async def _mute(_p):
        return ClipInfo(width=1280, height=720, fps=Fraction(60), duration=10.0, has_audio=False)

    vp.set_probe_fn(_mute)
    mute = _at(tmp_path, "mute.mp4", 20)  # no loudness entry: measuring it would KeyError
    out = json.loads((await create().call_tool("video_quick_trim", {"paths": [mute]})).content)
    assert out["trims"][0]["flag"] == "no_audio" and out["trims"][0]["end"] == 10.0


async def test_quick_trim_rejects_bad_input_without_saving(tmp_path, library, loudness):
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    for args in ({}, {"paths": []}, {"paths": "a.mp4"}, {"paths": [str(txt)]}, {"paths": [str(tmp_path / "gone.mp4")]}):
        assert (await create().call_tool("video_quick_trim", args)).is_error
    assert library.list_docs(1) == []


async def test_quick_trim_without_shotcut_gives_the_install_hint(tmp_path, library):
    vp.set_find_shotcut_fn(lambda: None)
    clip = _at(tmp_path, "a.mp4", 20)
    tr = await create().call_tool("video_quick_trim", {"paths": [clip]})
    assert tr.is_error and "setup-shotcut.ps1" in tr.content
