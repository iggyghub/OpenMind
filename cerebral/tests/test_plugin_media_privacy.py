import asyncio
import json
import pytest
from plugins.media_privacy import MediaPrivacyPlugin, create, PLUGIN_NAME, REQUIRED_CAPABILITIES

def test_status_maps_registry_values():
    plugin = create(reg_read=lambda k: "Allow" if k == "camera" else "Deny")
    result = asyncio.run(plugin.call_tool("media_access_status", {"kind": "camera"}))
    data = json.loads(result.content)
    assert data == {"kind": "camera", "status": "granted"}
    result = asyncio.run(plugin.call_tool("media_access_status", {"kind": "microphone"}))
    data = json.loads(result.content)
    assert data == {"kind": "microphone", "status": "denied"}

def test_status_unknown_when_missing():
    plugin = create(reg_read=lambda k: None)
    result = asyncio.run(plugin.call_tool("media_access_status", {"kind": "camera"}))
    data = json.loads(result.content)
    assert data == {"kind": "camera", "status": "unknown"}

def test_open_settings_uses_ms_settings_uri():
    called_with = []
    def mock_open_uri(uri):
        called_with.append(uri)
    plugin = create(open_uri=mock_open_uri)
    result = asyncio.run(plugin.call_tool("open_media_privacy_settings", {"kind": "microphone"}))
    data = json.loads(result.content)
    assert data == {"opened": True, "uri": "ms-settings:privacy-microphone"}
    assert called_with == ["ms-settings:privacy-microphone"]

def test_rejects_unknown_kind():
    plugin = create()
    result = asyncio.run(plugin.call_tool("media_access_status", {"kind": "laser"}))
    assert result.is_error
    data = json.loads(result.content)
    assert "error" in data
    result = asyncio.run(plugin.call_tool("open_media_privacy_settings", {"kind": "laser"}))
    assert result.is_error

def test_panel_shows_both_and_open_actions():
    plugin = create(reg_read=lambda k: "Allow" if k == "camera" else "Deny")
    spec = plugin.panel_spec()
    assert spec["title"] == "Camera & microphone"
    detail_widgets = [w for w in spec["widgets"] if w["type"] == "detail"]
    assert len(detail_widgets) == 1
    fields = detail_widgets[0]["fields"]
    assert {"label": "Camera", "value": "granted"} in fields
    assert {"label": "Microphone", "value": "denied"} in fields
    action_ids = [w["id"] for w in spec["widgets"] if w["type"] == "action"]
    assert "media-open-camera" in action_ids
    assert "media-open-microphone" in action_ids
    camera_action = next(w for w in spec["widgets"] if w["id"] == "media-open-camera")
    assert camera_action["tool"] == "open_media_privacy_settings"
    assert camera_action["tool_args"] == {"kind": "camera"}
