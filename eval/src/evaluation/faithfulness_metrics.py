"""Scores for the faithfulness judge's verdicts on each setup's answers.

For one answered reply, the judging is one of:
- judged: the judge gave one verdict for every cited sentence
- rejected: the judge's reply was malformed or left a sentence without exactly one verdict
- failed: Gemini returned an error that was not a used-up daily quota

Sentence rates are over the sentences judged, pooled across answers, so a long answer counts for
more than a short one. Answer rates are over the answers judged: fully supported means every
judged sentence was supported by its own cited lines; any unsupported means at least one
sentence said something no source shows, the most serious failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from retrieval.chunk_statistics import nearest_rank_percentile

from evaluation.faithfulness_judge import Verdict

MEDIAN_PERCENTILE = 50


class JudgingOutcome(StrEnum):
    JUDGED = "judged"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class FaithfulnessScore:
    question_id: str
    setup: str
    outcome: JudgingOutcome
    verdicts: list[Verdict]
    uncited_sentence_count: int
    prompt_tokens: int | None
    request_count: int

    @property
    def is_fully_supported(self) -> bool:
        is_judged = self.outcome == JudgingOutcome.JUDGED
        return is_judged and all(verdict == Verdict.SUPPORTED for verdict in self.verdicts)

    @property
    def has_unsupported(self) -> bool:
        return Verdict.UNSUPPORTED in self.verdicts


@dataclass(frozen=True)
class FaithfulnessSummary:
    setup: str
    answer_count: int
    judged_count: int
    rejected_count: int
    failed_count: int
    sentence_count: int
    supported_count: int
    miscited_count: int
    unsupported_count: int
    uncited_sentence_count: int
    fully_supported_count: int
    any_unsupported_count: int
    median_prompt_tokens: int | None
    request_count: int


def summarize_faithfulness(setup: str, scores: list[FaithfulnessScore]) -> FaithfulnessSummary:
    if not scores:
        raise ValueError("A summary needs at least one judged answer")
    judged = [score for score in scores if score.outcome == JudgingOutcome.JUDGED]
    verdicts = [verdict for score in judged for verdict in score.verdicts]
    prompt_tokens = sorted(
        score.prompt_tokens for score in scores if score.prompt_tokens is not None
    )
    return FaithfulnessSummary(
        setup=setup,
        answer_count=len(scores),
        judged_count=len(judged),
        rejected_count=_count_outcome(scores, JudgingOutcome.REJECTED),
        failed_count=_count_outcome(scores, JudgingOutcome.FAILED),
        sentence_count=len(verdicts),
        supported_count=verdicts.count(Verdict.SUPPORTED),
        miscited_count=verdicts.count(Verdict.MISCITED),
        unsupported_count=verdicts.count(Verdict.UNSUPPORTED),
        uncited_sentence_count=sum(score.uncited_sentence_count for score in judged),
        fully_supported_count=sum(1 for score in judged if score.is_fully_supported),
        any_unsupported_count=sum(1 for score in judged if score.has_unsupported),
        median_prompt_tokens=(
            nearest_rank_percentile(prompt_tokens, MEDIAN_PERCENTILE) if prompt_tokens else None
        ),
        request_count=sum(score.request_count for score in scores),
    )


def _count_outcome(scores: list[FaithfulnessScore], outcome: JudgingOutcome) -> int:
    return sum(1 for score in scores if score.outcome == outcome)
