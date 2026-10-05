from __future__ import annotations

import pytest

from evaluation.faithfulness_judge import Verdict
from evaluation.faithfulness_metrics import (
    FaithfulnessScore,
    JudgingOutcome,
    summarize_faithfulness,
)

SUPPORTED = Verdict.SUPPORTED

MISCITED = Verdict.MISCITED

UNSUPPORTED = Verdict.UNSUPPORTED


def _score(
    *verdicts: Verdict,
    outcome: JudgingOutcome = JudgingOutcome.JUDGED,
    uncited: int = 0,
    prompt_tokens: int | None = 2000,
    request_count: int = 1,
) -> FaithfulnessScore:
    return FaithfulnessScore(
        question_id="flask-001",
        setup="vector",
        outcome=outcome,
        verdicts=list(verdicts),
        uncited_sentence_count=uncited,
        prompt_tokens=prompt_tokens,
        request_count=request_count,
    )


def test_an_answer_is_fully_supported_only_when_every_sentence_is_supported():
    assert _score(SUPPORTED, SUPPORTED).is_fully_supported
    assert not _score(SUPPORTED, MISCITED).is_fully_supported
    assert not _score(outcome=JudgingOutcome.REJECTED).is_fully_supported


def test_one_unsupported_sentence_marks_the_answer():
    assert _score(SUPPORTED, UNSUPPORTED).has_unsupported
    assert not _score(SUPPORTED, MISCITED).has_unsupported


def test_sentences_are_pooled_across_judged_answers():
    scores = [
        _score(SUPPORTED, SUPPORTED, SUPPORTED, uncited=1, prompt_tokens=1000),
        _score(SUPPORTED, MISCITED, prompt_tokens=3000),
        _score(UNSUPPORTED, prompt_tokens=2000, request_count=2),
        _score(outcome=JudgingOutcome.REJECTED, prompt_tokens=None),
        _score(outcome=JudgingOutcome.FAILED, prompt_tokens=None),
    ]

    summary = summarize_faithfulness("vector", scores)

    assert summary.answer_count == 5
    assert summary.judged_count == 3
    assert summary.rejected_count == 1
    assert summary.failed_count == 1
    assert summary.sentence_count == 6
    assert summary.supported_count == 4
    assert summary.miscited_count == 1
    assert summary.unsupported_count == 1
    assert summary.uncited_sentence_count == 1
    assert summary.fully_supported_count == 1
    assert summary.any_unsupported_count == 1
    assert summary.median_prompt_tokens == 2000
    assert summary.request_count == 6


def test_a_summary_needs_at_least_one_answer():
    with pytest.raises(ValueError):
        summarize_faithfulness("vector", [])
