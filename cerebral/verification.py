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
    """Cheap boot-time existence check for a plugin's test file (ADR-0034).

    Not a live pytest re-run -- the sandbox's own test gate already runs
    the full suite during self_dev. This only confirms the file exists.
    """
    test_path = Path(__file__).parent / "tests" / f"test_plugin_{plugin_name}.py"
    if test_path.exists():
        return VerifyResult(passed=True, evidence=f"Test file {test_path.name} exists.", score=1.0)
    return VerifyResult(passed=False, evidence=f"Missing test file {test_path.name}.", score=0.0)
