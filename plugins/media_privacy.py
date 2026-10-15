import json
import os
from typing import Optional
from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "media_privacy"
REQUIRED_CAPABILITIES = frozenset({"device_control"})

KIND_REGISTRY_MAP = {
    "camera": "webcam",
    "microphone": "microphone",
}

STATUS_MAP = {
    "Allow": "granted",
    "Deny": "denied",
}

SETTINGS_URI_MAP = {
    "camera": "ms-settings:privacy-webcam",
    "microphone": "ms-settings:privacy-microphone",
}

class MediaPrivacyPlugin:
    name = PLUGIN_NAME

    def __init__(self, reg_read=None, open_uri=None):
        self._reg_read = reg_read if reg_read is not None else self._default_reg_read
        self._open_uri = open_uri if open_uri is not None else os.startfile

    def _default_reg_read(self, kind: str) -> Optional[str]:
        import winreg
        reg_key = KIND_REGISTRY_MAP.get(kind)
        if reg_key is None:
            return None
        key_path = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as hkey:
                with winreg.OpenKey(hkey, reg_key) as subkey:
                    value, _ = winreg.QueryValueEx(subkey, "Value")
                    return str(value)
        except FileNotFoundError:
            return None

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="media_access_status",
                description="Get the current access status for a camera or microphone.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}
            ),
            Tool(
                name="open_media_privacy_settings",
                description="Open the OS privacy settings page for a camera or microphone.",
                plugin=PLUGIN_NAME,
                schema={"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}
            )
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        kind = args.get("kind")
        if kind not in KIND_REGISTRY_MAP:
            return ToolResult(content=json.dumps({"error": f"Invalid kind: {kind}"}), is_error=True)

        if tool_name == "media_access_status":
            status_value = self._reg_read(kind)
            status = STATUS_MAP.get(status_value, "unknown")
            return ToolResult(content=json.dumps({"kind": kind, "status": status}))

        elif tool_name == "open_media_privacy_settings":
            uri = SETTINGS_URI_MAP[kind]
            self._open_uri(uri)
            return ToolResult(content=json.dumps({"opened": True, "uri": uri}))

        return ToolResult(content=json.dumps({"error": f"Unknown tool: {tool_name}"}), is_error=True)

    def panel_spec(self, profile_id=None) -> dict:
        camera_status = STATUS_MAP.get(self._reg_read("camera"), "unknown")
        mic_status = STATUS_MAP.get(self._reg_read("microphone"), "unknown")

        return {
            "title": "Camera & microphone",
            "widgets": [
                {
                    "type": "detail",
                    "fields": [
                        {"label": "Camera", "value": camera_status},
                        {"label": "Microphone", "value": mic_status}
                    ]
                },
                {
                    "type": "action",
                    "id": "media-open-camera",
                    "tool": "open_media_privacy_settings",
                    "tool_args": {"kind": "camera"}
                },
                {
                    "type": "action",
                    "id": "media-open-microphone",
                    "tool": "open_media_privacy_settings",
                    "tool_args": {"kind": "microphone"}
                }
            ]
        }

def create(**kwargs) -> MediaPrivacyPlugin:
    return MediaPrivacyPlugin(**kwargs)
