"""Edit list -> Shotcut/MLT project XML (ADR-0041 decision 4, VIDEO-EDIT V2).

The model never writes .mlt XML. It produces an edit list (clip path, in, out,
in order); this module validates it and serialises the Shotcut-format project
melt renders and Shotcut opens. ElementTree does all escaping, so a hostile
filename or caption can't inject XML. Pure: no subprocess, no filesystem writes.

Layout follows what Shotcut itself saves: a black background track plus one
video track (V1), each timeline clip its own <chain> carrying its fades, and a
tractor marked shotcut=1 so Shotcut opens it as an editable timeline.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

FADE_SECONDS = 0.25  # audio fade at each cut (ADR-0041 decision 9)
MAX_CLIPS = 200


@dataclass(frozen=True)
class ClipInfo:
    """What probing a source file tells us (filled by the plugin via ffprobe)."""
    width: int
    height: int
    fps: Fraction  # e.g. Fraction(60, 1), Fraction(30000, 1001)
    duration: float  # seconds
    has_audio: bool


@dataclass(frozen=True)
class Cut:
    """One edit-list entry: a stretch of one source clip, seconds from its start."""
    path: str
    start: float
    end: float


def validate(cuts: list[Cut], infos: dict[str, ClipInfo]) -> None:
    """Raise ValueError on anything melt or Shotcut would choke on."""
    if not cuts:
        raise ValueError("edit list is empty")
    if len(cuts) > MAX_CLIPS:
        raise ValueError(f"edit list has {len(cuts)} clips (max {MAX_CLIPS})")
    for i, c in enumerate(cuts):
        info = infos.get(c.path)
        if info is None:
            raise ValueError(f"clip {i}: not probed: {c.path}")
        if not (0 <= c.start < c.end <= info.duration + 1e-6):
            raise ValueError(
                f"clip {i}: need 0 <= start < end <= {info.duration:.3f}s, "
                f"got {c.start}..{c.end} ({Path(c.path).name})"
            )


def _frames(seconds: float, fps: Fraction) -> int:
    return int(round(Fraction(seconds).limit_denominator(1000) * fps))


def _prop(parent: ET.Element, name: str, value) -> None:
    ET.SubElement(parent, "property", name=name).text = str(value)


def _fade(chain: ET.Element, fid: str, kind: str, cin: int, cout: int, n: int) -> None:
    """A Shotcut fadeInVolume/fadeOutVolume filter spanning the cut [cin, cout]."""
    f = ET.SubElement(chain, "filter", id=fid, **{"in": str(cin), "out": str(cout)})
    _prop(f, "window", 75)
    _prop(f, "max_gain", "20dB")
    length = cout - cin
    level = f"0=-60;{n}=0" if kind == "in" else f"{length - n}=0;{length}=-60"
    _prop(f, "level", level)
    _prop(f, "mlt_service", "volume")
    _prop(f, "shotcut:filter", "fadeInVolume" if kind == "in" else "fadeOutVolume")
    _prop(f, "shotcut:animIn" if kind == "in" else "shotcut:animOut", n)


def build_mlt(cuts: list[Cut], infos: dict[str, ClipInfo], shotcut_version: str = "") -> str:
    """Serialise a validated edit list. The first clip sets the profile;
    others are conformed to it by melt at render time."""
    validate(cuts, infos)
    first = infos[cuts[0].path]
    fps = first.fps
    title = f"Shotcut version {shotcut_version}".strip()

    mlt = ET.Element("mlt", LC_NUMERIC="C", title=title, producer="main_bin")
    ET.SubElement(mlt, "profile", {
        "description": f"{first.width}x{first.height} {float(fps):g} fps",
        "width": str(first.width), "height": str(first.height), "progressive": "1",
        "sample_aspect_num": "1", "sample_aspect_den": "1",
        "display_aspect_num": str(first.width), "display_aspect_den": str(first.height),
        "frame_rate_num": str(fps.numerator), "frame_rate_den": str(fps.denominator),
        "colorspace": "709",
    })
    _prop(ET.SubElement(mlt, "playlist", id="main_bin"), "xml_retain", 1)

    spans = []
    for c in cuts:
        cin, cout = _frames(c.start, fps), _frames(c.end, fps) - 1
        spans.append((cin, max(cin, cout)))
    total = sum(cout - cin + 1 for cin, cout in spans)

    black = ET.SubElement(mlt, "producer", id="black", **{"in": "0", "out": str(total - 1)})
    for k, v in (("length", total), ("eof", "pause"), ("resource", 0), ("aspect_ratio", 1),
                 ("mlt_service", "color"), ("mlt_image_format", "rgba"), ("set.test_audio", 0)):
        _prop(black, k, v)
    bg = ET.SubElement(mlt, "playlist", id="background")
    ET.SubElement(bg, "entry", producer="black", **{"in": "0", "out": str(total - 1)})

    fade_n = max(1, _frames(FADE_SECONDS, fps))
    track = ET.Element("playlist", id="playlist0")
    _prop(track, "shotcut:video", 1)
    _prop(track, "shotcut:name", "V1")
    for i, (c, (cin, cout)) in enumerate(zip(cuts, spans)):
        info = infos[c.path]
        length = _frames(info.duration, fps)
        chain = ET.SubElement(mlt, "chain", id=f"chain{i}", out=str(length - 1))
        for k, v in (("length", length), ("eof", "pause"),
                     ("resource", Path(c.path).as_posix()),
                     ("mlt_service", "avformat-novalidate"), ("seekable", 1),
                     ("shotcut:caption", Path(c.path).name)):
            _prop(chain, k, v)
        if info.has_audio and cout - cin + 1 > 2 * fade_n:
            _fade(chain, f"filter{2 * i}", "in", cin, cout, fade_n)
            _fade(chain, f"filter{2 * i + 1}", "out", cin, cout, fade_n)
        ET.SubElement(track, "entry", producer=f"chain{i}", **{"in": str(cin), "out": str(cout)})
    mlt.append(track)

    tractor = ET.SubElement(mlt, "tractor", id="tractor0", title=title,
                            **{"in": "0", "out": str(total - 1)})
    _prop(tractor, "shotcut", 1)
    _prop(tractor, "shotcut:projectAudioChannels", 2)
    ET.SubElement(tractor, "track", producer="background")
    ET.SubElement(tractor, "track", producer="playlist0")
    mix = ET.SubElement(tractor, "transition", id="transition0")
    for k, v in (("a_track", 0), ("b_track", 1), ("mlt_service", "mix"),
                 ("always_active", 1), ("sum", 1)):
        _prop(mix, k, v)
    blend = ET.SubElement(tractor, "transition", id="transition1")
    for k, v in (("a_track", 0), ("b_track", 1), ("version", "0.1"),
                 ("mlt_service", "frei0r.cairoblend"), ("threads", 0), ("disable", 1)):
        _prop(blend, k, v)

    ET.indent(mlt)
    return '<?xml version="1.0" standalone="no"?>\n' + ET.tostring(mlt, encoding="unicode") + "\n"
