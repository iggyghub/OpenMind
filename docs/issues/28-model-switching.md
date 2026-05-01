## Parent
#1 — PRD: OpenMind v1

## What to build
Model switching UI: a model browser in the tray menu showing all available models (local Ollama models and cloud providers). Switch the active model at runtime, assign different models per task type, and see which model handled the last request.

## Acceptance criteria
- [ ] Tray "Model" submenu lists all available local Ollama models and configured cloud providers
- [ ] Switching model takes effect immediately on the next request — no restart
- [ ] Per-task-type model mapping: configurable assignment of model per category (quick queries vs complex reasoning)
- [ ] Active and last-used model shown in tray status
- [ ] Switching to a cloud model shows a visual cloud indicator (data leaving the machine)
- [ ] Visualiser reflects model-switching with a brief "thinking" animation

## Blocked by
- #6 (model router)
- #9 (visualiser)
