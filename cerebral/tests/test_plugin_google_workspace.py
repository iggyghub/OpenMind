"""Gate file for plugins/google_workspace_fallback.py (PLUGIN_NAME "google_workspace").

ADR-0034's registration gate keys on the plugin's NAME, so this file must exist under this exact
name or the plugin is refused at boot. The full suite is test_plugin_google_workspace_fallback.py.
"""
from plugins.google_workspace_fallback import PLUGIN_NAME, REQUIRED_CAPABILITIES, create


def test_required_capabilities():
    assert isinstance(REQUIRED_CAPABILITIES, frozenset)
    assert len(REQUIRED_CAPABILITIES) > 0


def test_create_registers_under_the_name_the_gate_checks():
    assert PLUGIN_NAME == "google_workspace"
    assert create().name == PLUGIN_NAME
