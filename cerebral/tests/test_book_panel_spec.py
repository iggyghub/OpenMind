"""Books panel_spec tests. The panel shares the cluster/group/move widgets
with Videos/GitHub and shows source-scoped (book) clusters + ingested books."""
from __future__ import annotations

from pathlib import Path

import plugins.book_ingest as book_ingest_mod
import plugins.video as video_mod
from cerebral.video.store import VideoStore
from plugins.book_ingest import BookIngestPlugin


def _wire() -> VideoStore:
    store = VideoStore(db_path=Path(":memory:"))
    video_mod.set_store(store)
    return store


def _all_widgets(spec):
    for w in spec["widgets"]:
        yield w
        if w.get("type") == "group":
            yield from w.get("widgets", [])


class _FakeMeta:
    """Stand-in for BookMetaStore -- panel_spec only needs list_for_profile."""

    def __init__(self, rows):
        self._rows = rows

    def list_for_profile(self, profile_id):
        return self._rows


def test_panel_spec_has_ingest_form_and_title():
    _wire()
    spec = BookIngestPlugin().panel_spec(None)
    assert spec["title"] == "Books"
    form = next(w for w in spec["widgets"] if w.get("id") == "book-ingest")
    assert form["tool"] == "book_ingest"
    assert form["input_arg"] == "path"
    assert form["input_arg2"] == "category"


def test_panel_spec_lists_books_with_progress_subtitle(monkeypatch):
    _wire()
    rows = [{"id": "/b.pdf", "title": "Deep Work", "author": "Cal Newport",
             "chapter_count": 10, "clustered_count": 4}]
    monkeypatch.setattr(book_ingest_mod._bm, "BookMetaStore", lambda: _FakeMeta(rows))
    spec = BookIngestPlugin().panel_spec(1)
    books_group = next(w for w in spec["widgets"] if w.get("label") == "Books")
    items = books_group["widgets"][0]["items"]
    assert items[0]["title"] == "Deep Work"
    assert "Cal Newport" in items[0]["subtitle"]
    assert "4 / 10 chapters clustered" in items[0]["subtitle"]


def test_panel_spec_no_profile_skips_book_list():
    _wire()
    spec = BookIngestPlugin().panel_spec(None)
    assert not any(w.get("label") == "Books" for w in spec["widgets"])


def test_panel_spec_shows_only_book_clusters():
    store = _wire()
    # A book-sourced cluster and a video-sourced one in the same collection.
    b = store.upsert("/b.pdf#ch0-intro", collection="deep work",
                      stage="verified", source_type="book")
    bc = store.get_or_create_cluster("Chapter Idea", "deep work")
    store.upsert_idea(b, "book idea", bc)
    v = store.upsert("https://yt/v", collection="deep work",
                      stage="verified", source_type="video")
    vc = store.get_or_create_cluster("Video Idea", "deep work")
    store.upsert_idea(v, "vid idea", vc)

    spec = BookIngestPlugin().panel_spec(None)
    clusters = [w for w in _all_widgets(spec) if w.get("type") == "cluster"]
    labels = {c["label"] for c in clusters}
    assert labels == {"Chapter Idea"}                 # video-only cluster not shown
    bc_widget = next(c for c in clusters if c["label"] == "Chapter Idea")
    assert bc_widget["move_tool"] == "video_move_cluster"  # reuses the shared move tool
    assert "deep work" in bc_widget["collections"]
    assert "chapter" in bc_widget["stats"]             # book stat wording (chapters)


def test_panel_spec_empty_when_no_book_clusters():
    _wire()
    spec = BookIngestPlugin().panel_spec(None)
    assert not [w for w in _all_widgets(spec) if w.get("type") == "cluster"]
