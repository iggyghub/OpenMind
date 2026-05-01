## Parent
#1 — PRD: OpenMind v1

## What to build
The always-on passive listening pipeline: Vosk runs continuously on the default microphone, maintaining a 60-second rolling audio buffer in RAM (never written to disk). When Vosk detects the wake word "Felix", it hands the buffered audio to faster-whisper for full transcription and routes to active mode. The tray icon reflects the state change over the IPC bridge.

## Acceptance criteria
- [ ] Vosk runs continuously with negligible CPU impact in passive mode
- [ ] A 60-second rolling audio buffer is maintained in RAM only — no disk writes
- [ ] Speaking "Felix" triggers wake detection within 1 second
- [ ] On wake, faster-whisper transcribes the buffered audio and the following command utterance
- [ ] Wake event is emitted over the IPC bridge (tray icon changes to active state)
- [ ] Non-wake ambient speech is discarded silently
- [ ] Pipeline shuts down cleanly when Cerebral stops

## Blocked by
- #2 (project scaffold)
