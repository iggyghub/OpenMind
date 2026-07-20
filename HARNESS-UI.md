# HARNESS-UI.md -- Harness UI Rework campaign driver

Autonomous slice loop for the Harness UI Rework (`docs/harness-ui-rework.md`).
Each session reads this file + the spec, implements the ONE active slice as its
issue specifies, opens a per-issue PR, merges it, then rewrites the "Next slice"
block below. This file is the only memory between sessions.

## Status: ready

## Next slice -- start here

- **Active:** S2 -- #470
- **Model:** sonnet

## Queue

- [x] S1 -- #469 -- plugins:list + plugins:changed broadcast (backend) (Model: opus)
- [ ] S2 -- #470 -- disabled_plugins setting + plugins:set_enabled (backend) (Model: sonnet)
- [ ] S3 -- #471 -- Harness section: filters, card grid, read-only drawer (renderer) (Model: sonnet)
- [ ] S4 -- #472 -- enable/disable toggle + plugins:test_call + args-form-from-schema (both) (Model: opus)
- [ ] S5 -- #473 -- route collapse 16->4 + hash redirects + header profile switcher (renderer) (Model: sonnet)

Phase 3 (activity feed + the `content_preview` transcript schema change) is
DEFERRED -- the spec says build it later and keep it out of phases 1-2. The loop
stops (Status: done) after S5. Do NOT touch the transcript schema unattended.

## Landed PRs

- S1 #469 -- PR #475 -- plugins:list + plugins:changed broadcast

## SAFETY

Highest priority; overrides the issue if they ever conflict.

1. Read `docs/harness-ui-rework.md` first. Section 2 constraints are hard rules:
   main window stays node-free (nodeIntegration:false, contextIsolation:true, WS
   only -- ADR-0007); SettingsStore closed key set; the gate order
   (scan -> capabilities -> create -> register) is untouched.
2. **No secret ever leaves the keyring.** No WS message may carry a secret value
   -- metadata only. Every backend slice that touches a payload adds/keeps a test
   that greps the serialized message for secret patterns (acceptance check,
   section 9). If you cannot prove a payload is secret-free, set Status: blocked.
3. Never install software (no winget, no npm global installs). Use the deps
   already in `tray/` and the repo's Python env.
4. Do NOT modify `tray/main.js` (S5 depends on that being true).
5. If you launch Cerebral to smoke IPC, launch it in the BACKGROUND and ALWAYS
   terminate it before finishing -- leave no orphan `python -m cerebral.main`.
6. Behaviour only verifiable by eye in the live Electron window (visual layout,
   real drawer interaction) -> APPEND an item to `docs/harness-ui-live-verify.md`,
   do NOT perform it. Logic must still be covered by jest/pytest.
7. Gate on tests: `python -m pytest cerebral/tests -q` for backend slices, plus
   `npx jest` in `tray/` for renderer slices. Proceed only if ALL pass.
