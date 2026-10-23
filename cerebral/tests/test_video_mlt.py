"""Tests for cerebral/video/mlt.py -- edit list -> Shotcut/MLT XML (ADR-0041, VIDEO-EDIT V2)."""
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from cerebral.video.mlt import ClipInfo, Cut, build_mlt, validate

INFO = ClipInfo(width=1920, height=1080, fps=Fraction(60), duration=30.0, has_audio=True)


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.split("\n", 1)[1])


def test_two_cuts_make_a_shotcut_timeline():
    cuts = [Cut("C:/clips/a.mp4", 1.0, 3.5), Cut("C:/clips/b.mp4", 20.0, 30.0)]
    root = _parse(build_mlt(cuts, {c.path: INFO for c in cuts}))
    profile = root.find("profile")
    assert (profile.get("width"), profile.get("height"), profile.get("frame_rate_num")) == ("1920", "1080", "60")
    entries = root.findall("playlist[@id='playlist0']/entry")
    assert [(e.get("in"), e.get("out")) for e in entries] == [("60", "209"), ("1200", "1799")]
    tractor = root.find("tractor")
    assert tractor.find("property[@name='shotcut']").text == "1"
    assert tractor.get("out") == str(150 + 600 - 1)  # total frames - 1
    assert [t.get("producer") for t in tractor.findall("track")] == ["background", "playlist0"]


def test_each_cut_gets_a_fade_in_and_out():
    cut = Cut("C:/clips/a.mp4", 0.0, 4.0)
    root = _parse(build_mlt([cut], {cut.path: INFO}))
    kinds = [f.find("property[@name='shotcut:filter']").text for f in root.findall("chain/filter")]
    assert kinds == ["fadeInVolume", "fadeOutVolume"]


def test_no_fades_without_audio_or_on_a_tiny_cut():
    silent = ClipInfo(1280, 720, Fraction(30), 10.0, has_audio=False)
    cut = Cut("C:/clips/a.mp4", 0.0, 5.0)
    assert _parse(build_mlt([cut], {cut.path: silent})).findall("chain/filter") == []
    tiny = Cut("C:/clips/b.mp4", 0.0, 0.3)  # shorter than two fades
    assert _parse(build_mlt([tiny], {tiny.path: INFO})).findall("chain/filter") == []


def test_hostile_filename_is_escaped_not_injected():
    path = 'C:/clips/x"><filter mlt_service="evil"/><a b=".mp4'
    cut = Cut(path, 0.0, 2.0)
    root = _parse(build_mlt([cut], {path: INFO}))
    assert root.find("chain/property[@name='resource']").text == path
    assert all(f.find("property[@name='mlt_service']").text == "volume" for f in root.iter("filter"))


@pytest.mark.parametrize("start,end", [(-1.0, 2.0), (3.0, 3.0), (5.0, 2.0), (0.0, 31.0)])
def test_validate_rejects_bad_ranges(start, end):
    cut = Cut("C:/clips/a.mp4", start, end)
    with pytest.raises(ValueError):
        validate([cut], {cut.path: INFO})


def test_validate_rejects_empty_and_unprobed():
    with pytest.raises(ValueError):
        validate([], {})
    with pytest.raises(ValueError):
        validate([Cut("C:/clips/a.mp4", 0, 1)], {})


def test_ntsc_frame_rate_is_kept_exact():
    info = ClipInfo(1920, 1080, Fraction(30000, 1001), 10.0, True)
    cut = Cut("C:/clips/a.mp4", 0.0, 2.0)
    profile = _parse(build_mlt([cut], {cut.path: info})).find("profile")
    assert (profile.get("frame_rate_num"), profile.get("frame_rate_den")) == ("30000", "1001")
