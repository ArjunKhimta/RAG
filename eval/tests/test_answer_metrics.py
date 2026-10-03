from __future__ import annotations

from dataclasses import replace

import pytest
from retrieval.answer_generation import Citation, GeneratedAnswer, ModelReply

from evaluation.answer_metrics import (
    AnswerOutcome,
    score_generated_answer,
    score_unanswered,
    summarize_answers,
)
from helpers import expected_definition, question, source

RUN = expected_definition("run", 10, 20)

PREPARE = expected_definition("prepare", 30, 40)

SOURCES = [source("run", 10, 20), source("prepare", 30, 40), source("other", 50, 60)]


def _generated(found_answer: bool, citations: list[Citation], prompt_tokens: int = 100):
    return GeneratedAnswer(
        found_answer=found_answer,
        answer="text",
        citations=citations,
        sources=SOURCES,
        reply=ModelReply(text="{}", prompt_tokens=prompt_tokens, attempt_count=1),
    )


def test_an_answer_citing_both_expected_definitions_cites_every_expected():
    two_part = question("q-1", (RUN, PREPARE))
    generated = _generated(True, [Citation(1, 12, 14), Citation(2, 31, 33)])

    score = score_generated_answer(two_part, SOURCES, generated, milliseconds=1500)

    assert score.outcome == AnswerOutcome.ANSWERED
    assert score.cited_count == 2
    assert score.cites_every_expected
    assert score.cites_at_least_one
    assert not score.is_wrong_refusal
    assert score.prompt_tokens == 100


def test_citing_one_of_two_cites_at_least_one_but_not_every_expected():
    two_part = question("q-1", (RUN, PREPARE))
    generated = _generated(True, [Citation(1, 12, 14), Citation(3, 51, 52)])

    score = score_generated_answer(two_part, SOURCES, generated, milliseconds=1500)

    assert score.cited_count == 1
    assert not score.cites_every_expected
    assert score.cites_at_least_one


def test_saying_not_found_while_the_code_was_present_is_a_wrong_refusal():
    score = score_generated_answer(
        question("q-1", (RUN,)), SOURCES, _generated(False, []), milliseconds=900
    )

    assert score.outcome == AnswerOutcome.SAID_NOT_FOUND
    assert score.is_wrong_refusal
    assert not score.cites_at_least_one


def test_saying_not_found_when_the_code_never_arrived_is_a_search_failure_not_a_wrong_refusal():
    score = score_generated_answer(
        question("q-1", (RUN,)), [source("other", 50, 60)], _generated(False, []), milliseconds=900
    )

    assert not score.is_wrong_refusal


def test_a_question_with_no_answer_in_the_code_is_refused_correctly_only_without_citations():
    unanswerable = replace(question("q-9", ()), answer_in_code=False)

    clean = score_generated_answer(unanswerable, SOURCES, _generated(False, []), 800)
    claimed = score_generated_answer(
        unanswerable, SOURCES, _generated(True, [Citation(3, 51, 52)]), 800
    )

    assert clean.is_correct_refusal
    assert not claimed.is_correct_refusal
    assert not clean.cites_every_expected


def test_a_rejected_reply_cites_nothing():
    score = score_unanswered(
        question("q-1", (RUN,)), SOURCES, AnswerOutcome.REJECTED, milliseconds=700, request_count=1
    )

    assert score.cited_count == 0
    assert not score.cites_at_least_one
    assert score.found_in_sources_count == 1


def test_a_summary_counts_cites_refusals_rejections_and_requests():
    unanswerable = replace(question("q-9", ()), answer_in_code=False)
    scores = [
        score_generated_answer(
            question("q-1", (RUN,)), SOURCES, _generated(True, [Citation(1, 12, 14)], 120), 1000
        ),
        score_generated_answer(question("q-2", (RUN,)), SOURCES, _generated(False, [], 80), 3000),
        score_unanswered(question("q-3", (RUN,)), SOURCES, AnswerOutcome.REJECTED, 2000, 1),
        score_generated_answer(unanswerable, SOURCES, _generated(False, [], 90), 500),
    ]

    summary = summarize_answers("vector", scores)

    assert summary.answerable_count == 3
    assert summary.cites_every_count == 1
    assert summary.cites_any_count == 1
    assert (summary.correct_refusal_count, summary.refusal_question_count) == (1, 1)
    assert summary.wrong_refusal_count == 1
    assert summary.rejected_count == 1
    assert summary.failed_count == 0
    assert summary.median_prompt_tokens == 90
    assert summary.median_milliseconds == 1000
    assert summary.slow_milliseconds == 3000
    assert summary.request_count == 4


def test_a_summary_needs_at_least_one_answer():
    with pytest.raises(ValueError, match="at least one"):
        summarize_answers("vector", [])
