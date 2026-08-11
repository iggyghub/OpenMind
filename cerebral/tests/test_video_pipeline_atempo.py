"""atempo_filter -- S14 #667 (audio speed-up for faster transcription)."""
from __future__ import annotations

from cerebral.video.pipeline import atempo_filter


def test_atempo_single_stage_up_to_2x():
    assert atempo_filter(1.5) == "atempo=1.5"
    assert atempo_filter(2.0) == "atempo=2"


def test_atempo_chains_above_2x():
    # 2.0 * 1.5 == 3.0
    assert atempo_filter(3.0) == "atempo=2,atempo=1.5"


def test_atempo_clamps_out_of_range():
    assert atempo_filter(0.1) == "atempo=0.5"   # floor
    assert atempo_filter(9.0) == "atempo=2,atempo=1.5"  # clamped to 3.0
