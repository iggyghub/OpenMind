# UI & Harness Overhaul — Human Live-Verify Checklist

Each slice below lists the visual/interactive checks that a headless run cannot
make. Run these in the live app after merging the slice's PR.

---

## S1 — Render-smoke harness + live-verify doc (#284)

S1 introduces no visible UI change — it only builds the headless smoke harness
and creates this document. Verify the harness itself works:

- [ ] Run `npm test` inside `tray/` and confirm all tests pass (including the
      new `render-smoke` suite).
- [ ] Confirm `.claude/tmp/render-smoke/last-run.json` is written after the
      run and lists all 9 expected routes in `panes_found` and `nav_items_found`.
- [ ] Open the live Felix window and confirm the existing UI is unchanged: all
      9 sidebar nav items (Conversation, Queue, Insights, Memory, Permissions,
      Credentials, Plugins, Profiles, Settings) are present and their panes
      activate on click.

---

## S2 — Grouped sidebar nav (#285)

- [ ] Open the live Felix window and confirm the sidebar nav is now grouped into
      four sections with headers: CHAT, MIND, TOOLS, SYSTEM.
- [ ] CHAT section contains: Conversation, Queue, Conversations.
- [ ] MIND section contains: Insights, Memory, Recipes.
- [ ] TOOLS section contains: Plugins, Integrations, Credentials, Permissions.
- [ ] SYSTEM section contains: Models, Settings, Profiles.
- [ ] Each section header label is visible and styled (small caps, muted colour).
- [ ] All pre-existing panes still activate on click (Conversation, Queue,
      Insights, Memory, Credentials, Permissions, Plugins, Profiles, Settings).
- [ ] Clicking Conversations, Recipes, Integrations, Models each shows a stub
      placeholder pane with a title, description, and "Coming in issue #N" label.
- [ ] Hash routing still works: navigating to e.g. `#memory` activates the
      Memory pane and highlights the Memory nav item.
- [ ] The active nav item retains the accent left-border highlight.

---

## S3 — Collapsible section headers (#286)

- [ ] Open Settings. Confirm each section header (Active model, Switch model,
      Per-task models, Notifications, System) shows a right-aligned chevron (▾).
- [ ] Click "Notifications" header — its rows collapse (hidden) and the chevron
      changes to ▸.
- [ ] Click "Notifications" again — rows expand and chevron returns to ▾.
- [ ] Reload the page. Navigate back to Settings. Confirm "Notifications" is
      still collapsed (state survived reload).
- [ ] Open Permissions → Capabilities tab. Confirm its three section headers
      (Capability classes, Session grants, New plugins to review) each have a
      chevron and are click-to-collapse.
- [ ] Open Plugins. Confirm "Registered plugins" header has a chevron and
      collapses/expands its plugin list.
- [ ] Open Profiles. Confirm "Profiles" header has a chevron and collapses the
      profile list and New profile button when clicked.
- [ ] Confirm Queue and Insights item-level collapse is unchanged (rows still
      start collapsed as before; section headers in those panes are unaffected).

---

## S4 — Federated search shell (#287)

- [ ] Open the live Felix window. Confirm the header has a search input
      between the "Felix" title and the health/state pill (placeholder text:
      "Search this pane and everywhere else...").
- [ ] Confirm the search bar is visible from EVERY pane (switch through
      Conversation, Queue, Insights, Memory, Permissions, Credentials,
      Plugins, Profiles, Settings, Models, Conversations, Integrations,
      Recipes) -- it lives in the static header, not in any single pane.
- [ ] Open the Plugins pane. Type a substring of a plugin name (e.g. `gmail`).
      Confirm only plugin rows whose name contains the substring stay visible;
      other rows are hidden. Clear the input -- all rows return.
- [ ] Still on Plugins, type a query that matches NOTHING in plugins but
      matches a setting (e.g. `notifications`). Confirm the "Found elsewhere"
      dropdown opens under the search bar with at least one Settings hit
      tagged with the route `settings`.
- [ ] Click the "Notifications" jump link. Confirm the active pane switches
      to Settings and the Notifications row is scrolled into view.
- [ ] Open Credentials and set at least one API key (or just observe an
      existing setup). Then type the FIRST FEW CHARS of any saved API token
      value (NOT the label, the actual secret). Confirm NO credentials row
      appears anywhere -- not in current pane, not in "Found elsewhere".
- [ ] Type a credential's label (e.g. `Google`, `OpenAI`, `Todoist`).
      Confirm the matching row appears with a status word ("connected",
      "set", "not set", etc.) -- never with the actual token value.
- [ ] Open Permissions -> Tools tab. Type into the header search and confirm
      tools matching the query appear in "Found elsewhere" with route
      `permissions`. Clicking one navigates to the Permissions pane.
- [ ] Press the header search input, then press Escape or click outside the
      search column. Confirm the "Found elsewhere" dropdown closes.
- [ ] Confirm that switching panes while the search box still has text
      re-applies the in-pane filter on the new pane (e.g. switching from
      Plugins with `gmail` typed to Permissions filters Permissions tools
      live without re-typing).

---

## S5 — Models tab (#288)

- [ ] Open the live Felix window. Click **Models** in the SYSTEM section of the
      sidebar. Confirm the pane shows three section headers: "Active model",
      "Switch model", and "Per-task models", plus a "Refresh installed models"
      button.
- [ ] Confirm **Settings** (SYSTEM section) no longer shows any model controls
      — only "Notifications" and "System" sections remain.
- [ ] With Cerebral running: confirm the Models pane shows the currently active
      model name and a local/cloud badge under "Active model".
- [ ] Click a different model in the "Switch model" list. Confirm the active
      model updates (radio dot moves, name in the header updates).
- [ ] Expand a task card in "Per-task models" and assign a model to one task.
      Confirm the card's current model label updates.
- [ ] Click "Refresh installed models". Confirm the button briefly shows
      "Refreshing..." then re-enables; the model list updates if Ollama is
      running.
- [ ] Type "active model" in the header search bar from any pane other than
      Models. Confirm the "Found elsewhere" dropdown shows an "Active model"
      hit with route `models`. Click it — confirm navigation to the Models pane.
- [ ] Confirm the S3 collapsible chevrons appear on each section header in the
      Models pane (Active model, Switch model, Per-task models) and that
      collapse/expand works.

---

## S6 — Appearance settings (#289)

- [ ] Open the live Felix window and navigate to **Settings** (SYSTEM section).
      Confirm an **Appearance** section is present at the top of the Settings
      pane, above Notifications. Confirm the Appearance section has a collapsible
      chevron (S3 behaviour).
- [ ] Under Appearance, confirm three controls are visible: a **UI scale**
      dropdown (90% / 100% / 110% / 125%), a **Theme** chip group (Midnight /
      Light / High Contrast), and an **Accent colour** picker.
- [ ] The **Midnight** theme chip should appear active (highlighted in coral)
      by default.
- [ ] Change UI scale to **110%**. Confirm the entire Felix window zooms in
      immediately. Reload the page — confirm it opens at 110%.
- [ ] Click the **Light** theme button. Confirm the background, sidebar, and
      text colours all change immediately to a light palette. Reload — confirm
      the Light theme is still applied.
- [ ] Click **High Contrast**. Confirm the UI switches to a high-contrast
      black-and-white palette. Reload — still HC.
- [ ] Click **Midnight** to restore the default dark theme.
- [ ] Open the **Accent colour** picker and choose a different colour (e.g.
      green or red). Confirm the accent changes live: the active nav item
      left-border, the orb glow, the state pill border (when speaking), and
      other accent-coloured elements update immediately. Reload — accent persists.
- [ ] Set the scale back to **100%** and accent back to the default purple
      (#7c5cfc). Reload and confirm both are restored.
- [ ] From a pane other than Settings, type **"theme"** in the header search
      bar. Confirm a hit for "Theme" appears in the "Found elsewhere" list
      with route `settings`. Click it — confirm navigation to Settings.
- [ ] Type **"accent"** in the search bar from any other pane. Confirm an
      "Accent colour" hit appears. Type **"ui scale"** — confirm a "UI scale"
      hit appears.

---

## S7 — Mic-mode control (#290)

- [ ] Open the live Felix window. Confirm the static header (visible on every
      pane) shows a segmented control with three buttons: **Passive**, **PTT**,
      **Disabled**. The **Passive** segment should be highlighted (accent
      background) by default.
- [ ] Confirm the control is visible regardless of which sidebar pane is active
      (switch through Conversation, Queue, Insights, Memory, Models, Settings,
      etc.).
- [ ] Click **Disabled** in the header control. Confirm it becomes highlighted
      and the other segments are un-highlighted. With Cerebral running, confirm
      the mic goes silent (no wake-word detection).
- [ ] Click **PTT** in the header control. Confirm it highlights. (PTT behaviour
      requires a future hotkey binding; for now confirm the setting persists.)
- [ ] Click **Passive** to restore the default.
- [ ] Navigate to **Settings** (SYSTEM section). Confirm a **Voice input**
      section appears with a **Mic mode** dropdown showing Passive / Push to
      talk / Disabled.
- [ ] Change the dropdown in Settings to **Disabled**. Confirm the header
      segmented control also switches to **Disabled** (single source of truth).
- [ ] Change the header control back to **Passive**. Confirm the Settings
      dropdown also updates to **Passive**.
- [ ] Reload the app. Confirm the last-set mic mode is restored in both the
      header control and the Settings dropdown (persisted via Cerebral).
- [ ] With Cerebral running: set mode to **Disabled**, then reload. Confirm
      Felix does not respond to the wake word.

---

## S8 — TTS controls (#291)

**Header controls (mute + volume)**

- [ ] Open the Felix window. Confirm the static header (visible on every pane)
      now contains a **vol** button and a volume slider, to the right of the
      mic-mode segmented control.
- [ ] Click the **vol** button. Confirm it changes to **muted** and the button
      appears dimmed (opacity reduced). With Cerebral running, confirm Felix no
      longer speaks responses aloud.
- [ ] Click **muted** again. Confirm it reverts to **vol** (un-dimmed) and TTS
      resumes on the next response.
- [ ] Drag the volume slider left (toward 0). Confirm Felix speaks more quietly.
      Drag it right (toward 100) and confirm full volume returns.

**Settings pane (Voice output section)**

- [ ] Navigate to **Settings** (SYSTEM section). Confirm a new **Voice output**
      section appears with three rows: **Text-to-speech** toggle, **Volume**
      slider with percentage label, and **Voice** dropdown.
- [ ] Toggle **Text-to-speech** off. Confirm the header mute button changes to
      **muted** (single source of truth — they stay in sync).
- [ ] Toggle **Text-to-speech** back on. Confirm the header button returns to
      **vol**.
- [ ] Move the **Volume** slider in Settings. Confirm the header slider moves
      to the same value, and the percentage label updates.
- [ ] Move the header volume slider. Confirm the Settings slider and label
      update to match.

**Voice picker (per-profile)**

- [ ] With Cerebral running, open Settings and confirm the **Voice** dropdown is
      populated with Kokoro voice names (e.g. Heart, Bella, Adam).
- [ ] Select a different voice and send a message. Confirm Felix responds in the
      new voice.
- [ ] Switch to a different profile (Profiles pane) and confirm the Voice
      dropdown updates to that profile's last-saved voice.
- [ ] Switch back to the original profile and confirm its voice is restored.

**Persistence across reload**

- [ ] Set volume to 50 and mute TTS. Reload the app. Confirm muted state and
      volume (50%) are restored in both header and Settings.
- [ ] Unmute and confirm the volume is still 50 after the unmute.

---

## S9 — Conversation threads (#292)

**Thread strip on the Conversation pane**

- [ ] Open the live Felix window and navigate to **Conversation** (CHAT
      section). Confirm a thin strip is visible at the very top of the
      pane (above the transcript), containing an editable thread title on
      the left and a **+ New conversation** button on the right.
- [ ] On a fresh profile with no turns yet, the title reads
      **Untitled conversation** in muted/italic text.

**Existing conversations migrate non-destructively**

- [ ] If the profile already had a transcript from before S9 landed,
      confirm the transcript is still visible (turns intact) and the
      thread title strip shows **Legacy conversation**. (Schema migration
      back-fills pre-#292 turns into one per-profile Legacy thread.)

**Auto-title after the first exchange**

- [ ] On a new conversation (click **+ New conversation**), type a
      message such as **"Plan my Tokyo trip"** and press Enter. Wait for
      Felix to respond.
- [ ] After Felix's first response, the title strip updates from
      **Untitled conversation** to **Plan my Tokyo trip** (truncated to
      ~60 chars with "..." if longer).
- [ ] Send a second message. The title does NOT change again -- auto-title
      fires once, from the first exchange only.

**Editable title**

- [ ] Click the thread title. The title becomes editable (cursor
      appears, text selectable). Type a new title and press Enter.
      Confirm the new title persists in the strip.
- [ ] Click the title and press Escape mid-edit. Confirm the original
      title is restored (no rename committed).
- [ ] Reload the app. Confirm the edited title survives.

**New conversation button**

- [ ] With an existing conversation visible (turns in transcript), click
      **+ New conversation**. The transcript clears to the empty state
      and the title strip resets to **Untitled conversation**.
- [ ] Send a message in the new conversation. Confirm the turn appears.
- [ ] Reload the app. The new thread is the active one on reload
      (Cerebral remembers the most-recently-updated thread per profile).
      The previous conversation's turns are NOT visible in the
      transcript -- they live in their own thread (the saved-list UI
      ships in S10).

**Profile switch keeps threads isolated**

- [ ] If you have two profiles, send a message in profile A, then switch
      to profile B (Profiles pane). Confirm profile B's transcript and
      title strip are independent (B sees its own active thread or
      Untitled if none).
- [ ] Switch back to A. Confirm A's thread title and turns are restored.

---

## S10 — Save / delete / search conversations (#293)

**Conversations pane list**

- [ ] Open the live Felix window and click **Conversations** in the CHAT section
      of the sidebar. Confirm the pane is no longer a placeholder: it shows a
      "Saved conversations" section header, a search input, and the list of saved
      threads (one row per thread showing its title, turn count, and date).
- [ ] Confirm the currently-active thread row has a left accent border (is-active
      highlight) distinguishing it from other rows.
- [ ] On a fresh profile with no conversations yet, confirm the empty state message
      ("No saved conversations yet.") is shown instead of an empty list.

**Open a thread**

- [ ] In the Conversations pane, click the **Open** button on any thread that is
      NOT the active one. Confirm the view switches to the Conversation pane and
      the correct thread's transcript and title are shown.
- [ ] Confirm the thread that was opened is now the active one (returning to
      Conversations shows its row with the accent border).

**Delete a thread**

- [ ] Click the **Delete** button on a thread. Confirm a confirmation dialog
      appears naming the thread being deleted.
- [ ] Click **Cancel** in the dialog. Confirm the thread is still in the list.
- [ ] Click **Delete** again and confirm in the dialog. Confirm the thread row
      disappears from the list and the turn count in the list updates.
- [ ] If the deleted thread was the active one, confirm the Conversation pane
      switches to another thread (or shows the empty state) automatically.
- [ ] With Cerebral running, confirm that after deleting a thread its turns are
      gone from the DB: reloading the app does not bring them back.

**In-pane search**

- [ ] Type a substring of a thread title into the search input in the
      Conversations pane. Confirm the list filters to show only matching threads.
- [ ] Clear the search input. Confirm all threads return.
- [ ] Type text that appears in the BODY of a turn (not the title). After a
      brief moment, confirm the Conversations pane updates to show the thread(s)
      that contain that turn text.
- [ ] Type a query that matches nothing. Confirm the empty state message appears.

**Global search provider**

- [ ] Navigate to any pane other than Conversations. Type a partial thread title
      in the header search bar. Confirm a hit for that thread appears in the
      "Found elsewhere" list with route `conversations`.
- [ ] Click the jump link. Confirm the Conversations pane activates and the
      matching thread is visible in the list.

---

## S11 -- Projects (folders) (#294)

**Projects toolbar + Unfiled bucket**

- [ ] Open the live Felix window and click **Conversations** in the CHAT
      section. Confirm a **+ New project** button is visible at the top
      right of the list, above the conversations.
- [ ] On a fresh profile (or one without projects), confirm there is a
      single project group titled **Unfiled** containing all saved
      conversations (or the empty-list message if there are none).
- [ ] The Unfiled group's header shows a chevron, the label "Unfiled", and
      a meta count (e.g. "3 threads" or "empty"). Unfiled has NO Delete
      button (it cannot be removed).

**Create a project**

- [ ] Click **+ New project**. A new project group appears below Unfiled,
      with its name shown as **"Untitled project"** in muted italic text.
- [ ] Click the project name text. It becomes editable. Type a name (e.g.
      **"Trips"**) and press Enter. Confirm the name persists and the muted
      italic styling is gone.
- [ ] Reload the app. Navigate back to Conversations. Confirm the project
      still exists with the same name.

**Move a thread between projects**

- [ ] In any conversation row inside Unfiled, click the dropdown showing
      "Unfiled" on the right side. Select your **"Trips"** project.
- [ ] Confirm the thread row jumps from the Unfiled group into the Trips
      group. The Trips header meta updates to reflect the new thread count.
- [ ] In the now-moved row, change the dropdown back to "Unfiled".
      Confirm the row returns to the Unfiled group.

**Rename a project**

- [ ] Click the **"Trips"** project name. Edit it to **"Travel plans"**
      and press Enter. Confirm the name updates in the header.
- [ ] Click the project name and press Escape mid-edit. Confirm the
      original name is restored (no rename committed).

**Collapsible groups (per S3 styling)**

- [ ] Click the **"Travel plans"** project header (NOT the name or the
      Delete button). Confirm the body collapses (threads hidden) and the
      chevron changes to ▸. Click again -- the body expands and the
      chevron is ▾ again.
- [ ] Click the Unfiled header. Confirm Unfiled also collapses/expands.

**Delete a project leaves threads Unfiled (spec AC)**

- [ ] Move a thread into the **"Travel plans"** project so it has at
      least one thread.
- [ ] Click the **Delete** button on the Travel plans header. Confirm a
      confirmation dialog warns that **conversations inside it will move
      to Unfiled** (and not be deleted).
- [ ] Click Cancel. Confirm the project is still present.
- [ ] Click Delete again and confirm in the dialog. Confirm the project
      row disappears. The thread that was inside it is now visible in the
      Unfiled group (its turns are intact -- click Open and verify the
      transcript is unchanged).

**Profile isolation**

- [ ] If you have two profiles, create a project under profile A, then
      switch to profile B. Confirm profile B's Conversations pane does
      NOT show profile A's project (each profile's projects are scoped to
      that profile).
- [ ] Switch back to A. Confirm A's project (and its threads) reappear.

**Persistence**

- [ ] Create at least one project. Reload the app. Confirm the project
      group + its threads are still present and the meta counts are
      correct.

---

## S12 -- Quick Ask (#295)

**Nav item and pane**

- [ ] Open the live Felix window. Confirm the CHAT section in the sidebar
      now shows **Quick Ask** as the first item (above Conversation).
- [ ] Click **Quick Ask**. Confirm a new pane opens with a strip at the top
      showing the title "Quick Ask", a "Web - Ephemeral" badge, and a
      **Clear** button.
- [ ] Confirm the pane shows an empty-state message explaining it is not
      saved, and a composer at the bottom with a textarea and a **Send**
      button.

**Sending a message**

- [ ] Type a question (e.g. "What is the current price of Bitcoin?") and
      press **Enter** (or click **Send**). Confirm the user message appears
      immediately in the Quick Ask transcript as a user bubble.
- [ ] With Cerebral running and the web-search plugin active, wait for
      Felix's response. Confirm it appears as a felix bubble sourced from
      web search.
- [ ] Navigate to the **Conversations** pane. Confirm the Quick Ask message
      does NOT appear in any thread or project folder -- it is ephemeral.
- [ ] Navigate to the **Conversation** pane. Confirm the Quick Ask message
      does NOT appear in the main transcript.

**Clear button**

- [ ] Send a few messages in Quick Ask. Click the **Clear** button. Confirm
      all turns are removed and the empty-state message reappears.
- [ ] Reload the app. Navigate to Quick Ask. Confirm it is empty -- turns
      are never persisted across sessions.

**Max-turn trim**

- [ ] Send more than 20 messages in Quick Ask without clearing. Confirm that
      old turns are removed from the top of the transcript as new ones arrive
      (the pane keeps at most 20 turns visible).

**Keyboard and focus**

- [ ] Click **Quick Ask** in the sidebar. Confirm the composer textarea
      receives focus automatically.
- [ ] Type a multi-line message using Shift+Enter. Confirm the textarea
      expands. Press Enter alone to send and confirm only the last line is
      NOT appended (Enter sends, Shift+Enter inserts newline).

---

## S13 -- Per-conversation model override (#296)

**Thread strip model select**

- [ ] Open the live Felix window and navigate to the **Conversation** pane.
      Confirm a model select dropdown is visible in the thread strip, to the
      left of the "+ New conversation" button.
- [ ] Confirm the select defaults to "Global default" on a fresh thread with
      no override set.
- [ ] Confirm the select is populated with all available models (same list as
      the Models pane).

**Setting an override**

- [ ] With at least two models available, select a non-default model from the
      thread strip dropdown. Confirm the selection is saved (navigate away and
      back -- the dropdown still shows the pinned model).
- [ ] Send a message in that thread. Confirm (via Cerebral logs or response
      characteristics) that the response uses the pinned model rather than
      the global active model.

**Conversations list badge**

- [ ] Navigate to the **Conversations** pane. Confirm the thread with the
      pinned model shows a model badge next to its title (e.g. "[Claude Sonnet 4.6]").
- [ ] A thread with no override shows no badge.

**Clearing the override**

- [ ] Return to the **Conversation** pane for the pinned thread. Change the
      model select back to "Global default". Confirm the badge disappears
      from the thread row in the Conversations pane.
- [ ] Send another message. Confirm it uses the global active model again.

**Persistence**

- [ ] Reload the app. Navigate to a thread that had a model override pinned.
      Confirm the override is still set in the dropdown (persisted across
      restarts).


---

## S14 — File upload (#297)

**Attach via paperclip**

- [ ] In the **Conversation** composer, click the paperclip and pick a file.
      Confirm an attachment chip appears showing the filename and type icon.
- [ ] Click the chip's remove (x) before sending. Confirm it is removed and
      not attached to the next turn.

**Drag-and-drop**

- [ ] Drag a file from Explorer onto the composer. Confirm a chip appears the
      same as the paperclip path.

**Extraction routing**

- [ ] Attach a PDF or .txt, send "summarise this". Confirm Felix's reply
      reflects the file's text content (extraction path).
- [ ] Attach an image, send "describe this". Confirm Felix describes the image
      (vision path), if a vision-capable model is active.
- [ ] Attach an arbitrary binary (e.g. a .zip). Confirm it is stored and
      referenced on the turn without erroring.

**Persistence**

- [ ] Reload the app and reopen the thread. Confirm the attachment chip still
      shows on the historical turn (bound to the turn, stored per-profile).

---

## S15 — Integrations tab: harness status (#298)

**Pane navigation**

- [ ] Click **Integrations** in the TOOLS section of the sidebar. Confirm the
      Integrations pane opens (no placeholder, no crash).

**HARNESS section — daemon row**

- [ ] With Cerebral running but OpenClaw not configured (no
      `~/.openclaw/openclaw.json` or `OPENCLAW_GATEWAY_TOKEN`), confirm the
      **OpenClaw** row shows a red dot and the label "down".
- [ ] With a valid OpenClaw token and the subscriber started, confirm the
      **OpenClaw** row shows a green dot and the label "running".

**Channel rows**

- [ ] Confirm all five channels are listed: WhatsApp, Telegram, Discord,
      Slack, Teams.
- [ ] When the daemon is down, confirm every channel row shows a red dot and
      the label "down".
- [ ] When the daemon is running (connected to the gateway), confirm every
      channel row shows a green dot and the label "connected".

**Live refresh**

- [ ] Navigate away to another pane, then return to Integrations. Confirm the
      status rows reflect the current daemon state (re-pull fires on every
      pane activation).

---

## S16 — Integrations: in-UI channel config + control (#299)

**Daemon control buttons**

- [ ] Open **Integrations** with the daemon down. Confirm **Start** is enabled
      and **Stop** / **Restart** are disabled (grayed out).
- [ ] Click **Start** (requires a valid OpenClaw token; the call is still safe
      to issue when none is configured -- it logs a warning and the dot stays
      red). When the daemon comes up the dot turns green, the label flips to
      "running", and **Start** disables while **Stop** / **Restart** become
      enabled.
- [ ] Click **Stop**. The dot turns red, the label flips to "down", and the
      button states invert again.
- [ ] Click **Start** to bring the daemon back up, then click **Restart**.
      Confirm the dot briefly indicates the down → running transition and ends
      green / "running".

**Per-channel enable/disable**

- [ ] For each of WhatsApp, Telegram, Discord, Slack, Teams: click the
      **Disabled** pill on its action row. The pill flips to green
      "**Enabled**".
- [ ] Fully close and relaunch the app. Reopen Integrations. Confirm the
      channels you enabled are still showing **Enabled** (state persisted to
      `cerebral/data/felix-harness.json`).
- [ ] Click an **Enabled** pill to flip it back to **Disabled**. Confirm the
      flip is immediate and persists across a reload.

**Write-only channel secrets**

- [ ] On a channel row, click **Set secret**. A password-masked input + Save /
      Cancel buttons appear in place of the button.
- [ ] Type a fake bot token (e.g. `not-a-real-token-123`). Confirm the input is
      MASKED (dots / asterisks, not the plaintext).
- [ ] Click **Save**. The editor collapses, the row pill flips from "no
      secret" to green "**secret set**", and the button label changes to
      **Replace secret**.
- [ ] Click **Replace secret**. Confirm the input field is EMPTY (the
      previously-saved token is NEVER echoed back into the DOM).
- [ ] Open DevTools (Ctrl+Shift+I) → Network / WS frames. Send a `Reload` and
      watch the `harness_status` event broadcast. Confirm the channel entries
      include only `name`, `state`, `enabled`, `secret_set` -- NO `secret`
      field, NO plaintext token in any frame.
- [ ] Click **Replace secret** then **Clear**. Confirm the pill flips back to
      "no secret" and the button reverts to **Set secret**.

**Cancel + empty save**

- [ ] Click **Set secret**, type a value, click **Cancel**. Confirm the form
      collapses and the row pill is unchanged (no IPC send).
- [ ] Click **Set secret**, leave the field empty, click **Save**. Confirm the
      form collapses with no state change (empty saves are ignored).
