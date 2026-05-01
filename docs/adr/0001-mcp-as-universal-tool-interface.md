# ADR-0001: MCP as the universal tool interface

**Date:** 2026-05-01
**Status:** Accepted

## Context

Felix needs to execute actions across a wide range of capabilities — files, browser, calendar, shell, messaging, and custom user-built tools. The system must be self-extending: users describe a new capability in plain language, Felix generates it, and it becomes immediately available. A consistent interface across all tools reduces coupling and makes the system predictable for the LLM.

## Decision

Every capability in Felix is exposed as an **MCP (Model Context Protocol) server**. The LLM calls tools exclusively through MCP regardless of what is underneath — some servers are native Python, some wrap n8n workflows, some are OpenClaw integrations. New tools built via the growth loop (`/grill-me` → generate → register) produce new MCP servers placed in `/plugins`.

## Consequences

- The LLM always has one interface. No special-casing per tool type.
- Adding a capability = adding an MCP server. Removing one = deregistering it.
- OpenClaw already speaks MCP, so the harness integrates without an adapter layer.
- Plugin code lives in `/plugins`, is always human-readable, and can be edited directly.
- The natural language plugin builder targets MCP server generation specifically.
