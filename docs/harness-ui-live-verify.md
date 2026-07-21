# Harness UI rework -- live-verify checklist

Behaviour that can only be confirmed in a running Electron main window
lands here. Each slice appends items rather than editing prior ones; a
human ticks them off after eyeballing the UI. Backend / renderer logic
is covered by pytest / jest and MUST be green before an item is worth
running.

## S3 (#471) -- Harness section: filters, card grid, read-only drawer

- [ ] Open the tray and navigate to the Plugins pane. The new harness
      layout is visible: a narrow filter rail on the left and an auto-fit
      card grid on the right. The old plain list is not shown.
- [ ] Every plugin registered in `plugins/` appears as a card with its
      name, status dot, source-layout label, tool count, and up to 3
      capability tags ("+N" overflow for more).
- [ ] Plugins loaded from `plugins/_trusted/` always show the amber
      "trusted, unverified" badge on their card -- it is never hidden or
      collapsed regardless of other filter state.
- [ ] Registration refusals (entries in `errors[]`) appear as cards with
      a red left border, an error dot, and the `reason` string inline.
- [ ] The filter rail lists capability classes that have at least one
      plugin; classes with no plugins are absent. All 4 status filters
      (active / error / trusted, unverified / disabled) are always present.
- [ ] Clicking a capability filter highlights it and narrows the grid to
      plugins that declare that capability. Clicking again deselects.
      Clicking a second filter adds it (OR): any plugin matching either
      filter shows. "Clear filters" resets to show all.
- [ ] Clicking a card opens the detail drawer on the right as an overlay.
      Clicking the X button closes it. The drawer shows the plugin's name,
      status dot, trust badge (if trusted), all tools with descriptions,
      capability links, credential metadata (source + masked hint, no raw
      value), and source path.
- [ ] Clicking a capability link inside the drawer closes the drawer and
      activates that capability filter in the rail.
- [ ] The "Manage" button in the credentials section of the drawer opens
      the existing credential entry flow (navigates to Credentials pane or
      opens the credential modal).
- [ ] With no plugins discovered, the empty state message is shown instead
      of the grid.
- [ ] Applying a filter that matches no plugin shows the "No plugins match"
      message and the "Clear filters" action; no grid cards are visible.
- [ ] Stop Cerebral. Within ~5 s, the section shows the "Can't reach
      Cerebral. Retry" banner. No stale plugin cards remain visible (grid
      is cleared or banner covers it). Clicking Retry sends a new
      `plugins:list` request; once Cerebral is back up the banner hides
      and cards re-appear via `plugins:changed`.
- [ ] The Discord user settings sub-pane (opened from the old plugin list,
      now legacy/hidden) still works if triggered programmatically: the
      harness layout hides and the settings view appears; pressing Back
      returns to the harness layout.

## S1 (#469) -- plugins:list + plugins:changed broadcast

- [ ] With Cerebral running, connect the tray; the WS log shows a
      `plugins:changed` event delivered inside `_greet` AND one fired
      right after `Listening - waiting for tray connection` (startup-
      complete broadcast). Payload carries `plugins[]`, `errors[]`,
      `capability_vocabulary` (16 entries).
- [ ] Sending `{"type":"plugins:list"}` over the WS returns a
      `plugins:list` event with the same payload shape.
- [ ] For `google_workspace`, the `tools[]` entry for `gmail_send`
      carries `"supersedes": {"tool":"gmail_send","from_plugin":"gmail"}`.
- [ ] With a Todoist API token stored in the keyring, the `todoist`
      card's `credentials[0]` reads
      `{provider:"todoist", source:"keyring", hint:"****<last4>", env_var:null}`.
      Unset the keyring, set `TODOIST_API_TOKEN`, refetch: `source` flips
      to `"env"` and `env_var:"TODOIST_API_TOKEN"`; hint still masked.
- [ ] Grep the raw WS trace for the full token value; MUST NOT appear.

## S4 (#472) -- enable/disable toggle + plugins:test_call + args-form-from-schema

- [ ] Open the tray, navigate to the Harness section, click any card.
      The drawer header now shows a live `Disable` (or `Enable` for a
      disabled plugin) button in place of the S3 placeholder -- accent
      colour, not greyed out.
- [ ] Click `Disable` on an active plugin: the button briefly reads
      `Disabling...`, then the drawer re-renders with a `disabled` status
      dot, the toggle flips to `Enable`, and the card in the grid drops
      to reduced opacity. No visible page flash / optimistic state --
      the drawer waits for the `plugins:changed` broadcast to update.
- [ ] Click `Enable` on that same plugin: it re-registers, the status
      goes back to `active`, and the card returns to full opacity.
- [ ] Disable `google_workspace`, then hover the `gmail` card: the drawer
      lists `gmail_send` without a `supersedes` indicator (takeover reverted).
- [ ] In the drawer, each tool row now carries a `Test call` button plus
      an args form. Tools with a well-formed JSON Schema (e.g. `read_notes`
      with `{path: string}`) render individual labelled inputs; tools with
      no schema, arrays, or nested objects render a single JSON textarea
      pre-filled with `{}`.
- [ ] Required schema fields are marked with a red `*` next to the label.
- [ ] Click `Test call` on a read-only tool with valid args: the result
      area under the button appears, first as `Running...`, then with the
      truncated preview string. `is_error: false` styles it in the default
      colour. Repeat with an invalid arg (e.g. missing required): the
      response comes back with error styling (red) and a short error
      message from the tool.
- [ ] Click `Test call` on an irreversible tool (e.g. `gmail_send`): the
      existing irreversible-modal window appears exactly as it does for a
      voice-triggered `gmail_send`. Cancelling it produces an
      `is_error: true` response in the drawer; accepting it dispatches.
      No new confirm surface was built for this slice.
- [ ] Type malformed JSON into a fallback textarea and click `Test call`:
      the result area shows `Invalid JSON: ...` without hitting Cerebral.
- [ ] Grep the raw WS trace across an entire test-call session (form
      submit + response + irreversible modal ping-pong) for any long
      credential-looking string: MUST NOT appear. Only `content_preview`
      strings from the tool itself are present.

## S5 (#473) -- route collapse 16->4 + hash redirects + header profile switcher

- [ ] Open the tray. The sidebar nav shows exactly 4 buttons: Conversation,
      Harness, Library, Settings. No Profiles button, no sub-section labels.
- [ ] The header shows a profile switcher button (displaying the active
      profile name) to the left of the state pill. Clicking it opens a
      dropdown listing all profiles with Switch buttons; clicking "Manage
      profiles" navigates to the Profiles pane.
- [ ] Switching profiles via the dropdown updates the active profile and
      closes the dropdown; the button label updates to the new profile name.
- [ ] Navigate to Library. The tab bar shows: Memory | Insights | Recipes |
      Documents | Job Search. Clicking each tab shows the corresponding
      content and updates the URL hash to `#library/<sub>`.
- [ ] Navigate to Harness. The existing plugin card grid and drawer appear.
      Old `#plugins`, `#integrations`, `#credentials`, `#permissions` hashes
      all redirect to `#harness` (test by typing them in the URL bar).
- [ ] Old hashes redirect correctly: `#memory` → Library (memory sub-tab),
      `#models` → Settings, `#quick-ask` → Conversation.
- [ ] Navigate to `#harness/my_plugin_name` (replace with a real plugin name):
      the Harness pane opens and the drawer for that plugin auto-opens once
      the plugin list loads.
- [ ] The Profiles pane is still reachable via `#profiles` (for the first_run
      wizard flow) even though it has no nav button.

## S1 (#480) -- sidebar collapse to icon rail

- [ ] Open the tray. The sidebar shows "Felix" brand text plus a small ◄
      toggle button in the top-right of the brand bar. Four nav items show
      an icon (✦ Conversation, ⚙ Harness, ☰ Library, ◆ Settings) and their
      text label, exactly as before.
- [ ] Click the ◄ toggle button. The sidebar animates to a narrow 48 px
      icon rail; the brand text "Felix" disappears; the nav labels hide;
      only the four icons remain visible, each still clickable. The toggle
      button now shows ►.
- [ ] While collapsed, click each rail icon in turn. Each navigates to the
      correct route (Conversation, Harness, Library, Settings) exactly as
      the full sidebar does. The active icon receives the accent highlight.
- [ ] Press Ctrl+B while the sidebar is collapsed. The sidebar re-expands
      with the animation, labels reappear, and the toggle reverts to ◄.
      Press Ctrl+B again to collapse it back.
- [ ] Reload the window (Ctrl+R or close/reopen). The sidebar restores to
      whichever state (collapsed or expanded) it was in before the reload.

## S2 (#481) -- workspace shell (primary + secondary slot)

- [ ] Open the tray, land on Conversation. Header shows a compact "+ Panel"
      dropdown near the state pill; the right side of the window shows the
      Conversation full-width (no secondary slot visible).
- [ ] Type a message in the composer; scroll the transcript up a few turns.
      Note the scroll position and the composer draft text.
- [ ] Pick "Documents" from the "+ Panel" dropdown. The window splits: the
      Conversation stays in the left slot at the same width it takes when
      docked with a companion (its scroll position and draft text are still
      exactly where you left them -- the transcript is not remounted); a
      new "Documents" panel appears on the right (fixed 480 px, the drag
      splitter lands in S3). No tab strip yet -- only one panel is open.
- [ ] Pick "Job Search" from the dropdown. The tab strip appears at the top
      of the secondary slot with two tabs: "Documents" and "Job Search";
      "Job Search" is the active tab. Click "Documents" -- it becomes the
      active tab and the panel body swaps.
- [ ] Click the × on the "Documents" tab. Documents closes; "Job Search"
      is the only remaining tab; the tab strip disappears (only 1 panel
      open) but the "Job Search" panel content is still visible in the
      secondary slot.
- [ ] Click the × on "Job Search". The secondary slot collapses entirely
      and the Conversation returns to full width. The Conversation scroll
      position and draft text are still preserved from step 2.
- [ ] Re-open Documents + Job Search, activate Documents, then reload the
      window (Ctrl+R). Both panels re-open in the same order and Documents
      is still the active tab.
- [ ] With both panels open, click Harness in the sidebar. The primary slot
      swaps to the Harness route as before, and the secondary slot still
      shows the active panel beside it (workspace layout is orthogonal to
      the sidebar route -- ADR-0012 decision 6). Click Conversation to
      return; secondary is unchanged.
- [ ] Collapse the sidebar with Ctrl+B while both panels are open. The
      layout still holds: icon-rail sidebar | Conversation | tab strip +
      panel, nothing overlaps.

## UI2 S3 (#482) -- drag splitter between workspace slots

- [ ] Open a panel (e.g. "Documents") from the "+ Panel" dropdown. A 5px
      drag handle appears between the Conversation and the secondary slot.
- [ ] Hover over the splitter: it highlights (border fill visible).
- [ ] Drag the splitter left: the secondary slot widens live; primary shrinks
      to fill remaining space. Release -- the new width persists.
- [ ] Drag the splitter to the far right: the secondary slot stops at its
      minimum width (~180px) and the primary still has usable space.
- [ ] Drag the splitter to the far left: the secondary stops before the
      primary is squeezed below ~200px minimum.
- [ ] Reload the window (Ctrl+R). The secondary slot reopens at the same
      width that was set before the reload (width persists in localStorage).
- [ ] Close all panels -- secondary slot collapses AND the splitter disappears
      (no orphan drag handle). Open a panel again -- splitter reappears.

## UI2 S4 (#483) -- panel spec v1 end-to-end (plugin-declared panels)

- [ ] Open the tray. The "+ Panel" dropdown in the header only has "Documents"
      listed under the placeholder (the S2/S3 hardcoded "Job Search" demo
      option is gone -- the registry is now populated at runtime from the
      `plugins:panels` WS event).
- [ ] Pick "Documents" from the dropdown. The secondary slot opens with a
      panel titled "Documents". Two widgets are visible: a list of the docs
      the active profile has stored (or an "Empty." marker when the library
      is empty), and a "detail" block below with two label/value rows --
      Documents=N, Library=profile-scoped.
- [ ] Store a new document via `doc_store` (or from the Documents sidebar
      pane). The workspace panel updates live: the new doc appears in the
      list widget and the "Documents" count in the detail widget increments.
      No manual refresh needed -- `_docs_broadcast` re-broadcasts the fresh
      panel spec whenever documents change.
- [ ] Delete/revert a doc so the library shrinks. The list widget drops the
      row and the count decreases live.
- [ ] Open the WS trace (developer tools -> Network -> ws). No `plugins:list`
      -esque payload carries any secret value in the panel spec -- it is
      pure metadata (doc names, kinds, counts).
- [ ] Grep the raw WS trace for `<script`. It does not appear -- plugins
      never ship markup or code through this channel.
- [ ] Confirm the workspace panel only shows list/detail widgets rendered
      by the whitelist. If a plugin ever returns an unknown widget type
      (e.g. `text`, `table`, `form` -- reserved for A4/A5), the workspace
      panel simply omits that widget rather than injecting anything.
- [ ] Close the Documents tab -> reopen. The panel re-fetches its spec via
      `plugins:panel_spec` and renders correctly.
- [ ] Reload the window (Ctrl+R). If Documents was open on the last session
      it reopens (existing workspace persistence) and its spec is fetched
      fresh from Cerebral before the body is drawn.

## UI2 S6 (#485) -- detach a panel into its own window

- [ ] Open the Documents panel in the secondary slot. Each tab now shows
      three affordances after the label: the detach glyph (⧉), the close
      glyph (×), and the tab itself (click-to-activate).
- [ ] Click ⧉ on the Documents tab. The tab disappears from the secondary
      slot AND a new OS window opens showing a header ("Documents") and the
      same list/detail widgets. The window has an independent title bar,
      resizes freely, and is not always-on-top.
- [ ] Store a new document (from the Documents sidebar pane or another
      profile session). The detached window's list widget updates live --
      it has its own WS bridge and receives `plugins:panel_spec` broadcasts
      directly from Cerebral.
- [ ] Open the detached window's DevTools (Ctrl+Shift+I). Confirm
      `process` is undefined and `require` is undefined -- proving the
      posture is `nodeIntegration:false` / `contextIsolation:true` like
      the Main window (SAFETY #2, ADR-0007).
- [ ] Close the detached window. The panel does NOT reappear in the docked
      slot; it is deliberately dropped (open it again from the "+ Panel"
      opener). No WS reconnect chatter continues after the window closes
      (check the Cerebral log for a reasonable disconnect).
- [ ] Detach again, then close the Main window (X hides to tray per #188).
      The detached window stays open and its WS keeps working -- it is a
      standalone renderer.
- [ ] Reload the Main window (Ctrl+R) while a detached panel is open. The
      detached window is unaffected. On a fresh Main-window reload with no
      detached windows open, no detached panel auto-restores -- this is
      the deliberate "not persisted" branch of the AC (a plain re-open
      via the "+ Panel" dropdown suffices).
- [ ] Right-click inside the detached window's body and try to open its URL
      in a browser or invoke any `window.open(...)` from DevTools -- it is
      denied at the main-process `setWindowOpenHandler` guard. Only the
      whitelisted `detached-panel.html` may be opened.

## S8 #487 -- memory proposals: prompt guidance + user confirms

**Fact quality (does Felix remember the right things?)**

- [ ] Tell Felix a durable fact: "I prefer dark mode". Verify a memory
      proposal appears in the queue (title starts "Remember: "). Do NOT
      see it silently written to the Memory pane without confirmation.
- [ ] Approve the proposal. Open the Library Memory tab -- the fact
      appears there immediately (no page reload needed).
- [ ] Tell Felix another fact: "My wife's name is Aria". A second proposal
      appears. Dismiss it. Open the Library Memory tab -- "Aria" does NOT
      appear in memory.
- [ ] Ask Felix "what time is it?" (a non-durable request). No memory
      proposal is raised; Felix uses the time tool or replies in text.
- [ ] Tell Felix "I like jazz". Approve the proposal. Ask Felix "what music
      do I like?" -- it should recall "jazz" from memory in its answer.
