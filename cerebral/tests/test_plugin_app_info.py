import asyncio
import json
import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from plugins.app_info import create


def test_app_config_fields():
    plugin = create(root=Path("/tmp/fake_root"))
    result = asyncio.run(plugin.call_tool("app_config", {}))
    data = json.loads(result.content)
    assert data["name"] == "Felix"
    assert isinstance(data["commit"], str)
    assert isinstance(data["platform"], str)
    assert isinstance(data["os_class"], str)
    assert isinstance(data["python"], str)
    assert Path(data["user_data_path"]) == Path("/tmp/fake_root") / "cerebral" / "data"


def test_user_data_path_under_root():
    plugin = create(root=Path("/custom/root"))
    result = asyncio.run(plugin.call_tool("user_data_path", {}))
    data = json.loads(result.content)
    assert Path(data["path"]) == Path("/custom/root") / "cerebral" / "data"


def test_os_theme_dark_and_light():
    plugin_dark = create(theme_read=lambda: 0)
    result_dark = asyncio.run(plugin_dark.call_tool("os_theme", {}))
    assert json.loads(result_dark.content) == {"dark": True}

    plugin_light = create(theme_read=lambda: 1)
    result_light = asyncio.run(plugin_light.call_tool("os_theme", {}))
    assert json.loads(result_light.content) == {"dark": False}


def test_resolve_host_returns_unique_sorted():
    mock_resolver = MagicMock(side_effect=lambda h, p, f: [
        ("AF_INET", socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0)),
        ("AF_INET", socket.SOCK_STREAM, 6, "", ("192.168.1.2", 0)),
        ("AF_INET", socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0))
    ])
    plugin = create(resolver=mock_resolver)
    result = asyncio.run(plugin.call_tool("resolve_host", {"host": "example.com", "family": "ipv4"}))
    data = json.loads(result.content)
    assert data["addresses"] == ["192.168.1.1", "192.168.1.2"]
    assert data["host"] == "example.com"


def test_resolve_host_rejects_bad_names():
    plugin = create()
    result = asyncio.run(plugin.call_tool("resolve_host", {"host": "-invalid.com"}))
    assert result.is_error is True
    data = json.loads(result.content)
    assert data.get("is_error") is True


def test_resolve_host_error_is_reported():
    mock_resolver = MagicMock(side_effect=socket.gaierror("Host not found"))
    plugin = create(resolver=mock_resolver)
    result = asyncio.run(plugin.call_tool("resolve_host", {"host": "test.local", "family": "any"}))
    assert result.is_error is True
    data = json.loads(result.content)
    assert data.get("is_error") is True
    assert "error" in data
