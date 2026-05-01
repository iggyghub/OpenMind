# ADR-0004: Integration registry and build priority

**Date:** 2026-05-01
**Status:** Accepted

## Context

Felix needs to interact with anything the user can interact with on a computer. Rather than building everything at once, integrations are prioritised into waves. All integrations are exposed as MCP servers. OpenClaw handles model providers and messaging channels — Felix's MCP layer covers the action/tool side only.

## Decision

Integrations are categorised into three build waves:

**Day 1** — built alongside the core system:
OS tools (Clock, Scheduler, Browser, Files, Apps, Clipboard, Notes, System, Shell), Google Workspace (with local OSS fallbacks), Git, GitHub/GitLab, Docker, Package Managers, SSH, HTTP Client, Wikipedia, Weather (Open-Meteo), News, Stocks/Crypto (read-only), Bitwarden (read-only), VPN, Network Scanner, Printer/Scanner, Game Launcher (Steam), Invoice/Receipt OCR, Zoom/Google Meet, Phone Calls (via OpenClaw).

**Second wave** — added via the growth loop as needed:
Notion, Obsidian, Todoist, Time Tracker, YouTube, Reddit, Twitter/X, RSS, Sports Scores, GIMP/Darktable, Blender, Figma, FFmpeg, Home Assistant, Dropbox/OneDrive.

**Later:**
Health integrations (Fitbit, Garmin, Google Fit).

## Consequences

- 28 Day 1 MCP servers gives Felix broad capability from launch without overbuilding.
- Second wave integrations use the growth loop (`/grill-me` → build → register) rather than being pre-built.
- Google Workspace is the primary integration suite; every Google tool has a defined local OSS fallback for offline use.
- Security tools (Bitwarden, VPN, Network Scanner) ship Day 1 — read-only where sensitive.
- Health data deferred until the user explicitly needs it.
