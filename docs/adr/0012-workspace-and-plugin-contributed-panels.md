# 12. Main window workspace: primary + secondary slots, and plugin-contributed panels as declarative specs

Date: 2026-07-20
Status: accepted (grill session, Felix UI Round 2)

## Context

The Main window renders one route at a time in a fixed layout: a `width: 180px`
sidebar with no collapse, and exactly one side-by-side split in 10,335 lines of
`main.html` (`.conv-pane-body` — thread backlog beside the transcript). Live use
surfaced the want plainly: *"conversation, then the plugins I can open (editors
or other integrations I would use)"* — the user wants the chat permanently
visible with a working surface beside it.

Two constraints shape every option. The renderer is `nodeIntegration:false` /
`contextIsolation:true` and talks to Cerebral over WebSocket only (ADR-0007),
with no bundler and one runtime dependency. And plugins are Python that
`builder.py` can **generate from natural language at runtime** — LLM-authored
code. The Main window is also the window that draws the Credentials and
Permissions UI.

## Decision

1. **The workspace is primary + secondary, not a free-form dock.** The
   Conversation permanently holds the primary slot. One secondary slot sits
   beside it with a drag splitter and a tab strip when several panels are open;
   closing it returns the Conversation to full width. A recursive split tree with
   drop-zone hit-testing was rejected: it is the bulk of the work in any docking
   library, and nothing in the stated need composes more than two things.
2. **A panel may be detached into its own OS window.** Cheap given decision 3 —
   a declaratively-drawn panel is self-contained, so a detached window is the
   same renderer with one panel and no dock. Requires extracting the inline WS
   bridge into a UMD-ish `tray/lib` module.
3. **Plugins contribute panels as declarative specs, never as code.** A plugin
   returns JSON describing a widget tree from a fixed vocabulary (list, table,
   form, detail, text); the renderer owns all drawing. No plugin-authored HTML or
   JavaScript ever enters the Main window. A sandboxed-iframe contract was
   considered and rejected: it hands script execution to LLM-generated plugins in
   the window holding the secrets UI, and adds a second renderer contract to
   maintain. This extends `schemaToFormHtml` (#472), which already renders a JSON
   Schema into form HTML from a whitelist of primitive types.
4. **The vocabulary's only editable widget is a plain `<textarea>`.** It covers
   plain text and Markdown. CodeMirror 6 ships as ESM and requires a bundler,
   which this project deliberately does not have; syntax highlighting on a résumé
   is not worth introducing a build step.
5. **ADR-0011 is narrowed, not reversed.** `.docx` editing still opens LibreOffice
   Writer as its own program, because no browser widget reproduces Word's layout
   engine. The text widget serves the plain/Markdown cases ADR-0011 never covered.
6. **Nav prominence and dockability are orthogonal.** The sidebar stays at the four
   sections #473 collapsed it to; making a view dockable does not restore it to the
   nav. Discovery and composition are separate concerns.
7. **Layout state is machine-global, in renderer `localStorage`**, keyed like
   `section-collapse.js`. Layout is ergonomics, not identity, so it is neither a
   System setting nor Profile-scoped. `position-store.js` cannot serve here — it is
   `require('fs')`, main-process only.

## Consequences

- Plugins gain a UI surface without gaining a code-execution surface. The security
  posture of an LLM-generated plugin is unchanged by giving it a panel.
- The vocabulary is a ceiling. A panel that cannot be expressed in list/table/form/
  detail/text cannot be built without widening the vocabulary — deliberately, so the
  pressure lands on a reviewed shared vocabulary rather than on per-plugin code.
- Detach adds a second `BrowserWindow` sharing the WS bridge; `tray/main.js` must
  change (the #473-era "do not modify main.js" constraint was campaign-scoped and
  has expired).
- The sidebar collapse and the splitter are independent of all of the above and can
  land first — the `.conv-backlog-panel` 200px→28px collapse pattern already exists
  to copy.
