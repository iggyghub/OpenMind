# ADR-0002: Python backend, Node.js/web frontend

**Date:** 2026-05-01
**Status:** Accepted

## Context

Felix needs a backend that is fluent with the AI/ML ecosystem (Whisper, Vosk, Kokoro, ChromaDB, Ollama SDKs — all Python-native) and a frontend that can own the system tray, render a dark web UI with an animated visualiser, and eventually serve thin clients on phones and smartglasses.

## Decision

Split along a clear boundary:

- **Python** owns the AI pipeline (STT, TTS, LLM calls, memory, MCP execution, action routing).
- **Node.js + web** owns the UI layer (system tray, dark UI, animated visualiser, device communication).

The two processes communicate over a local IPC/WebSocket interface.

## Consequences

- The AI layer stays in its natural ecosystem. No wrapping Python libraries in Node bindings.
- The UI layer is thin and portable — the same web frontend can be adapted for phone and glasses clients.
- Clear boundary makes each side independently replaceable.
- Two processes to manage on startup, but both are lightweight background services.
