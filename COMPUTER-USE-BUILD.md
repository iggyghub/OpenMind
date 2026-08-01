# COMPUTER-USE-BUILD.md -- Computer use (ADR-0016) campaign driver

Autonomous loop for the ADR-0016 computer-use capability (see
`docs/adr/0016-computer-use.md`). Felix sees the screen and drives the
mouse/keyboard itself: hybrid modality (UIA structured-first, pixel-vision
fallback), Windows-only v1, fail-closed elsewhere.

## Status: done

## Next slice -- start here

- **Active:** (none -- AFK queue exhausted; S3/#577 and S8/#581 remain Human-gated)
- **Model:** opus

## Queue (AFK -- auto-run, in blocker order)

- [x] S1 -- #574 -- computer_use plugin spine (window capture + UIA read + actuation + retry loop) on Calculator
- [x] S2 -- #576 -- 3-part kill switch (corner + F11+F12 + Visualiser Stop) + window-bounded region
- [x] S4 -- #575 -- multimodal Backend seam + computer_use_vision routing (local -> Budd -> cloud, honors local_only)
- [x] S5 -- #578 -- pixel-vision fallback + RAM thumbnail buffer + DRM-black escalation
- [x] S6 -- #579 -- attended handoff on retry exhaustion / no structured surface
- [x] S7 -- #580 -- browser-as-app stealth path + planner selection vs Browser plugin

## Human-gated -- NOT auto-run (the loop must NEVER set these Active)

- [ ] S3 -- #577 -- ADR-0005 gate integration + full-autonomy switch (GUARDRAIL: touches cerebral/security/, human review)
- [ ] S8 -- #581 -- Discord target by sight (BAN-RISK: Discord ToS, throwaway account only, human review)

## Landed PRs

- S1 -- #574 -- PR #582
- S2 -- #576 -- PR #583
- S4 -- #575 -- PR #584
- S5 -- #578 -- PR #585
- S6 -- #579 -- PR #586
- S7 -- #580 -- PR #587

## SAFETY

Highest priority -- overrides finishing any slice:

1. **HITL slices are off-limits to this loop.** If the Active slice is ever
   #577 (S3) or #581 (S8), do NOT implement it: set `Status: blocked` (reason:
   HITL needs human review), commit the driver, and STOP without a PR. These two
   are tracked in the Human-gated section and must never be set Active.
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
   running app, real grounding accuracy, real Discord) -> APPEND it to
   `docs/computer-use-live-verify.md` and do NOT perform it.

Code areas in scope: `plugins/computer_use.py`, `cerebral/llm/router.py`
(S4 multimodal seam), `cerebral/main.py` (IPC seams only, not the gate),
`cerebral/tests/*`, `tray/windows/main.html`, `tray/lib/*.js`. Tests are the
gate: `python -m pytest cerebral/tests -q` plus `npx jest` in `tray/` when a
tray file changed -- the FULL suite must be green before a PR, re-run it, do not
trust a subset.
