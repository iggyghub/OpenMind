# ADR-0016: Computer use — Felix sees the screen and drives mouse/keyboard

**Date:** 2026-07-31
**Status:** Accepted

## Context

Felix's reach ends where an API, plugin, or channel ends. Apps with no
programmatic surface — a native Win32 tool, a game, a canvas, an Electron app
that exposes nothing — are simply unreachable. The motivating case is Discord:
the only automated paths today are the bot-API (ADR-0006 path 1, deferred) and
the **self-bot token** (ADR-0006 path 2), and the self-bot's fatal flaw is
*detection* — Discord actively fingerprints automated clients and a hit is a
permanent account ban. The user's framing: let Felix operate Discord (and
anything else) **the way a person does — by sight, with its own mouse and
keyboard** — so there is no automation token to detect.

This is the same shape as ADR-0010: take an existing, highest-blast capability
class and build the execution mechanics beneath it. ADR-0005's 16-class
vocabulary **already contains the two classes this needs** — `screen_capture`
(default *ask*) and `device_control` (default *silent*) — so, like ADR-0010,
**this ADR adds no vocabulary; it is execution mechanics on existing classes.**

But the ADR-0010 template breaks in one decisive place. `shell_exec` could be
*contained*: a child process wrapped in an AppContainer + Job Object physically
cannot touch anything outside its workdir. **A real mouse and keyboard driving
the live desktop cannot be contained that way.** There is no kernel jail for the
physical cursor — every click lands on the user's actual logged-in session with
all its open apps, sessions, and secrets. And `device_control` is *silent*-by-
default, which was harmless when nothing drove it and is not harmless once an
LLM does. The containment story is therefore the spine of this decision, and it
is necessarily *softer* than ADR-0010's kernel boundary.

The threat is ADR-0005 threat #1 (prompt injection → tool misuse) at its
sharpest: a poisoned page steers the planner into computer-use, and with no
kernel boundary the only thing between a confused loop and a destructive click
is the gate, the bounds, and a human's hand on a kill switch.

## Decision

**Computer use is a single `computer_use` plugin declaring `screen_capture` +
`device_control`. Windows-only in v1; fail-closed on every other platform**
(the `shell_exec`/ADR-0010 posture). The 16-class vocabulary and both
cross-cutting flags (`passive`, `irreversible`) are **unchanged**.

### 1. Hybrid modality — structured-first, pixel-fallback

Felix reaches first for a **structured handle** — the OS accessibility tree
(Windows UI Automation), acting on *named elements* read as text by the local
text model (fast, reliable, private). It falls back to **raw pixel vision +
coordinate clicks** only where the tree is absent or too thin (games, canvases,
tree-less apps). Structured is the default because it is orders of magnitude
faster and more reliable at targeting; pixel is the general-but-slow escape
hatch that guarantees "Felix can operate *anything*."

Rejected: raw pixel vision as the primary mode. It is the most *general* path
but the slowest and least reliable per action, worst of all on local hardware
(a GTX 1080 grounds GUI pixels poorly). Generality is preserved by keeping it as
the fallback, not the headline.

### 2. The browser is just another app

Computer use drives a **normal, user-launched browser window** via OS-level
input + its UIA tree — deliberately **not** through Playwright/CDP. CDP carries
automation fingerprints (`navigator.webdriver`, timing) that serious bot-
detection (Cloudflare, Discord) reads; OS-level input on a real browser looks
human. This is the **stealth-sensitive** path. The **Browser** plugin
(Playwright DOM) **coexists** as the *fast* path for benign/anonymous use (the
logged-out Job board, public reads/extraction). The planner picks per site:
speed when detection doesn't matter, stealth when it does — the same "two paths
coexist" philosophy CONTEXT.md already applies to the two Discord integrations.

Rejected: reuse Playwright inside computer_use for the web-structured path. It
is faster and more reliable at DOM targeting, but its CDP fingerprint defeats
the entire detection-avoidance reason the capability exists. Kept as a separate
tool instead.

### 3. Consequence-level gating, not per-action

A computer-use task is dozens–hundreds of primitive ops; gating each is
unusable. Consent fires at two levels:

- **`screen_capture`: keep *ask*, session-grantable** — capturing the screen is
  the real privacy event, so it earns one deliberate yes per session.
- **`device_control` primitives (move/click/type/scroll): stay *silent*** — a
  mouse-move harms nothing; gating it is pointless friction.
- **The *consequence* is gated at the planner** — an irreversible act
  (send/submit/delete/pay) carries `irreversible=True` and pops the ADR-0005
  modal, exactly like `gmail_send`.

Every knob rides ADR-0005's existing machinery (class policy toggles + per-tool
overrides), so the user turns restrictions on/off as they do for the rest.

**The semantic-opacity gap.** The consequence-gate can only read intent on the
**structured** path — clicking element "Send" is legible; a raw pixel click at
`(842, 391)` is not (the gate can't tell "Send" from "Cancel"). So on the
**pixel-fallback path**, the committing click carries a **per-tool override**
(default *ask*), which the user may flip to *silent* for full-auto. This creates
healthy pressure to stay on the structured path.

### 4. A badged, default-off full-autonomy switch — the one ADR-0005 exception

ADR-0005 makes `irreversible` **non-bypassable** even by persistent grants — the
one hard floor. Computer use carves a **deliberate, documented exception**: a
single **full-autonomy master switch**, **default off**, that when on removes
even the irreversible floor for computer-use actions. While on it wears a
**permanent visible indicator** (the `shell_exec`-opt-in / trusted-plugin-red-
badge pattern). This honors the user's "I run full-auto by choice" without
making "an LLM clicks *send* blind on the real desktop with zero friction" the
*silent default*. The escape hatch is real; it is deliberate and visible, never
accidental.

Rejected: keep the irreversible floor absolute. It is the safer default but
denies a power user the off-switch they have on every other class; the badged,
default-off form preserves the floor *by default* while leaving the switch
reachable.

### 5. Grounding routes through the model-priority chain (multimodal seam)

Pixel-vision grounding runs on the existing **model-priority chain** (local →
custom server → cloud) honoring `local_only`, and falls to the **first backend
that has a vision-capable model**. In practice that is the user's primary custom
endpoint (**Budd**, a `ClawBackend`), so the heavy looking happens there, not on
the 1080; local-only mode uses a local VL model if pulled, else escalates to
attended-handoff; cloud is last.

This requires a **multimodal seam**: the `Backend` protocol (`router.py`) is
text-only today (`complete(prompt, task_type)`), so it gains **image input**,
implemented for `OllamaBackend` / `ClawBackend` / `AnthropicBackend`. Grounding
is a router `task_type` (`computer_use_vision`), selectable like the self-dev
loop's model. Setup prerequisite (written down): a tier only grounds if it hosts
a VL model.

### 6. Containment substitute — kill switch, window bounds, retry limit

No kernel jail, so containment is three softer bounds:

- **Three-part kill switch (non-negotiable):** (a) `pyautogui` corner-failsafe
  (slam the mouse to a screen corner → hard abort, works mid-action); (b) a
  global panic hotkey — **F11+F12** (chord); (c) a visible "Felix is driving" state on the Visualiser
  with a Stop control. The loop **yields between actions** so input is never
  100% hijacked.
- **Window-bounded region by default:** actions are refused outside the target
  app's window (soft check — defeatable on a mis-grounded coordinate, but it
  turns "the whole screen" into "one app"). Widenable/off under full-autonomy.
- **Retry limit, not a step/time budget:** a *try* is one attempt at a sub-goal,
  verified against the next observation; a success advances and costs nothing,
  only a **failed** try counts. After **N consecutive failed tries on a sub-goal
  (default 3–5, configurable), Felix stops and escalates** (attended-handoff or
  notify — the ADR-0006 2026-07-02 pattern). This bounds the real danger (blind
  thrashing / mis-grounded retry loops) instead of capping honest progress. A
  per-*action* timeout is retained purely as a hang-guard (a UIA/screenshot call
  that never returns), not a work budget.

**Chain relationship:** computer_use is **one tool from the Chain's view**
(ADR-0008) — "accomplish X via computer use" is one chain step; the perception-
action loop lives *inside* the tool with its own retry limit + kill switch, so
the Chain's 8-step cap is never the bound. Same shape as `shell_exec` being one
tool that does a lot internally.

Rejected: driving an **isolated Windows session / virtual desktop** so the
user's desktop stays untouched. It is the stronger containment but a heavier
build and it defeats "watch it work / take over." Deferred post-v1; the
capture/actuation seam does not preclude it.

### 7. Capture scope + persistence — the audio-buffer rule for the screen

- **Window-scoped capture by default** (the `Windows.Graphics.Capture` per-
  window API, the same "choose which app" Discord's screen-share uses). Felix
  sees only the target app's window — the password manager, banking tab, and
  DMs in *other* windows never enter a frame or reach the grounding model.
  Capture region == action region == the target window. **Full-desktop capture
  is the heavier exception** (finding/switching apps), gated more strictly than
  the routine window grab.
- **Raw frames are never persisted** — held in RAM only long enough to ground,
  then discarded. This is the **rolling-audio-buffer rule** applied to the
  screen: CONTEXT.md already keeps raw ambient audio unwritten and persists only
  the post-Whisper *text*; the screen is treated identically.
- **Only the structured action trace persists** to the Conversation store as
  `tool_call`/`tool_result` turns: the targeted element (name, role, bounding
  box), the action, and **expected-vs-actual verification** ("targeted 'Send';
  post-action tree still showed the composer → failed try 2/3"). Fully
  diagnosable for a mis-click *without a screenshot*.
- **In-session debugging without disk:** an ephemeral live view while driving +
  an optional **RAM-only thumbnail ring buffer** of recent frames (capped,
  cleared on restart, never written). A deliberate user command may save one
  frame for a bug report; automatic persistence never happens.
- **Transmission ≠ persistence:** whether a frame may leave the machine for
  *cloud* grounding is governed by `local_only` (decision 5); persistence to
  disk is simply *never*, regardless.

Known gap: **DRM/GPU-protected windows capture black** (Netflix, some anti-cheat
games, protected video). There the pixel path is blind; Felix falls to UIA or
escalates.

### 8. Build sequence — safe native first, Discord as the destination

The first target is where the framework is debugged, so it is **not** the ban-
risky, hardest-UIA case. Sequence:

1. **Safe native app with a clean UIA tree** (Notepad / File Explorer /
   Calculator / Settings) — proves capture → UIA read → element click/type →
   kill switch → verification loop, with **zero ban risk and nothing
   irreversible**.
2. **Safe pixel-fallback target** (Paint / a game menu) — proves grounding-on-
   Budd + coordinate actuation without consequence.
3. **Discord** — the motivating real target (desktop Electron exercises the
   pixel fallback; Discord-web exercises browser-as-app), reached only once the
   machinery is trustworthy.

## Considered and rejected

- **Raw pixel vision as the primary modality.** Most general, slowest + least
  reliable per action, worst on local hardware. Kept as the fallback.
- **Playwright/CDP for the web-structured path.** Faster DOM targeting, but the
  CDP fingerprint defeats the detection-avoidance purpose. Browser plugin keeps
  this role for benign/anonymous use only.
- **An absolute irreversible floor.** Safer default, but denies the reachable
  off-switch the user has on every other class. Replaced by the badged,
  default-off full-autonomy switch.
- **A new capability class for "computer use."** Unnecessary — `screen_capture`
  + `device_control` already exist; this is execution mechanics on them.
- **Step/wall-clock work budget.** Caps honest progress; the retry limit bounds
  the actual danger (thrashing) instead.
- **Isolated virtual-desktop session in v1.** Stronger containment, heavier
  build, defeats watch-and-take-over. Deferred post-v1.
- **Persisting screenshots for later review/training.** The screen is the most
  sensitive data in the system; the audio-buffer rule (raw signal never
  written) applies. The structured trace recovers most postmortem value.
- **Full-screen capture by default.** Sweeps up every other window's secrets;
  window-scoped capture is both more private and unifies capture with the action
  bound.

## Consequences

- `pyautogui` (actuation) and a Windows UIA library (`uiautomation`) become
  runtime dependencies on Windows; `mss` (present) handles capture. A host
  without them fails closed for computer use — ADR-0005's fail-closed stance.
- The `Backend` protocol gains **image input** (first multimodal use); text-only
  backends are unaffected, and a tier without a VL model simply doesn't ground.
- ADR-0005's non-bypassable-`irreversible` rule gains **one documented
  exception** — the badged, default-off full-autonomy switch, scoped to
  computer-use actions. This is the deliberate trade recorded so a future reader
  knows it was chosen, not leaked.
- The 16-class vocabulary and both cross-cutting flags are **unchanged**. Like
  ADR-0010, this is execution mechanics beneath existing classes, layered on
  ADR-0005 — but, unlike ADR-0010, **without a kernel containment boundary**;
  the gate + window bounds + retry limit + kill switch are the substitute, and
  that softer boundary is the acknowledged cost of driving the real desktop.
- The **Browser** plugin (Playwright) and **computer_use** (OS-level) coexist
  permanently; neither replaces the other.
- Non-Windows hosts gain nothing yet: computer use stays denied there until a
  platform backend (macOS AX / Linux AT-SPI) lands — intended fail-closed
  behaviour, not a regression.
- Felix drives the user's **live, contended desktop** in v1 (the isolated-
  session option is deferred), which is precisely why the kill switch and the
  visible "Felix is driving" state are non-negotiable rather than nice-to-have.

## Amendment 2026-08-02: Background (concurrent) actuation

### Context

S1 (#574) live-verify surfaced a usability floor: `_WindowsBackend` actuates
through `pyautogui`, so every `click_element` / `type_into` / `browser_navigate`
**steals the physical cursor and keyboard** — the user cannot use the PC while
Felix drives. `read_ui` is already pure UIA (no cursor) and safe to run during
use; only the actuation methods hijack input. The original ADR treated
"structured vs pixel" as one modality spectrum, which hid the fact that
*perception* and *actuation* are **independent axes**.

### Decision

**Two orthogonal axes.** Perception: `structured` (UIA tree) vs `pixel` (vision
grounding). Actuation: **background** (UIA control patterns — `Invoke`,
`SetValue`, `Toggle`, `Select`, `ExpandCollapse` — no cursor, no keystrokes) vs
**foreground** (`pyautogui` synthetic input — steals devices). Control-pattern
actuation exists **only on the structured axis** (a pixel coordinate has no
control handle); `pixel + background` is an impossible cell. Background is
therefore a *property the structured path can have*, not a third sibling of
structured/pixel.

**a. Tiered actuation, background-first.** On the structured path, try the
control pattern first (background, concurrent). Fall back to `pyautogui`
(foreground) only when the control exposes no usable pattern. Pixel is always
foreground.

**b. Re-resolve, never carry a durable handle.** The observe→act→verify loop
already re-reads the UIA tree and re-matches by name/role every try, so
actuation re-resolves the **live** `AutomationElement` just-in-time
(`_resolve_element`) and uses that same element for both the pattern attempt and
its foreground-bbox fallback. `read_ui`'s public contract stays
`{name, role, bbox}` text — no COM objects leak to the model, and no COM pointer
is held across an `await`. Re-resolution reliability equals today's bbox
targeting reliability (same first-match `_find_element`); a non-unique match is
flagged in the trace rather than silently guessed.

**c. `SetValue` fills, never submits.** `SetValue` sets text atomically and
bypasses per-keystroke handlers, so search-as-you-type, input masks, JS
`keydown`, and Enter/Tab/`@` semantics never fire. It is therefore used **only**
to populate a plain field — a non-readonly `Edit`/`Document` control **outside**
any browser/Electron (webview) surface — and **never** to trigger or submit; the
submit is always a discrete `click_element`/`press_key` step (which keeps it in
front of the `is_committing_action` gate). Reactivity is not detectable from the
tree, so the rule is by **control class + surface**, not by classification, and
any doubt falls to foreground typing. Two `felix-settings.json` knobs:
`computer_use.background_actuation` (master, default on; off = today's pure
foreground) and `computer_use.setvalue_roles` (allowlist, default
`["Edit","Document"]`; empty it to keep pattern clicks background while forcing
all text through foreground typing).

**d. "What I'm doing takes priority" — idle-gated foreground fallback.**
Background patterns run concurrently, always. The **foreground** fallback is the
input-thief, so before any `pyautogui` action Felix checks `GetLastInputInfo`:
if the user touched input within `computer_use.user_idle_ms` (default 3–5s) the
user is *present* and Felix does **not** grab input — it waits for idle or
escalates to attended-handoff (S6). When the user is idle/away, or under
full-autonomy, foreground proceeds as today. One syscall distinguishes
present-vs-away and does the right thing in both states; background pattern
actuation emits no input events, so the idle signal reflects only the user.
Additionally, a pattern action that *unexpectedly* foregrounds the target
(measured via before/after `GetForegroundWindow`) is a **soft trip**: stamp the
trace, stop, and do **not** escalate to the input-stealing foreground fallback.

**e. Kill switch is two-tier.** Corner-slam is a `pyautogui`-cursor mechanism
and is **meaningless for a cursor-less background action** — it guards the
**foreground** path only, and that is not a regression (a background action has
no cursor to escape). **F11+F12** and **Visualiser Stop** are the always-
available legs covering both paths (the `_abort_event` check between tries is
already path-independent). Two legs suffice for background because it is
inherently lower-stakes (no input theft to physically flee) and idle-gated.

**f. Safer-because-invisible: surface harder, don't gate harder.** The
`irreversible` gate is already **path-independent by construction** —
`is_committing_action` reads the planner's *intent* (target element name), not
the actuation path, so a background `Invoke()` on "Send" hits the ADR-0005 modal
exactly like a foreground click. That property is now load-bearing and recorded.
"Surface harder" lives in the **indicator**, not a second gate: a background
action has no moving cursor, so the "Felix is driving" state is its *only*
feedback and carries `mode: "background"|"foreground"` + target window + current
action (background reads *"Felix is acting in Notepad (background) — you can keep
working"*). No stricter gate on non-irreversible background actions (that is the
friction full-auto exists to avoid); the fix for "easy to miss" is visibility,
not another consent wall.

**g. Trace records both axes.** Per-try:
`path: "uia_pattern" | "uia_synthetic" | "pixel"` (keeps S5's `"pixel"`;
today's structured-foreground click becomes `"uia_synthetic"`) **plus**
`foregrounded: bool`. Live-verify asserts background-vs-foreground and
focus-theft per action, mirroring S5's `path` field.

### Considered and rejected

- **Carry a durable `AutomationElement` reference** across tries. A COM pointer
  held across `await` marshals across the loop's thread boundary and goes stale
  on any redraw — a new failure mode. Re-resolution rides the loop we already
  pay for.
- **Classify field reactivity to decide `SetValue` vs typing.** UIA exposes no
  "has keydown handler" property; classification would be a guess. Gate by
  control class + surface, fall to typing on doubt.
- **Foreground fallback always fires (no idle gate).** Less code, but steals
  input mid-keystroke on every no-pattern control — contradicts the entire
  concurrent-use purpose. The idle gate is one syscall and is correct in both
  present and away states.
- **A single-key background panic key.** F11+F12 + Visualiser Stop suffice;
  background actions are idle-gated and non-input-stealing, so the panic urgency
  is genuinely lower than for the cursor-thief foreground path.
- **A stricter consent tier for background actions.** "Invisible" is a
  perception gap; the mode-aware always-on indicator closes it. A second gate
  would tax full-auto for no safety the modal doesn't already provide.

### Consequences

- Actuation gains a background tier on the structured path; `read_ui`'s text
  contract is unchanged. `GetLastInputInfo` + `GetForegroundWindow` (ctypes /
  `win32gui` one-liners; already in the Win32 surface `uiautomation` pulls in)
  become the idle gate + focus-theft probe.
- The isolated-session/VM option (§6) remains the deferred path for *true*
  isolation; UIA-pattern background actuation is the v1 concurrency story on the
  live desktop, not a replacement for it.
- `felix-settings.json` gains `background_actuation`, `setvalue_roles`, and
  `user_idle_ms`, all riding ADR-0005's existing toggle machinery.
