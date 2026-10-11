"""State shared by every loaded copy of a plugin module.

The orchestrator loads plugins/<name>.py as ``openmind_plugin_<name>`` (#153),
so a plugin that does ``import plugins.<name>`` gets a SECOND module instance
with its own globals. Seams main.py wires, or live state like a running crawl,
then exist in only one copy. Plugins keep such state here instead, as the
video plugin does with its cerebral.video.* modules.
"""

state: dict = {}
