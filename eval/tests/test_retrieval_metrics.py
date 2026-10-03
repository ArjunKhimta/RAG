from __future__ import annotations

import pytest

from evaluation.retrieval_metrics import QuestionScore, score_question, summarize_scores
from helpers import expected_definition, question, source

RUN = expected_definition("run", 10, 20)

PREPARE = expected_definition("prepare", 30, 40)


def test_a_question_scores_recall_completeness_and_the_first_hit_rank():
    two_part = question("q-1", (RUN, PREPARE))
    sources = [source("other", 50, 60), source("run", 10, 20), source("unrelated", 70, 80)]

    score = score_question(two_part, sources, milliseconds=12.5)

    assert score.found_count == 1
    assert score.expected_count == 2
    assert not score.is_complete
    assert score.first_hit_rank == 2
    assert score.reciprocal_rank == 0.5
    assert score.milliseconds == 12.5


def test_a_question_with_nothing_found_has_no_rank_and_scores_zero():
    score = score_question(question("q-1", (RUN,)), [source("other", 50, 60)], milliseconds=1.0)

    assert score.found_count == 0
    assert score.first_hit_rank is None
    assert score.reciprocal_rank == 0.0


def test_a_summary_pools_recall_and_averages_reciprocal_ranks():
    scores = [
        QuestionScore("q-1", expected_count=2, found_count=2, first_hit_rank=1, milliseconds=10),
        QuestionScore("q-2", expected_count=1, found_count=0, first_hit_rank=None, milliseconds=30),
        QuestionScore("q-3", expected_count=1, found_count=1, first_hit_rank=4, milliseconds=20),
    ]

    summary = summarize_scores("router", scores)

    assert summary.setup == "router"
    assert summary.question_count == 3
    assert (summary.found_total, summary.expected_total) == (3, 4)
    assert summary.recall == 0.75
    assert summary.complete_count == 2
    assert summary.mean_reciprocal_rank == pytest.approx((1 + 0 + 0.25) / 3)
    assert summary.median_milliseconds == 20
    assert summary.slow_milliseconds == 30


def test_a_summary_needs_at_least_one_question():
    with pytest.raises(ValueError, match="at least one"):
        summarize_scores("router", [])
