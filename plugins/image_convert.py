import os
import json
from typing import Dict, Any
from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "image_convert"
REQUIRED_CAPABILITIES = frozenset({"fs_read", "fs_write"})

def create(**kwargs):
    return ImageConvertPlugin()

class ImageConvertPlugin:
    name = PLUGIN_NAME

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="convert_image",
                description="Convert an image file to a different format.",
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the source image"},
                        "to": {"type": "string", "enum": ["jpeg", "png", "webp"], "default": "jpeg"},
                        "quality": {"type": "integer", "minimum": 1, "maximum": 100, "default": 90}
                    },
                    "required": ["path"]
                }
            )
        ]

    async def call_tool(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if tool_name != "convert_image":
            return ToolResult(content=json.dumps({"error": f"Unknown tool: {tool_name}"}), is_error=True)
        return await self._handle_convert(args)

    async def _handle_convert(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path")
        to = args.get("to", "jpeg")
        quality = args.get("quality", 90)

        if not isinstance(path, str) or not os.path.exists(path):
            return ToolResult(content=json.dumps({"error": f"Source path does not exist: {path}"}), is_error=True)

        if to not in ("jpeg", "png", "webp"):
            return ToolResult(content=json.dumps({"error": f"Invalid format: {to}. Choose 'jpeg', 'png', or 'webp'."}), is_error=True)

        if not isinstance(quality, int) or quality < 1 or quality > 100:
            return ToolResult(content=json.dumps({"error": "Quality must be an integer between 1 and 100."}), is_error=True)

        try:
            from PIL import Image
        except ImportError:
            return ToolResult(content=json.dumps({"error": "Pillow is not installed. Please install it with `pip install Pillow`. For HEIC support, install `pillow-heif`."}), is_error=True)

        try:
            img = Image.open(path)
        except Exception as e:
            return ToolResult(content=json.dumps({"error": f"Cannot open image: {e}. Note: HEIC files require the optional `pillow-heif` package."}), is_error=True)

        # Flatten transparency for JPEG
        if to == "jpeg":
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

        # Determine output path
        base, ext = os.path.splitext(path)
        ext_map = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}
        new_ext = ext_map[to]
        output_path = f"{base}{new_ext}"

        # Avoid overwriting
        counter = 1
        while os.path.exists(output_path):
            output_path = f"{base}-{counter}{new_ext}"
            counter += 1

        # Save
        if to == "jpeg":
            img.save(output_path, "JPEG", quality=quality)
        elif to == "png":
            img.save(output_path, "PNG", optimize=True)
        else:
            img.save(output_path, "WEBP", quality=quality)

        width, height = img.size
        return ToolResult(content=json.dumps({"output": output_path, "width": width, "height": height}))
