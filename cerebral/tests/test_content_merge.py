"""Content-aware clustering tests -- ADR-0018 S4. The extractor is handed each
existing cluster's label AND a representative idea, so it merges on meaning."""
from __future__ import annotations

from pathlib import Path

from cerebral.video import channel as _channel
from cerebral.video import extraction as _extraction
from cerebral.video.store import VideoStore


def _store() -> VideoStore:
    return VideoStore(db_path=Path(":memory:"))


def test_get_cluster_summaries_carries_representative_idea():
    s = _store()
    v = s.upsert("u1", collection="c", stage="verified")
    cid = s.get_or_create_cluster("Label A", "c")
    s.upsert_idea(v, "a longer representative idea text", cid)
    # An empty cluster (no idea) yields an empty sample.
    s.get_or_create_cluster("Empty", "c")
    summ = s.get_cluster_summaries("c")
    assert {"label": "Label A", "sample_idea": "a longer representative idea text"} in summ
    assert {"label": "Empty", "sample_idea": ""} in summ


async def test_extract_and_cluster_feeds_summaries_not_labels():
    s = _store()
    # Seed one existing cluster + idea in the collection.
    v0 = s.upsert("u0", collection="c", stage="verified")
    c0 = s.get_or_create_cluster("Existing", "c")
    s.upsert_idea(v0, "the existing sample idea", c0)

    seen = {}

    async def fake_extract(transcript, ocr, vis, clusters, category=""):
        seen["clusters"] = clusters
        return {"idea": "new idea", "cluster_label": "New", "people_required": 1}

    _extraction.set_extract_fn(fake_extract)
    try:
        v1 = s.upsert("u1", collection="c", stage="transcribed")
        await _channel.extract_and_cluster(
            s, video_id=v1, url="u1", transcript="x", collection="c", verify=False
        )
        # The extractor saw the existing cluster as {label, sample_idea}, not a bare label.
        assert seen["clusters"] == [
            {"label": "Existing", "sample_idea": "the existing sample idea"}
        ]
    finally:
        _extraction.set_extract_fn(None)
