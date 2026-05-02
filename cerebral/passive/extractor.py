"""
5W1H extractor — Issue #11.

Passive intent extraction: given a transcript of ambient audio, ask the LLM
to extract Who/What/When/Where/Why/How and propose a candidate action for the
queue. Low-confidence results (< CONFIDENCE_THRESHOLD) are discarded.

Public interface:
  ext = FiveW1HExtractor(router)
  action = await ext.extract(transcript)   # → CandidateAction | None
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from cerebral.llm.router import ModelRouter

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.5

EXTRACTION_PROMPT = """\
You are an intent extractor. Given the following ambient audio transcript, \
extract a candidate action using the 5W1H framework.

Transcript:
{transcript}

Respond ONLY with valid JSON matching this exact schema — no prose, no markdown:
{{
  "title": "<short imperative action title, e.g. 'Call the dentist'>",
  "summary": "<one sentence describing what Felix detected>",
  "fields": {{
    "who":   "<person or entity involved, or empty string>",
    "what":  "<action to take>",
    "when":  "<time or deadline, or empty string>",
    "where": "<location, or empty string>",
    "why":   "<reason or motivation, or empty string>",
    "how":   "<method or channel, or empty string>"
  }},
  "confidence": <float 0.0–1.0 reflecting how actionable the transcript is>
}}
"""


@dataclass
class CandidateAction:
    title: str
    summary: str
    fields: dict = field(default_factory=dict)
    confidence: float = 0.0
    context: dict = field(default_factory=dict)


class FiveW1HExtractor:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def extract(
        self, transcript: str, env_context: dict | None = None
    ) -> CandidateAction | None:
        if not transcript or not transcript.strip():
            return None

        prompt = EXTRACTION_PROMPT.format(transcript=transcript.strip())
        try:
            raw = await self._router.complete(prompt, task_type="extraction")
        except Exception:
            logger.exception("[extractor] LLM call failed")
            return None

        action = self._parse(raw)
        if action is not None and env_context:
            action.context = env_context
        return action

    @staticmethod
    def _parse(raw: str) -> CandidateAction | None:
        try:
            # Strip markdown code fences if the model wrapped the JSON
            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(
                    line for line in lines if not line.startswith("```")
                ).strip()

            data = json.loads(text)

            title = data["title"]
            summary = data["summary"]
            confidence = float(data["confidence"])
            raw_fields = data.get("fields", {})

            # Normalise: ensure all 6 keys present as strings
            fields = {
                k: str(raw_fields.get(k, ""))
                for k in ("who", "what", "when", "where", "why", "how")
            }

        except (KeyError, ValueError, json.JSONDecodeError):
            logger.debug("[extractor] Failed to parse LLM response: %r", raw[:200])
            return None

        if confidence < CONFIDENCE_THRESHOLD:
            logger.debug("[extractor] Discarding low-confidence extraction: %.2f", confidence)
            return None

        return CandidateAction(
            title=title,
            summary=summary,
            fields=fields,
            confidence=confidence,
        )
