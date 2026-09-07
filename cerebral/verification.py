from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

@dataclass
class VerifyResult:
    passed: bool
    evidence: str
    score: float | None = None

class Verifiable(Protocol):
    def verify(self) -> VerifyResult: ...
