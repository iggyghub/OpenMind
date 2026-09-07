from __future__ import annotations

from cerebral.verification import VerifyResult

def test_verify_result_shape_and_defaults() -> None:
    # Assert shape
    result = VerifyResult(passed=True, evidence="test", score=0.5)
    assert result.passed is True
    assert result.evidence == "test"
    assert result.score == 0.5

    # Assert score defaults to None
    result_none_score = VerifyResult(passed=False, evidence="fail")
    assert result_none_score.score is None
