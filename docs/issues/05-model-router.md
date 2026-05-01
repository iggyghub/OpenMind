## Parent
#1 — PRD: OpenMind v1

## What to build
Model router: routes LLM completions to Ollama/Gemma 4 (local, default) or Claude via Anthropic (cloud, opt-in) using OpenClaw's inference layer. Felix can now answer a plain spoken question end-to-end: wake → transcribe → LLM → speak response.

## Acceptance criteria
- [ ] `complete(prompt, task_type)` returns a response from Ollama/Gemma 4 by default
- [ ] Switching to Claude routes through OpenClaw's Anthropic provider
- [ ] `switch_model(model_id)` changes the active model without restart
- [ ] Local model is used when offline — no silent fallback to cloud
- [ ] End-to-end demo: say "Felix, what is the capital of Japan?" → Felix speaks the answer
- [ ] Model router logs which model handled each request

## Blocked by
- #5 (TTS engine)
