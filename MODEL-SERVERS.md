# MODEL-SERVERS.md -- Model servers campaign driver

Server-first custom model servers: connect an OpenAI-compatible (or remote
Ollama) server once, discover/auto-track its model, use it in the tray and
(S4) via the harness. Builds on the custom-model registry (custom_models.py,
add_custom_model IPC, router.add_backend).

## Status: ready

## Next slice -- start here

- **Active:** S3 -- #525
- **Model:** opus

## Queue

- [x] S1 -- #523 -- OpenAI-compatible custom model works: Bearer auth + /v1 strip + 4xx caught (Model: opus)
- [x] S2 -- #524 -- model discovery: list_openai_models + discover_models IPC + Fetch datalist (Model: sonnet)
- [ ] S3 -- #525 -- dynamic server-first model: auto-resolve + cache, one picker entry (Model: opus)
- [ ] S4 -- #526 -- harness MCP tool model_server_* [HITL -- DO NOT IMPLEMENT, STOP] (Model: opus)

## Landed PRs

- #528 -- S1 -- OpenAI-compatible custom servers: Bearer auth + /v1 strip + 4xx caught
- #529 -- S2 -- model discovery: list_openai_models + discover_models IPC + Fetch datalist

## SAFETY

Highest priority -- these override the "finish the slice" drive:

1. **No live external calls in tests.** Never hit the user's real server
   (`https://bonsai.ai-dabs.com`) or any live LLM/HTTP endpoint from a test.
   Inject a fake fetch/client, mirroring the established
   `OllamaBackend.list_installed_models(tags_fetch_fn=...)` and
   `AnthropicBackend(client=...)` seams. No network in `cerebral/tests`.
2. **No real secrets.** Never hardcode, commit, print, or persist a real API
   key. Tests use dummy keys only. The api_key stays in the keyring via
   `CredentialStore` (provider `custom_model/<slug>`, field `api_token`) --
   never in the `custom_models` table or logs.
3. **Behaviour only checkable against a real remote server** (e.g. a live
   `/v1/models` round-trip against bonsai) -> APPEND an item to
   `docs/model-servers-live-verify.md`; do NOT perform it in the loop.
4. **S4 (#526) is HITL.** When S4 becomes Active, do NOT implement it. Set
   `Status: blocked` with reason "S4 needs human security review (ADR-0005
   threat gating -- LLM adding a model server is a data-exfiltration
   amplifier)", commit MODEL-SERVERS.md to master, and stop WITHOUT opening a
   PR. The loop halts here for a human.
5. **ASCII-only PowerShell** script bodies (CLAUDE.md gotcha 1). No orphan
   `python -m cerebral.main` process left behind (gotcha 3 applies to any
   detached spawn).
