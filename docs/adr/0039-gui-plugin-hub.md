# ADR-0039: The Hub -- one sidebar button for GUI plugins, user-pinnable and reorderable

**Date:** 2026-10-13
**Status:** Accepted (user direction, not yet built)
**Relates:** ADR-0037 (absorbed apps + the 2026-10-13 reconstructed-plugin
amendment), ADR-0012 (plugin-contributed panels; decision 6 "nav prominence and
dockability are orthogonal", decision 7 layout state in renderer localStorage),
ADR-0031 (interface guidelines), R7 in `CLAUDE.md` (plugins contribute panels,
never menu items or hotkeys).

## Context

ADR-0037 now has two kinds of GUI plugin: an external program Felix launches
(LibreOffice; later GIMP/Darktable) and a page Felix builds from a REA analysis.
Each would otherwise want its own sidebar button, and ADR-0012 decision 6 holds
the sidebar at four sections. The user's direction: keep them in one place by
default, but let them promote the ones they use.

## Decisions

1. **A Hub is one sidebar button** that opens a page of tiles, one per GUI
   plugin, launched and reconstructed alike. A tile is a panel contribution
   (ADR-0012); a plugin gains a Hub tile by declaring a GUI, never a sidebar
   button of its own.
2. **Pinning is the user's act, done by drag and drop.** Drag a tile out of the
   Hub onto the sidebar to pin it as its own button; drag a pinned button back
   onto the Hub button to unpin it. This is how R7 stays intact: the plugin never
   takes the slot, the user spends it.
3. **Sidebar buttons are reorderable by the same drag**, pinned tiles and the
   existing sections alike. The four built-in sections keep their identity (labels,
   routes); only their order is user-owned.
4. **Layout state is renderer `localStorage`, machine-global**, per ADR-0012
   decision 7. Order and pins are ergonomics, not identity: neither a System
   setting nor Profile-scoped. The Hub renders correctly with it empty.
5. **A tile click is a declared tool call** (ADR-0031 button invariant): launch
   the program, or open the page. Through the ADR-0005 gate like any invocation.

## Consequences

- **Not built today.** `tray/lib/sidebar-router.js` has no pin or reorder
  support; the user expected reordering to exist. Slices: H1 Hub page + GUI
  plugin registry, H2 drag-reorder of sidebar buttons (independent, useful on
  its own), H3 drag-pin/unpin between Hub and sidebar (reuses H2's drop
  machinery).
- ADR-0012 decision 6 is **extended, not reversed**: sidebar prominence is still
  never plugin-claimed; the user may now place pinned entries.
- Adding a Help topic for the Hub is part of H1's PR (CLAUDE.md Help-tab rule).
- The 16-class capability vocabulary (ADR-0005) is unchanged.
