# MODEL-SERVERS.md -- Model servers campaign driver

Server-first custom model servers + a model priority panel. S1-S3 (connect an
OpenAI-compatible / remote-Ollama server, discover/auto-track its model) landed.
S4 (harness remote-add MCP tool) was SUPERSEDED -- the bridge is chat-only and a
local irreversible modal can't be accepted remotely (see #526). Direction moved
to a per-model on/off + drag-drop fallback-ordering panel (P1 router, P2 tray).

## Status: ready

## Next slice -- start here

- **Active:** P1 -- #531
- **Model:** opus

## Queue

- [x] S1 -- #523 -- OpenAI-compatible custom model works: Bearer auth + /v1 strip + 4xx caught (Model: opus)
- [x] S2 -- #524 -- model discovery: list_openai_models + discover_models IPC + Fetch datalist (Model: sonnet)
- [x] S3 -- #525 -- dynamic server-first model: auto-resolve + cache, one picker entry (Model: opus)
- [~] S4 -- #526 -- harness MCP tool [SUPERSEDED -- closed, not planned]
- [ ] P1 -- #531 -- model priority list + ordered-fallback routing (router + persistence + IPC) (Model: opus)
- [ ] P2 -- #532 -- drag-drop Model priority tray panel (replaces Switch model) (Model: sonnet)

## Landed PRs

- #528 -- S1 -- OpenAI-compatible custom servers: Bearer auth + /v1 strip + 4xx caught
- #529 -- S2 -- model discovery: list_openai_models + discover_models IPC + Fetch datalist
- #530 -- S3 -- dynamic server-first custom model: auto-resolve + cache, one picker entry

## SAFETY

Highest priority -- these override the "finish the slice" drive:

1. **No live external calls in tests.** Never hit the user's real server
   (`https://bonsai.ai-dabs.com`) or any live LLM/HTTP endpoint from a test.
   Inject a fake fetch/client, mirroring the established
   `OllamaBackend.list_installed_models(tags_fetch_fn=...)` and
   `AnthropicBackend(client=...)` seams. No network in `cerebral/tests`.
2. **No real secrets.** Never hardcode, commit, print, or persist a real API
   key. Tests use dummy keys only. The api_key stays in the keyring via
   `CredentialStore` (provider `custom_model/<slug>`, field `api_token`).
3. **Preserve no-silent-fallback (P1).** With the master fallback toggle OFF,
   routing MUST keep today's behavior: use only the top enabled model and raise
   `ModelUnavailableError` when it is down -- never silently fall through to
   cloud. The ordered fallback chain only runs when the master toggle is ON.
4. **Behaviour only checkable live** (a real `/v1/models` round-trip against
   bonsai, or the actual drag-drop feel of the P2 panel) -> APPEND an item to
   `docs/model-servers-live-verify.md`; do NOT perform it in the loop.
5. **ASCII-only PowerShell** script bodies (CLAUDE.md gotcha 1). No orphan
   `python -m cerebral.main` process left behind (gotcha 3 applies to any
   detached spawn).
