# What does editing a UI actually need? -- research prompt

Paste below the line into a fresh session (or run it yourself). It is tool-agnostic
on purpose: the answer should hold for a web page, an Electron app, a Qt/GTK/WPF
desktop app, a mobile app or a game menu.

---

You are a senior product designer and a senior front-end engineer who have both
spent years on the loop "someone looks at a UI and wants it changed -> someone else
changes the code". Your job is to map what that loop **needs**, independent of any
one tool.

Work through these, in order. Be concrete, give a real example for every item.

1. **The pointing problem.** What can a person want to point at? Go beyond "one
   element": groups, every-element-like-this, the gap between two things, a region
   with no element (canvas, video, game), a state (hover, focus, disabled, empty,
   loading, error), a size (narrow window, zoom), a moment (an animation, a flow
   across several screens), something that is *missing*.

2. **The intent vocabulary.** List the distinct kinds of request (change style,
   change text, move/reorder, remove, add, behaves wrong, make consistent with X,
   accessibility...). For each: what extra information does the fixer need that a
   one-line note leaves out?

3. **The evidence.** What has to travel with a request so the fixer never has to
   ask "which one?" or "what were you looking at?": locator, screenshot, app state,
   data on screen, window size, version/commit, console errors, the steps that led
   there.

4. **From pixel to source.** How does a fixer get from the thing on screen to the
   line of code? Cover DOM apps (selectors, source maps, component names), native
   toolkits (accessibility trees: UI Automation, AX, AT-SPI; object names; .ui files),
   and pixel-only surfaces. What breaks the mapping (dynamic ids, re-renders,
   generated markup)?

5. **The loop after the fix.** How does the requester confirm the fix, reopen it,
   or see that a fix broke something else? What status lifecycle do requests need?

6. **Batching and scale.** What changes when there are 50 requests instead of 1:
   dedupe, grouping by root cause, priority, "this applies everywhere" rules.

7. **Input modes.** Typing, voice, sketching, direct manipulation (drag the padding,
   pick the colour) recorded as a *spec* instead of baked output. When is each best?

8. **Portability.** What would it take for one marking tool to attach to any
   open-source app, per UI technology? What is easy, hard, impossible? Name the
   real mechanisms (browser extension, Chrome DevTools Protocol, WebView2 remote
   debugging, UI Automation...).

Then:

9. **Gap analysis against the tool described below.** For each need above, mark
   it covered / partly / missing.

10. **Recommend.** The 5 additions with the best value-for-effort, in build order,
    each with a one-line "done looks like". Then the bigger bets, with honest risk.

Tool to analyse: <describe the current marking tool here>
