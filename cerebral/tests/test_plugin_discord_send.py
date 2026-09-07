import importlib
import unittest
from unittest.mock import patch


class TestPluginDiscordSend(unittest.TestCase):
    def test_required_capabilities(self):
        plugin = importlib.import_module("plugins.discord_send")
        self.assertIsInstance(plugin.REQUIRED_CAPABILITIES, frozenset)
        self.assertTrue(len(plugin.REQUIRED_CAPABILITIES) > 0)

    @patch("plugins.discord_send.fetch_fn")
    def test_tool_call(self, mock_fetch):
        mock_fetch.return_value = {"status": "ok"}
        plugin.run()
        mock_fetch.assert_called_once()
