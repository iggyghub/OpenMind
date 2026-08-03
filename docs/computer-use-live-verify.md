# Computer-use capability -- live verification checklist

Items here require real Windows hardware (a running target app, real UIA, real
mouse/keyboard). They cannot run in the automated test suite -- the suite
uses fake backends per ADR-0016 SAFETY rule 2.

Run these manually on a Windows machine where the target apps are installed.

## S1 #574 -- plugin spine (window capture + UIA read + actuation + retry loop)

- [ ] Open Windows Calculator. Start Cerebral (`python -m cerebral.main`).
      Ask Felix: "read the UI of the Calculator window". Verify: `read_ui`
      returns a non-empty element list containing button names ("Two", "Plus",
      "Equals", "Three", etc.) with plausible `bbox` values inside the
      Calculator window bounds.
- [ ] Ask Felix: "click the Two button in Calculator, then click Plus, then
      Two, then Equals". Verify: the Calculator display advances through the
      keypresses and settles on `4`; the `click_element` tool trace shows
      `ok: true` for each call and `target.role == "Button"`.
- [ ] Ask Felix to click a button that does not exist ("click the
      Antimatter button in Calculator"). Verify: `click_element` returns
      `is_error: true` after `DEFAULT_RETRY_LIMIT` failed observations;
      the trace shows `observed: true, acted: false, actual: "not present"`
      on every try.
- [ ] Open Notepad, focus the editing area. Ask Felix: "type 'hello world'
      into Notepad". Verify: text appears in the Notepad window; the
      `type_into` trace shows `ok: true` and the pyautogui actuation
      landed on the editor bbox (not another window).
- [ ] Slam the mouse to a screen corner while a `type_into` sequence is in
      flight. Verify: pyautogui FAILSAFE aborts the run; Felix returns an
      error and no more keystrokes fire.
- [ ] Inspect the Conversation store after the demo: verify per-action
      tool_result turns exist with the structured trace (targeted element
      name, role, bbox, per-try verification). Verify NO PNG / JPG / raw
      frame bytes exist anywhere under `cerebral/data/` -- the audio-buffer
      rule (ADR-0016 sec 7) must hold.

## S2 #576 -- 3-part kill switch + window-bounded region

- [ ] (a) Corner-failsafe: with a `type_into` in flight against Notepad,
      slam the mouse to a screen corner. Verify: pyautogui.FailSafeException
      fires; the plugin returns is_error with a trace entry containing
      "corner-failsafe abort"; no further keystrokes land after the abort.
- [ ] (b) F11+F12 chord: with a long-running `type_into` in flight, press
      F11 and F12 together. Verify: the tool trace's final try records
      "aborted by kill switch"; typing halts within one action of the press;
      no zombie keystrokes reach the target window after the abort.
- [ ] (c) Visualiser "Felix is driving" state + Stop: enable the Visualiser
      (tray menu). Trigger any `click_element` / `type_into`. Verify: the
      "Felix is driving" badge appears over the orb the moment the tool
      starts and disappears the moment it ends; the STOP button is
      clickable (window is not click-through while driving); clicking STOP
      halts the tool and the trace's final try records "aborted by kill
      switch". Verify the Visualiser reverts to click-through after the
      tool ends.
- [ ] Window-bounded region: open Calculator, then ask Felix to click a
      known element while Calculator's title changes / the window is moved
      such that the previously-observed bbox no longer sits inside the
      window's outer rect. Verify: the click is refused with
      "outside window bounds -- refused" in the trace and NO cursor moves.
- [ ] Loop yields: while a long retry sequence runs, verify the mouse is
      still usable (drag a window, click another app) -- input is NOT 100%
      hijacked because the plugin yields to the event loop between tries.

## S4 #575 -- multimodal Backend seam + computer_use_vision routing

- [ ] Pull a local VL model (`ollama pull qwen2.5vl`) and add its backend
      to the router with `supports_vision=True`. Capture a real Calculator
      window screenshot (mss / `Windows.Graphics.Capture`) and pass its PNG
      bytes to `router.complete_with_images("Where is the Equals button? "
      "Reply with pixel coordinates.", [png_bytes])`. Verify: response
      contains coordinates that plausibly land inside the Equals button's
      bbox; `router.last_model` == the VL Ollama backend id.
- [ ] With Budd (custom OpenAI-compat) configured as a VL tier
      (`supports_vision=True`) and priority ordered `local_vl` -> `budd` ->
      `claude/sonnet`: temporarily stop the local Ollama process, then re-
      run the grounding call. Verify: request falls through to Budd, Budd
      returns coordinates, `last_model` == the Budd id.
- [ ] Enable local-only mode with NO local VL model installed. Verify:
      `router.complete_with_images(...)` raises `ModelUnavailableError` with
      "no vision-capable model" -- cloud is not called, no PNG bytes leave
      the box. (Caller then escalates to attended-handoff per ADR-0016
      sec 5; S6 will wire that path.)
- [ ] With `ANTHROPIC_API_KEY` set and priority chain ending in
      `claude/sonnet`, call `router.complete_with_images(...)` with a real
      screenshot. Verify at the network level (Fiddler / Wireshark) that
      exactly ONE image block is sent, the media_type is `image/png`, and
      the response text names a UI element visible in the frame.

## S5 #578 -- pixel-vision fallback + RAM thumbnail buffer + DRM-black escalation

- [ ] Open MS Paint on a fresh canvas. Configure Budd (or any VL Ollama
      model) at the top of the model priority. Ask Felix: "click the
      Brush tool in Paint". Verify: UIA exhausts (Paint's ribbon is
      partially UIA-blank on some tool palettes), the plugin falls
      through to pixel-vision, the trace's final try has
      `path: "pixel"` and `ok: true`, and the Brush tool becomes the
      active selection. Verify NO PNG / raw frame bytes exist under
      `cerebral/data/` -- the ring stays in RAM.
- [ ] Repeat with a target Felix should be able to find in the UIA tree
      (e.g. "click the File menu"). Verify: it succeeds on the
      structured path -- final try has no `path` key or `path: "uia"` --
      and `capture_frame` is never invoked (no pixel round-trip on the
      happy structured case).
- [ ] Open Netflix (or any DRM-protected video player) and start a
      video. Ask Felix: "click the pause button". Verify: `capture_frame`
      returns a black raster; the trace's final try records
      `escalated: true` with the "black/protected capture" message; NO
      cursor moves; the plugin surfaces `is_error: true` for the caller
      to escalate (S6 will wire attended-handoff).
- [ ] Ask Felix a target that requires pixel fallback. Immediately after
      it succeeds, call `plugin.thumbnail_ring_snapshot()` from a debug
      REPL. Verify: exactly one non-empty `bytes` entry; run 8+ more
      fallback tasks and verify the ring caps at
      `DEFAULT_THUMBNAIL_RING_SIZE` (oldest rolls off). Restart Cerebral;
      verify the ring starts empty (audio-buffer rule -- RAM only, never
      persisted).
- [ ] With Budd stopped and no local VL model installed, force the
      pixel fallback (Paint brush task). Verify: the grounding seam
      returns None (router raises `ModelUnavailableError` -> seam logs
      and returns None); the trace records a fallback try with
      `ok: false` and no coord; no click fires. Caller escalates.

## S6 #579 -- attended-handoff on retry exhaustion / DRM-black

- [ ] Open Calculator and ask Felix to click a button that does not exist
      (e.g. "click the Antimatter button in Calculator"). Verify: after
      `DEFAULT_RETRY_LIMIT` structured tries + a pixel fallback attempt,
      Cerebral emits a `computer_use:handoff_needed` broadcast, an OS
      notification titled "Felix needs you to take over" fires, and the
      Calculator window is surfaced (SetActive brings it forward). Verify
      the plugin trace's final try has `path: "handoff"` and awaits a
      reply. Send `{"type":"computer_use_handoff_done","data":{"handoff_id":"h1","completed":true}}`
      via the tray IPC (or a dev harness). Verify: the tool call returns
      `is_error: false`, the final try records `handoff completed by human`,
      and the transcript shows Felix continuing.
- [ ] Repeat the flow but respond with `completed: false`. Verify: the
      tool call returns `is_error: true`, the final try records
      `handoff declined by human`, and Felix does not continue.
- [ ] Open Netflix (or any DRM-protected video) and ask Felix to click a
      control (e.g. "click Pause"). Verify: `capture_frame` returns a
      black raster, the pixel path records an `escalated: true` try, and
      the plugin then invokes the handoff seam (notification + broadcast).
      Complete the step manually and reply `computer_use_handoff_done` +
      `completed: true`. Verify: Felix continues.
- [ ] Ask Felix to interact with an app that exposes no UIA tree at all
      (e.g. a custom-drawn canvas window). Verify: read_ui returns an
      empty list every try -> retries exhaust -> attended-handoff fires
      even without an explicit "element not found" record.
- [ ] While a handoff is pending (Felix is awaiting the human), click the
      Visualiser's Stop control. Verify: the pending handoff resolves as
      declined (`is_error: true`, "handoff declined by human"), the
      driving indicator flips off, and no further tool calls run.
- [ ] Confirm the handoff carries NO frame bytes: dump the broadcast
      payload for `computer_use:handoff_needed`. Verify it contains only
      `handoff_id`, `window_title`, `reason` -- no image data (ADR-0016
      sec 7 audio-buffer rule extends across the seam).

## S3 #577 -- ADR-0005 gate integration + full-autonomy switch

- [ ] Start Cerebral with a tray connected. Ask Felix to read Calculator's UI
      (`read_ui`) then click a benign button (`click_element` name="Two").
      Verify: `screen_capture` prompts a consent card **once** (choose Session);
      subsequent captures this session do not re-prompt. `device_control`
      (the click) is SILENT -- no prompt.
- [ ] Ask Felix to click a COMMITTING control -- open any app with a Send /
      Submit / Delete button and ask "click the Send button". Verify: the
      **irreversible modal** pops (not a consent card), Accept lets the click
      fire, Cancel blocks it. Confirm a benign click ("click Cancel",
      "click Two") never pops the modal.
- [ ] Open Permissions -> Capabilities. Verify the **Computer-use full
      autonomy** switch renders at the top, default OFF, with the amber warning
      text and no ON badge.
- [ ] Flip full autonomy ON. Verify: the row shows the permanent **ON** badge +
      amber highlight. Now repeat the committing click ("click Send"): verify it
      fires with **no modal**. Confirm a DIFFERENT plugin's irreversible action
      (e.g. `gmail_send`) STILL pops its modal -- the switch is scoped to
      computer use only.
- [ ] With full autonomy ON, restart Cerebral. Verify it comes back OFF (badge
      gone) -- RAM-only, never persisted. Repeat: turn it on, switch profile,
      verify it resets OFF for the new profile.

## S7 #580 -- browser-as-app stealth path + planner selection

Needs a running Windows host with a real browser (Chrome / Edge / Firefox)
plus real network egress. All steps use `computer_use.browser_navigate`.

- [ ] Open Chrome / Edge to a blank tab. Ask Felix: "Open discord.com/app".
      Verify: `browser_navigate` runs, the plugin trace's `target` is the
      address bar (name contains "Address and search bar"), the URL types
      into it, Enter fires, the resulting page loads discord.com, and the
      trace's `elements` field lists the post-navigate UIA tree. Confirm the
      request path is stealth: page-side JS console reports
      `navigator.webdriver === false` and no CDP client id in the browser's
      internals -- the OS-input path leaves no automation fingerprint.
- [ ] Log in to Discord in the SAME window (once, by hand). Now ask Felix
      to navigate to a specific channel URL and read the latest visible
      message name. Verify: `browser_navigate` submits the URL, and the
      subsequent tool call (e.g. `read_ui`) sees the logged-in surface with
      the channel visible. The logged-in cookie / session survived because
      Felix drove the user's own browser, not a fresh Playwright context.
- [ ] Repeat with Firefox to confirm the address-bar candidate list picks
      up "Search or enter address" (Firefox's default name). If a locale
      renders a different name, pass `address_bar` explicitly and verify
      the override wins.
- [ ] Coexistence check: ask Felix to read the anonymous job board (a
      benign public URL) and verify the planner picks the Browser plugin's
      `navigate` (Playwright DOM, no visible window), NOT
      `browser_navigate`. Same session, immediately after, ask for a
      Discord action and verify it flips back to `browser_navigate`.
- [ ] Kill-switch check on the stealth path: mid-navigate (during the URL
      type), slam the mouse to a screen corner. Verify: the plugin trace
      records `corner-failsafe abort` and no Enter fires.
- [ ] Handoff check on the stealth path: point Felix at a browser window
      with the address bar removed (kiosk mode / full-screen video).
      Verify: `browser_navigate` exhausts its retries with "address bar
      not found", then the attended-handoff seam fires (notification +
      SetActive), and a `computer_use_handoff_done` reply resumes the
      chain.

## Background actuation (ADR-0016 amendment 2026-08-02) -- S4 #595

Verifies decisions (a)/(b)/(c)/(d)/(e)/(f)/(g) of the amendment: background
UIA-pattern actuation runs concurrently with the user, `SetValue` never
submits, the idle gate defers to a present user, the kill switch covers both
actuation paths, and the driving indicator is mode-aware. All checks assume
`computer_use.background_actuation` is at its default (on) unless a step says
otherwise.

- [ ] Concurrent use: open Notepad and click into its editing area so the
      caret is blinking there. Open Calculator as a SECOND, background
      window. While continuously typing in Notepad yourself, ask Felix:
      "click the Two button in Calculator". Verify: the caret never leaves
      Notepad, the cursor never moves, focus never jumps away from Notepad
      (you can keep typing the whole time and your keystrokes land in
      Notepad, not Calculator), and the `click_element` trace's successful
      try shows `"path": "uia_pattern"` and `"foregrounded": false`.
- [ ] Focus-theft per control class -- button `Invoke`: ask Felix to click a
      plain button with no side dialog (e.g. Calculator's "Two"). Verify the
      successful try records `"path": "uia_pattern"`, `"foregrounded": false`.
- [ ] Focus-theft per control class -- checkbox `Toggle`: point Felix at a
      background window containing a checkbox (e.g. a Settings toggle, or
      Notepad's Format menu "Word Wrap" if exposed as a checkbox) and ask
      Felix to toggle it while another window stays focused. Verify: the
      checkbox state actually flips, the successful try records
      `"path": "uia_pattern"`, `"foregrounded": false`.
- [ ] Focus-theft per control class -- background `Edit` `SetValue`: with a
      plain, non-browser `Edit` control in a background window (e.g.
      Notepad's Find dialog text field), ask Felix to `type_into` it. Verify
      the successful try records `"path": "uia_pattern"`, `"foregrounded":
      false`, and no `Edit`/`Document` window ever became foreground.
- [ ] Focus-theft actually detected: pick (or contrive) a control that opens
      a modal / steals focus the instant it's invoked via its control
      pattern. Ask Felix to click it. Verify: the try records
      `"foregrounded": true`, `"ok": false`, `"actual"` containing "soft
      trip", and the call returns immediately WITHOUT falling through to the
      pyautogui foreground fallback (no cursor moves after the soft trip).
- [ ] `SetValue` fills, never submits: pick a native (non-browser,
      non-Electron) `Edit`/`Document` control with autocomplete-like
      behavior -- e.g. Windows Explorer's search box, or Notepad's Find
      dialog field -- and ask Felix to `type_into` it. Verify: no
      navigation, search, or dialog action fires as a side effect of the
      fill; the successful try shows `"path": "uia_pattern"`. Then, as a
      SEPARATE step, ask Felix to `click_element` the Find Next / Search
      button. Verify: the two actions are distinct trace entries (`type_into`
      then `click_element`) -- the fill never auto-submitted -- and if the
      submit button's name matches an `is_committing_action` verb (e.g.
      "Delete", "Send"), the irreversible modal pops on that second call and
      not on the `type_into` call. NOTE: the plugin exposes no standalone
      `press_key` tool -- the only Felix-callable submit step today is a
      second `click_element` call (see Mismatch note below).
- [ ] Kill-switch remap -- F11+F12 aborts a background action: ask Felix to
      run a `click_element`/`type_into` against a background window under
      conditions that keep the retry loop in flight for a few tries (e.g. a
      name that briefly doesn't resolve). Mid-loop, press F11+F12 together.
      Verify: the trace's final try records `"actual": "aborted by kill
      switch"`, `"ok": false` (this early-abort entry carries no `path` key
      -- the abort fires before a path is chosen), and no further UIA
      pattern calls or keystrokes occur after the press.
- [ ] Kill-switch remap -- Visualiser Stop aborts a background action: same
      setup as above, click the Visualiser's STOP button instead of the
      hotkey. Verify the same trace shape: final try `"actual": "aborted by
      kill switch"`, `"ok": false`.
- [ ] Kill-switch remap -- corner-slam is foreground-only (documented, not a
      defect): with background actuation succeeding via `uia_pattern` (no
      cursor movement), slam the mouse to a screen corner mid-loop. Verify:
      the action completes normally -- `CornerAbort` is only ever raised
      inside the backend's `click()`/`type_text()` calls (the foreground
      path), never inside `pattern_click()`/`pattern_set_value()`, so a
      background action has no cursor to slam and the corner-failsafe simply
      does not apply to it. This is intentional: corner-slam covers the
      foreground leg (verified in S2 #576); F11+F12 and Visualiser Stop are
      the two legs that cover BOTH paths, because the `_abort_event` check at
      the top of every retry loop runs before the background/foreground
      branch is chosen.
- [ ] Idle gate: force the foreground fallback (either set
      `computer_use.background_actuation` to off, or target a control with
      no usable pattern) and actively move the mouse / type on the keyboard
      yourself, staying within `computer_use.user_idle_ms` (default 4000ms)
      of continuous activity. Ask Felix to click/type into that control.
      Verify: while you stay active, each try is recorded as waiting --
      `"actual"` contains "user present (idle Nms < 4000ms) -- waiting for
      idle instead of stealing input", `"ok": false`, `"path": "uia_
      synthetic"` -- and the cursor never moves. Then stop touching input
      for >= 4000ms and verify the very next try proceeds with the
      foreground click/type. If you never go idle, verify the retry loop
      exhausts and escalates to attended-handoff (S6 #579) rather than ever
      stealing input from a present user.
- [ ] Idle gate bypassed under full autonomy: flip the full-autonomy switch
      ON (S3 #577). Repeat the previous check while continuously active.
      Verify: the foreground fallback proceeds immediately despite you being
      "present" -- full autonomy bypasses the idle gate the same way it
      bypasses the irreversible floor.
- [ ] Mode-aware indicator -- background: enable the Visualiser. Ask Felix
      to click/type into a background-actuatable control. Verify: the
      driving panel shows the calm blue `mode-background` style with the
      text "Felix is acting in `<window title>` (background) -- you can
      keep working" for as long as the pattern actuation is running.
- [ ] Mode-aware indicator -- soft trip flips to foreground/urgent: force a
      focus-theft soft trip (per the check above) or target a control with
      no usable pattern (forcing the foreground fallback). Verify: the
      indicator flips in real time to the original urgent purple/pink style
      with the text "Felix is driving" -- the same broadcast, no separate
      gate, purely a mode change.
- [ ] Mode-aware indicator -- `browser_navigate` is always foreground: ask
      Felix to `browser_navigate`. Verify: the indicator NEVER shows the
      background style for this tool, even with `background_actuation` on --
      `browser_navigate` has no control-pattern equivalent and always emits
      `mode="foreground"`.

**Doc-vs-code mismatch found while writing this section:** the issue's
suggested "submit is a separate gated `click_element`/`press_key` step" does
not fully match the merged code. `plugins/computer_use.py` exposes exactly
four tools -- `read_ui`, `click_element`, `browser_navigate`, `type_into` --
and no standalone `press_key` tool the planner can call. `press_key` exists
only as an internal `ComputerUseBackend` method used by `browser_navigate`'s
own URL-submit (Enter after typing the address), which is not routed through
`is_committing_action` at all (that gate only fires for `tool_name ==
"click_element"`, per its docstring and implementation at line ~138-153).
The check above is written against `click_element` as the submit step, which
is what the gate actually covers -- it does not assert a `press_key` tool or
gate that does not exist.
