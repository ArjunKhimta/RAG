"""Scores for how well a search setup brings each question's expected code to the answer model.

For one question and one setup:
- recall: the share of the question's expected definitions found among the sources the model
  would see. The model only ever sees those few sources, so this is the measure that matters.
- complete: whether every expected definition was found.
- first-hit rank: the position of the first source that covers any expected definition, and its
  reciprocal (1 for first place, 1/2 for second, and so on, 0 when none is found). The model reads
  sources in order, so higher is better.

Across questions, recall is pooled: all definitions found divided by all expected, so a question
expecting two definitions counts twice as much as one expecting one. The mean reciprocal rank
(MRR) averages the reciprocals. Times are reported as the median and the 90th percentile, using
the nearest-rank method, so each is a time that actually occurred.

Questions whose answer is not in the code have nothing to find and are left out.
"""

from __future__ import annotations

from dataclasses import dataclass

from retrieval.chunk_statistics import nearest_rank_percentile
from retrieval.question_set import EvaluationQuestion, found_definitions, is_covered_by
from retrieval.search_results import SearchResult

MEDIAN_PERCENTILE = 50

SLOW_PERCENTILE = 90


@dataclass(frozen=True)
class QuestionScore:
    question_id: str
    expected_count: int
    found_count: int
    first_hit_rank: int | None
    milliseconds: float

    @property
    def is_complete(self) -> bool:
        return self.found_count == self.expected_count

    @property
    def reciprocal_rank(self) -> float:
        if self.first_hit_rank is None:
            return 0.0
        return 1 / self.first_hit_rank


@dataclass(frozen=True)
class SetupSummary:
    setup: str
    question_count: int
    expected_total: int
    found_total: int
    complete_count: int
    mean_reciprocal_rank: float
    median_milliseconds: float
    slow_milliseconds: float

    @property
    def recall(self) -> float:
        if self.expected_total == 0:
            return 0.0
        return self.found_total / self.expected_total


def score_question(
    question: EvaluationQuestion, sources: list[SearchResult], milliseconds: float
) -> QuestionScore:
    return QuestionScore(
        question_id=question.question_id,
        expected_count=len(question.expected),
        found_count=len(found_definitions(question.expected, sources)),
        first_hit_rank=first_hit_rank(question, sources),
        milliseconds=milliseconds,
    )


def first_hit_rank(question: EvaluationQuestion, sources: list[SearchResult]) -> int | None:
    """Return the 1-indexed position of the first source covering any expected definition."""
    for rank, source in enumerate(sources, start=1):
        if any(is_covered_by(expected, source) for expected in question.expected):
            return rank
    return None


def summarize_scores(setup: str, scores: list[QuestionScore]) -> SetupSummary:
    if not scores:
        raise ValueError("A summary needs at least one scored question")
    sorted_times = sorted(score.milliseconds for score in scores)
    return SetupSummary(
        setup=setup,
        question_count=len(scores),
        expected_total=sum(score.expected_count for score in scores),
        found_total=sum(score.found_count for score in scores),
        complete_count=sum(1 for score in scores if score.is_complete),
        mean_reciprocal_rank=sum(score.reciprocal_rank for score in scores) / len(scores),
        median_milliseconds=nearest_rank_percentile(sorted_times, MEDIAN_PERCENTILE),
        slow_milliseconds=nearest_rank_percentile(sorted_times, SLOW_PERCENTILE),
    )
