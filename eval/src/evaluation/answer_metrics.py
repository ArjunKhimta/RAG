"""Scores for the answers written from each search setup's sources.

For one question and one setup, an answer is one of:
- answered: the model said it found the answer and passed the citation check
- said not found: the model said the sources do not contain the answer, and passed the check
- rejected: the citation check refused the reply, so nobody would have seen it
- failed: Gemini returned an error that was not a used-up daily quota

Then:
- cites every expected: an answered question whose citations point into every expected
  definition (strict)
- cites at least one: citations point into at least one expected definition (lenient)
- correct refusal: a question with no answer in the code, where the model said so and cited
  nothing
- wrong refusal: a question whose answer is in the code, where at least one expected definition
  was among the sources, yet the model said it was not found; this separates a model failure
  from a search failure, where the code never arrived

Cite rates are over the questions whose answer is in the code; refusal counts are over the rest.
Times include any wait for the per-minute request limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from retrieval.answer_generation import GeneratedAnswer
from retrieval.chunk_statistics import nearest_rank_percentile
from retrieval.question_set import (
    EvaluationQuestion,
    cited_definitions,
    found_definitions,
    is_correct_refusal,
)
from retrieval.search_results import SearchResult

MEDIAN_PERCENTILE = 50

SLOW_PERCENTILE = 90


class AnswerOutcome(StrEnum):
    ANSWERED = "answered"
    SAID_NOT_FOUND = "said not found"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class AnswerScore:
    question_id: str
    answer_in_code: bool
    outcome: AnswerOutcome
    expected_count: int
    cited_count: int
    found_in_sources_count: int
    is_clean_refusal: bool
    prompt_tokens: int | None
    milliseconds: float
    request_count: int

    @property
    def cites_every_expected(self) -> bool:
        is_answered = self.outcome == AnswerOutcome.ANSWERED
        return self.answer_in_code and is_answered and self.cited_count == self.expected_count

    @property
    def cites_at_least_one(self) -> bool:
        return self.answer_in_code and self.cited_count > 0

    @property
    def is_correct_refusal(self) -> bool:
        return not self.answer_in_code and self.is_clean_refusal

    @property
    def is_wrong_refusal(self) -> bool:
        said_not_found = self.outcome == AnswerOutcome.SAID_NOT_FOUND
        return self.answer_in_code and said_not_found and self.found_in_sources_count > 0


@dataclass(frozen=True)
class AnswerSummary:
    setup: str
    answerable_count: int
    cites_every_count: int
    cites_any_count: int
    refusal_question_count: int
    correct_refusal_count: int
    wrong_refusal_count: int
    rejected_count: int
    failed_count: int
    median_prompt_tokens: int | None
    median_milliseconds: float
    slow_milliseconds: float
    request_count: int


def score_generated_answer(
    question: EvaluationQuestion,
    sources: list[SearchResult],
    generated: GeneratedAnswer,
    milliseconds: float,
) -> AnswerScore:
    outcome = AnswerOutcome.ANSWERED if generated.found_answer else AnswerOutcome.SAID_NOT_FOUND
    cited = cited_definitions(question.expected, generated.citations, generated.sources)
    return AnswerScore(
        question_id=question.question_id,
        answer_in_code=question.answer_in_code,
        outcome=outcome,
        expected_count=len(question.expected),
        cited_count=len(cited),
        found_in_sources_count=len(found_definitions(question.expected, sources)),
        is_clean_refusal=is_correct_refusal(generated),
        prompt_tokens=generated.reply.prompt_tokens,
        milliseconds=milliseconds,
        request_count=generated.reply.attempt_count,
    )


def score_unanswered(
    question: EvaluationQuestion,
    sources: list[SearchResult],
    outcome: AnswerOutcome,
    milliseconds: float,
    request_count: int,
) -> AnswerScore:
    """Score a reply that was rejected or a request that failed: it cites nothing."""
    return AnswerScore(
        question_id=question.question_id,
        answer_in_code=question.answer_in_code,
        outcome=outcome,
        expected_count=len(question.expected),
        cited_count=0,
        found_in_sources_count=len(found_definitions(question.expected, sources)),
        is_clean_refusal=False,
        prompt_tokens=None,
        milliseconds=milliseconds,
        request_count=request_count,
    )


def summarize_answers(setup: str, scores: list[AnswerScore]) -> AnswerSummary:
    if not scores:
        raise ValueError("A summary needs at least one scored answer")
    answerable = [score for score in scores if score.answer_in_code]
    refusal_questions = [score for score in scores if not score.answer_in_code]
    sorted_times = sorted(score.milliseconds for score in scores)
    prompt_tokens = sorted(
        score.prompt_tokens for score in scores if score.prompt_tokens is not None
    )
    return AnswerSummary(
        setup=setup,
        answerable_count=len(answerable),
        cites_every_count=sum(1 for score in answerable if score.cites_every_expected),
        cites_any_count=sum(1 for score in answerable if score.cites_at_least_one),
        refusal_question_count=len(refusal_questions),
        correct_refusal_count=sum(1 for score in refusal_questions if score.is_correct_refusal),
        wrong_refusal_count=sum(1 for score in answerable if score.is_wrong_refusal),
        rejected_count=sum(1 for score in scores if score.outcome == AnswerOutcome.REJECTED),
        failed_count=sum(1 for score in scores if score.outcome == AnswerOutcome.FAILED),
        median_prompt_tokens=(
            nearest_rank_percentile(prompt_tokens, MEDIAN_PERCENTILE) if prompt_tokens else None
        ),
        median_milliseconds=nearest_rank_percentile(sorted_times, MEDIAN_PERCENTILE),
        slow_milliseconds=nearest_rank_percentile(sorted_times, SLOW_PERCENTILE),
        request_count=sum(score.request_count for score in scores),
    )
