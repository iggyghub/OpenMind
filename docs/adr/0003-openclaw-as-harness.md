# ADR-0003: OpenClaw as the messaging harness and remote access gateway

**Date:** 2026-05-01
**Status:** Accepted

## Context

Felix needs to integrate with multiple external messaging channels (WhatsApp, Telegram, Slack, Discord, Teams, etc.) without writing individual integrations for each. It also needs a way to be reached from phones and other devices before native mobile clients are built.

## Decision

OpenClaw serves as the **harness** — the master command and communication gateway. Felix sends and receives all external channel messages through a single OpenClaw MCP interface. OpenClaw is also the remote access point: a message sent to Felix via WhatsApp or Telegram is received by OpenClaw, routed to Cerebral, and the response returned through the same channel.

## Consequences

- Felix talks to one interface; OpenClaw handles per-service complexity.
- Phone access is available on day one via any messaging app OpenClaw supports, before a native client ships.
- Adding a new messaging channel = configuring it in OpenClaw, not writing new Felix code.
- OpenClaw is already installed and already speaks MCP — no adapter layer needed.
