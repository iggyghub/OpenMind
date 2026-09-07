from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass
class VerifyResult:
    passed: bool
    evidence: str
    score: float | None = None

class Verifiable(Protocol):
    def verify(self) -> VerifyResult: ...


def verify_plugin_test_file(plugin_name: str) -> VerifyResult:
    """Cheap boot-time existence check for a plugin's test file."""
    test_path = Path(f"cerebral/tests/test_plugin_{plugin_name}.py")
    if test_path.exists():
        return VerifyResult(passed=True, evidence=f"Test file {test_path.name} exists.", score=1.0)
    return VerifyResult(passed=False, evidence=f"Missing test file {test_path.name}.", score=0.0)
