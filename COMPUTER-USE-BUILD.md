# COMPUTER-USE-BUILD.md -- Computer use (ADR-0016) campaign driver

Autonomous loop for the ADR-0016 computer-use capability (see
`docs/adr/0016-computer-use.md`). Felix sees the screen and drives the
mouse/keyboard itself: hybrid modality (UIA structured-first, pixel-vision
fallback), Windows-only v1, fail-closed elsewhere.

## Status: ready

Running the **AFK software seams against fakes** (S11/#605 -> S12/#606 ->
S15/#609 -> S16/#610), each in a fresh session on an efficient model (sonnet),
per the v1 fakes-first pattern (SAFETY 2). The software seams are decoupled from
the real vehicle: the protocol, worker, kill/dead-man logic, thumbnail stream,
and mode ladder are all built + unit-tested with injected fakes (fake session
token / process launcher / WS transport / keyring). The **vehicle** slices (#603
spike, #604 provisioning, #607 IDD driver, #608 real logins) stay **Human-led /
never auto-run** (SAFETY 1/6). The "blocked by <vehicle>" note on each software
slice refers to REAL-session verification, which is DEFERRED to
`docs/computer-use-live-verify.md` -- do NOT set `Status: blocked` for that
reason; append the real-hardware check to live-verify and proceed with fakes.

## Next slice -- start here

- **Active:** S12 -- #606 (out-of-session kill switch + Job-Object/heartbeat dead-men, built against fakes)
- **Model:** sonnet

### Landed PRs

- S11 -- #605 -- in-session worker + device-agnostic action protocol -- PR #612

---

## Phase 2 -- Isolated interactive session (#601)

Design: `docs/adr/0016-computer-use.md`, amendment "Isolated interactive session
(issue #601)", 2026-08-03. Umbrella issue: #601. The isolated session is the
escalation for the input-stealing paths (foreground synthetic input + pixel)
that the 2026-08-02 background-actuation amendment cannot make concurrent on the
live desktop.

### Human-led -- NOT auto-run (the loop must NEVER set these Active)

Real OS state / real accounts / a signed driver / real logins -- cannot be
proven with fakes and must not be auto-performed. A human does these; the loop
records their real-hardware verification in `docs/computer-use-live-verify.md`.

- [ ] S9  -- #603 -- SPIKE: drive Notepad `read_ui` in a real second session (real user + loopback RDP + `CreateProcessAsUser`). Proves the "fiddly core" before anything else.
- [ ] S10 -- #604 -- auto-provision Felix's session (creates a real standard Windows user, real RDP, real keyring credential write)
- [ ] S13 -- #607 -- IDD virtual-display DRIVER INSTALL (signed system driver -- a security/system change)
- [ ] S14 -- #608 -- per-app identity map + one-time real app logins (the login bootstrap is a real-account action)

### AFK queue -- auto-run once the vehicle lands (software seams, fakes only)

These are buildable with injected fakes exactly like the v1 plugin was (fake
session token, fake process launcher, fake WS transport, fake keyring). Each
stays blocked until its real prerequisite (above) is human-verified; real
cross-session behaviour is APPENDED to `docs/computer-use-live-verify.md`, never
performed by the loop.

- [x] S11 -- #605 -- in-session worker + device-agnostic action protocol over Cerebral's WS IPC (blocked by S10 real vehicle) -- PR #612
- [ ] S12 -- #606 -- out-of-session kill switch + Job-Object/heartbeat dead-men (blocked by S11)
- [ ] S15 -- #609 -- watch thumbnail stream + on-demand RDP take-over that pauses the worker (blocked by S11, S12)
- [ ] S16 -- #610 -- three-tier mode ladder + never-silent failure/notify (blocked by S12, S13, S14)

### Model

- **Model:** sonnet (efficient model for the AFK software seams)

---

## Phase 1 -- v1 plugin (done)

### Landed (AFK)

- [x] S1 -- #574 -- computer_use plugin spine (window capture + UIA read + actuation + retry loop) on Calculator -- PR #582
- [x] S2 -- #576 -- 3-part kill switch (corner + F11+F12 + Visualiser Stop) + window-bounded region -- PR #583
- [x] S4 -- #575 -- multimodal Backend seam + computer_use_vision routing (local -> Budd -> cloud, honors local_only) -- PR #584
- [x] S5 -- #578 -- pixel-vision fallback + RAM thumbnail buffer + DRM-black escalation -- PR #585
- [x] S6 -- #579 -- attended handoff on retry exhaustion / no structured surface -- PR #586
- [x] S7 -- #580 -- browser-as-app stealth path + planner selection vs Browser plugin -- PR #587

### Human-gated (never auto-run)

- [ ] S3 -- #577 -- ADR-0005 gate integration + full-autonomy switch (GUARDRAIL: touches cerebral/security/, human review)
- [ ] S8 -- #581 -- Discord target by sight (BAN-RISK: Discord ToS, throwaway account only, human review)

---

## SAFETY

Highest priority -- overrides finishing any slice:

1. **HITL / Human-led slices are off-limits to this loop.** If the Active slice
   is ever a Human-gated (#577 S3, #581 S8) or Phase-2 Human-led slice (#603 S9,
   #604 S10, #607 S13, #608 S14), do NOT implement it: set `Status: blocked`
   (reason: needs a human), commit the driver, and STOP without a PR. These live
   in the Human-gated / Human-led sections and must never be set Active by the loop.
2. **Never drive real input or a real account in tests.** No real mouse/keyboard
   actuation, no real screen grounding call, no live Discord, no live external
   HTTP/LLM endpoint. Inject fakes for UIA trees, capture, actuation, and the
   vision backend -- follow the existing seam pattern (OllamaBackend
   `tags_fetch_fn`, AnthropicBackend client injection, discord_user `fetch_fn`).
3. **Do not touch the guardrails.** AFK slices must NOT modify `cerebral/security/`
   (the ADR-0005 gate) -- that is S3's job and it is HITL. If a slice seems to
   need a guardrail edit, its scope has crept: set `Status: blocked` (reason:
   needs guardrail change, escalate to S3) and STOP without a PR.
4. **Fail-closed on non-Windows** must be covered by a test (import/registration
   denies the capability, no crash).
5. **Behaviour only checkable on real hardware or a live app** (actual UIA of a
   running app, real grounding accuracy, real Discord, a real second session, a
   real RDP connect, a real display driver) -> APPEND it to
   `docs/computer-use-live-verify.md` and do NOT perform it.
6. **Phase-2 real-world actions are OFF-LIMITS to the loop.** Creating or
   altering a real Windows user account, real loopback-RDP provisioning,
   installing the IDD display driver, writing a real Felix credential to
   Credential Manager, and any real app login are Human-led (SAFETY 1). Code them
   behind seams and unit-test with injected fakes (fake session token, fake
   process launcher, fake transport, fake keyring); the real action goes to
   `docs/computer-use-live-verify.md`. The consequence/irreversible gate is NOT
   relaxed for the isolated session -- a committing action via the worker must
   still hit the ADR-0005 modal.

Code areas in scope: `plugins/computer_use.py`, the new isolated-session worker
module under `cerebral/` (Phase 2 seam), `cerebral/llm/router.py` (S4 multimodal
seam), `cerebral/main.py` (IPC seams only, not the gate), `cerebral/tests/*`,
`tray/windows/main.html`, `tray/lib/*.js`. Tests are the gate:
`python -m pytest cerebral/tests -q` plus `npx jest` in `tray/` when a tray file
changed -- the FULL suite must be green before a PR, re-run it, do not trust a
subset.
