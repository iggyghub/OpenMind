"""Tests for VideoPlugin.panel_spec() -- S4 #643 (ADR-0017 decision 9).

Verifies the declarative panel spec returned by the video plugin:
- shape: {title, widgets} with expected types
- table widget present when clusters exist
- action widgets for ingest + batch-start forms
- detail widget carries batch status fields
- no plugin-authored HTML (all content is plain data strings)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import plugins.video as _video_plugin
from plugins.video import VideoPlugin
from cerebral.video.store import VideoStore
from cerebral.video import channel as _channel


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "test.db"
    return VideoStore(db_path=db)


@pytest.fixture()
def plugin(store, monkeypatch):
    monkeypatch.setattr(_video_plugin, "_store", store)
    return VideoPlugin()


# Patch channel.batch_status to avoid touching module-level _state channel name.
def _idle_status(_store):
    return {"running": False, "channel": None, "stage_counts": {}, "eta_seconds": None}


def test_panel_spec_returns_dict_with_title_and_widgets(plugin, store, monkeypatch):
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    spec = plugin.panel_spec(None)
    assert isinstance(spec, dict)
    assert spec.get("title") == "Videos"
    assert isinstance(spec.get("widgets"), list)


def test_panel_spec_has_ingest_and_batch_action_forms(plugin, store, monkeypatch):
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    spec = plugin.panel_spec(None)
    widgets = spec["widgets"]
    action_ids = [w.get("id") for w in widgets if w.get("type") == "action"]
    assert "video-ingest" in action_ids
    assert "video-batch-start" in action_ids


def test_panel_spec_has_status_detail(plugin, store, monkeypatch):
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    spec = plugin.panel_spec(None)
    detail_widgets = [w for w in spec["widgets"] if w.get("type") == "detail"]
    assert detail_widgets, "expected at least one detail widget for batch status"
    labels = [f["label"] for f in detail_widgets[0].get("fields", [])]
    assert "Status" in labels


def test_panel_spec_no_clusters_yields_empty_list(plugin, store, monkeypatch):
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    spec = plugin.panel_spec(None)
    list_widgets = [w for w in spec["widgets"] if w.get("type") == "list"]
    assert any(w.get("items") == [] for w in list_widgets), \
        "expected an empty list widget when no clusters"


def test_panel_spec_cluster_table_when_clusters_exist(plugin, store, monkeypatch):
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    monkeypatch.setattr(_video_plugin, "_store", store)

    # Seed a cluster + idea.
    video_id = store.upsert("https://example.com/v1", channel="test", stage="extracted")
    cluster_id = store.get_or_create_cluster("dropshipping")
    store.upsert_idea(video_id, "Sell goods without holding inventory", cluster_id)

    spec = plugin.panel_spec(None)
    table_widgets = [w for w in spec["widgets"] if w.get("type") == "table"]
    assert table_widgets, "expected a table widget listing clusters"
    tbl = table_widgets[0]
    assert "Idea cluster" in tbl["columns"]
    labels = [row[0] for row in tbl["rows"]]
    assert "dropshipping" in labels


def test_panel_spec_running_batch_includes_stop_action(plugin, store, monkeypatch):
    def _running_status(_store):
        return {"running": True, "channel": "test-channel", "stage_counts": {}, "eta_seconds": 120}
    monkeypatch.setattr(_channel, "batch_status", _running_status)

    spec = plugin.panel_spec(None)
    action_ids = [w.get("id") for w in spec["widgets"] if w.get("type") == "action"]
    assert "video-batch-stop" in action_ids


def test_panel_spec_running_batch_shows_eta(plugin, store, monkeypatch):
    def _running_status(_store):
        return {"running": True, "channel": "ch", "stage_counts": {}, "eta_seconds": 75}
    monkeypatch.setattr(_channel, "batch_status", _running_status)

    spec = plugin.panel_spec(None)
    detail_widgets = [w for w in spec["widgets"] if w.get("type") == "detail"]
    all_fields = [f for w in detail_widgets for f in w.get("fields", [])]
    eta_field = next((f for f in all_fields if f["label"] == "ETA"), None)
    assert eta_field is not None
    assert "1m" in eta_field["value"]   # 75s -> 1m 15s


def test_panel_spec_no_per_cluster_detail_wall(plugin, store, monkeypatch):
    """S11 #659: many clusters must not emit a detail+list block per cluster."""
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    monkeypatch.setattr(_video_plugin, "_store", store)

    for i in range(4):
        vid = store.upsert(f"https://example.com/v{i}", channel="test", stage="verified")
        cid = store.get_or_create_cluster(f"idea-{i}")
        store.upsert_idea(vid, f"Idea number {i}", cid)
        store.set_cluster_verdict(cid, "legit", 0.9, ["http://e"])

    spec = plugin.panel_spec(None)
    widgets = spec["widgets"]

    # Each cluster appears exactly once — in the single clusters table.
    tables = [w for w in widgets if w.get("type") == "table" and "Idea cluster" in w.get("columns", [])]
    assert len(tables) == 1
    assert len(tables[0]["rows"]) == 4

    # No per-cluster detail wall: the only detail is the batch status (a "Status" field).
    details = [w for w in widgets if w.get("type") == "detail"]
    assert len(details) == 1, f"expected only the status detail, got {len(details)}"
    assert not any(
        f.get("label") == "Cluster" for w in details for f in w.get("fields", [])
    ), "per-cluster detail widgets should be gone"

    # One commit button per verified-and-uncommitted cluster (all 4 here).
    commit_actions = [w for w in widgets if str(w.get("id", "")).startswith("video-commit-")]
    assert len(commit_actions) == 4


def test_panel_spec_committed_cluster_shows_in_memory_and_no_commit(plugin, store, monkeypatch):
    """S11 #659: committed clusters read '✓' in the table and drop their commit button."""
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    monkeypatch.setattr(_video_plugin, "_store", store)

    vid = store.upsert("https://example.com/c", channel="test", stage="verified")
    cid = store.get_or_create_cluster("committed-idea")
    store.upsert_idea(vid, "A committed idea", cid)
    store.set_cluster_verdict(cid, "legit", 0.9, ["http://e"])
    store.set_cluster_committed(cid, "mem-123")

    spec = plugin.panel_spec(None)
    widgets = spec["widgets"]
    tbl = next(w for w in widgets if w.get("type") == "table" and "In Memory" in w.get("columns", []))
    in_mem_idx = tbl["columns"].index("In Memory")
    assert tbl["rows"][0][in_mem_idx] == "✓"
    assert not [w for w in widgets if str(w.get("id", "")).startswith("video-commit-")]


def test_panel_spec_no_html_in_widget_values(plugin, store, monkeypatch):
    """All field values must be plain strings, not HTML markup."""
    monkeypatch.setattr(_channel, "batch_status", _idle_status)
    spec = plugin.panel_spec(None)
    for w in spec["widgets"]:
        for field in w.get("fields", []):
            val = field.get("value", "")
            assert "<" not in val and ">" not in val, \
                f"HTML found in field value: {val!r}"
