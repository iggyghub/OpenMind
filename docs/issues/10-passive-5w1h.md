## Parent
#1 — PRD: OpenMind v1

## What to build
Passive 5W1H intent extraction: when Vosk detects an actionable signal beyond the wake word, faster-whisper transcribes the last ~60 seconds and the LLM extracts Who/What/When/Where/Why/How. The result is a candidate action added to the queue — not executed, just surfaced.

## Acceptance criteria
- [ ] Vosk triggers on a configurable list of actionable signal words
- [ ] On trigger, faster-whisper transcribes the rolling buffer
- [ ] LLM extracts 5W1H fields and proposes a candidate action with a human-readable summary
- [ ] Candidate action appears in the tray queue with the 5W1H summary
- [ ] Low-confidence extractions are discarded or flagged
- [ ] Rolling buffer is cleared after each transcription pass
- [ ] CPU usage in passive mode remains negligible between triggers

## Blocked by
- #7 (MCP orchestrator)
- #8 (tray app queue)
