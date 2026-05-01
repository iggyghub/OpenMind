## Parent
#1 — PRD: OpenMind v1

## What to build
Kokoro TTS integration: Felix speaks responses aloud using a local Kokoro model. The active profile's voice ID determines which voice is used. Voice selection is exposed in tray profile settings. Demo: say "Felix" and hear "I'm listening" in the profile voice.

## Acceptance criteria
- [ ] Kokoro TTS runs fully locally with no network calls
- [ ] `speak(text, voice_id)` plays audio on the default output device
- [ ] `list_voices()` returns all available Kokoro voices with IDs and display names
- [ ] Active profile voice ID is used automatically
- [ ] Switching voice in profile settings takes effect on the next speak() call without restart
- [ ] TTS does not block the Cerebral event loop (async or threaded)
- [ ] End-to-end: say "Felix" → Felix responds aloud "I'm listening" in the profile voice

## Blocked by
- #3 (audio pipeline)
- #4 (profile manager)
