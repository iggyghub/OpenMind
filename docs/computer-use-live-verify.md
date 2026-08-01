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
