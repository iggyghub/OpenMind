# Model servers -- live verification

Behaviour that only holds against a real remote LLM server (per MODEL-SERVERS.md
SAFETY block: no live external calls in tests). Cross off after a human runs
the check.

## S1 -- #523 -- OpenAI-compatible custom model (Bearer + /v1 + 4xx)

- [ ] Settings -> AI models: add an OpenAI-compatible server (e.g.
      `https://bonsai.ai-dabs.com/v1`) with a pinned model name and a valid
      API key. The add-time reachability ping succeeds (no 401), and switching
      to that model routes a completion end-to-end.
- [ ] The same server added with the bare host URL (no trailing `/v1`) also
      succeeds -- verifies the normalization actually collapses to a single
      `/v1/chat/completions` on the wire.
- [ ] Adding with a deliberately-wrong key surfaces a `ModelUnavailableError`
      with the HTTP status (401), not a raw `httpx` traceback in the tray log.
