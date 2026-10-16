import os
import json
import asyncio
import tempfile
from PIL import Image
from plugins.image_convert import create

plugin = create()

def test_png_to_jpeg():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.png")
        Image.new("RGB", (100, 100), (255, 0, 0)).save(src, "PNG")
        result = asyncio.run(plugin.call_tool("convert_image", {"path": src, "to": "jpeg"}))
        assert not result.is_error
        data = json.loads(result.content)
        assert os.path.exists(data["output"])
        assert data["output"].endswith(".jpg")
        assert data["width"] == 100
        assert data["height"] == 100

def test_rgba_flattened_for_jpeg():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "rgba.png")
        Image.new("RGBA", (50, 50), (0, 255, 0, 128)).save(src, "PNG")
        result = asyncio.run(plugin.call_tool("convert_image", {"path": src, "to": "jpeg"}))
        assert not result.is_error
        data = json.loads(result.content)
        with Image.open(data["output"]) as out_img:
            out_img.load()
            out_img = out_img.copy()
        assert out_img.mode == "RGB"
        assert data["width"] == 50
        assert data["height"] == 50

def test_does_not_overwrite_existing():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.png")
        Image.new("RGB", (10, 10), (255, 255, 255)).save(src, "PNG")
        target = os.path.join(tmpdir, "test.jpg")
        Image.new("RGB", (1, 1), (0, 0, 0)).save(target, "JPEG")
        result = asyncio.run(plugin.call_tool("convert_image", {"path": src, "to": "jpeg"}))
        assert not result.is_error
        data = json.loads(result.content)
        assert data["output"] == target.replace(".jpg", "-1.jpg")

def test_missing_file_is_error():
    result = asyncio.run(plugin.call_tool("convert_image", {"path": "/nonexistent/path/image.png"}))
    assert result.is_error
    data = json.loads(result.content)
    assert "does not exist" in data["error"].lower()

def test_not_an_image_is_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "not_an_image.txt")
        with open(src, "w") as f:
            f.write("hello")
        result = asyncio.run(plugin.call_tool("convert_image", {"path": src, "to": "png"}))
        assert result.is_error
        data = json.loads(result.content)
        assert "cannot open" in data["error"].lower()


def test_la_and_palette_transparency_flatten_for_jpeg(tmp_path):
    """Regression: only RGBA has an alpha band at index 3."""
    from PIL import Image
    la = tmp_path / "la.png"
    Image.new("LA", (4, 4), (128, 0)).save(la)
    pal = tmp_path / "pal.png"
    p_img = Image.new("P", (4, 4), 0)
    p_img.info["transparency"] = 0
    p_img.save(pal, transparency=0)
    plugin = create()
    for src in (la, pal):
        res = asyncio.run(plugin.call_tool("convert_image", {"path": str(src), "to": "jpeg"}))
        assert not res.is_error, res.content
        with Image.open(json.loads(res.content)["output"]) as out:
            assert out.mode == "RGB"


def test_source_file_is_not_left_locked(tmp_path):
    from PIL import Image
    src = tmp_path / "a.png"
    Image.new("RGB", (2, 2)).save(src)
    res = asyncio.run(create().call_tool("convert_image", {"path": str(src), "to": "webp"}))
    assert not res.is_error
    src.unlink()   # raises PermissionError on Windows if the plugin kept it open

