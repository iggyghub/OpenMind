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
