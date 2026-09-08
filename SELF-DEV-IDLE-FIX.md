# SELF-DEV-IDLE-FIX.md -- self_dev campaign driver: campaign-aware idle check

Source: 2026-09-08 Claude Code session, discovered live while running the TICKER-DISCOVERY
campaign -- three separate driver-advance pushes each raced against `tray/main.js`'s auto-update
restart, which considers Felix "idle" (safe to restart on new commits) purely from chat/voice wake
state, blind to whether a self_dev campaign is mid-flight via the direct-IPC
`scripts/trigger_campaign.py` path. Harmless so far only because `plugins/self_dev.py`'s
2026-09-08 concurrency guard meant no two attempts ever corrupted each other -- a real slice's
in-progress clone/edit/test getting killed by this restart is a live risk, not hypothetical.

Not folded into TICKER-DISCOVERY.md's own queue -- unrelated topic (self_dev/tray infra, not the
trading candidate pool), kept as its own driver so the two campaigns' scopes stay clean. Runs as
the next self_dev slice once TICKER-DISCOVERY's DD5 lands.

**tray/ guardrail note (ADR-0015 decision 5, same caveat as TRADING-PANEL-UI.md's UI1):** `tray/`
is in `GUARDRAIL_PATHS` but the 2026-08-21 amendment made that informational-only -- self_dev can
merge it. The self_dev sandbox test gate is pytest-only and cannot run any JS at all, let alone
Electron main-process code with no existing test harness (`tray/main.js` has no test file today).
**Hand-verify this one by actually reading the diff and, ideally, exercising a real restart -- there
is no automated safety net for this specific change.**

## Status: ready

## Next slice -- start here

- **Active:** IDLE1 -- #1168
- **Model:** sonnet

## Queue

- [ ] IDLE1 -- #1168 -- Auto-update restart must not consider a running self_dev_campaign idle

## Landed PRs

(none yet)
