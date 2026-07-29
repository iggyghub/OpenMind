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

## S2 -- #524 -- model discovery (list_openai_models + discover_models IPC + Fetch datalist)

- [ ] Settings -> AI models: with an OpenAI-compatible server URL and API key
      entered, click **Fetch** -- the model-name input populates a native
      datalist showing the server's model IDs (`/v1/models` response). Typing
      in the field still works freely (not locked to the suggestions).
- [ ] With an Ollama server URL entered, click **Fetch** -- the datalist shows
      the installed model names from `/api/tags`.
- [ ] When the server returns exactly one model, the model-name input is
      auto-filled with that model's id.
- [ ] With `kind=anthropic` selected, **Fetch** returns an empty datalist
      immediately (no network call to Anthropic).
- [ ] With an unreachable server URL, **Fetch** completes without an error in
      the UI (empty datalist, user can still type the model name manually).

## S3 -- #525 -- dynamic (server-first) custom model

- [ ] Settings -> AI models: add an OpenAI-compatible server (e.g. bonsai)
      with the model input **left blank** and a valid API key. Add succeeds
      and the server appears as exactly one entry in the switch-list,
      per-task cards, and tray submenu.
- [ ] Switch to the dynamic server and send a completion end-to-end -- the
      call is routed to whatever model `/v1/models` currently returns first.
- [ ] Confirm the `custom_models` row now carries a non-empty `model` (the
      last-resolved cache) and `dynamic=1`.
- [ ] Restart Cerebral with the server **unreachable**. Startup completes
      without blocking (dynamic restore is lazy -- no network at boot).
      Once the server is back up, a completion resolves and routes correctly.
- [ ] Swap the model behind the server (or point the server at a new model),
      then send another completion -- the first call sees a 404, the resolver
      re-queries `/v1/models`, and the retry succeeds against the new model.
      No user action required; the cached `custom_models.model` updates.
- [ ] Same drill with Ollama kind (blank model + Ollama server URL); the
      dynamic entry stays visible under **local-only** (openai-dynamic is
      hidden as expected).
