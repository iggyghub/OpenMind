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
