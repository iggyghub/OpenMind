## Parent
#1 — PRD: OpenMind v1

## What to build
OpenClaw channel bridge: configure OpenClaw's messaging channels (WhatsApp, Telegram, Discord) so Felix is reachable remotely before a native mobile client exists. A message sent to Felix via any channel is received by OpenClaw, routed to Cerebral, and the response returned through the same channel. Day-one mobile access.

## Acceptance criteria
- [ ] OpenClaw gateway starts alongside Cerebral as a background service
- [ ] At least one channel (Telegram recommended) is configured and active
- [ ] Sending "Felix, what time is it?" via the channel returns a text response
- [ ] Multi-turn conversation context is maintained within a channel session
- [ ] Channel messages triggering tools (e.g. "set a reminder") execute via MCP and confirm back through the channel
- [ ] Unsupported commands return a helpful error via the channel
- [ ] SETUP.md documents channel configuration steps

## Blocked by
- #6 (model router)
- #7 (MCP orchestrator)
