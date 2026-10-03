from __future__ import annotations

from dataclasses import replace

from retrieval.answer_generation import Citation, GeneratedAnswer, ModelReply

from evaluation.answer_metrics import score_generated_answer
from evaluation.answer_results import answer_group_summaries, render_answer_table
from evaluation.results import question_groups
from helpers import expected_definition, question, source
from test_results import DETAILS

RUN = expected_definition("run", 10, 20)

SOURCES = [source("run", 10, 20)]


def _generated(found_answer: bool, citations: list[Citation]) -> GeneratedAnswer:
    return GeneratedAnswer(
        found_answer=found_answer,
        answer="text",
        citations=citations,
        sources=SOURCES,
        reply=ModelReply(text="{}", prompt_tokens=100, attempt_count=1),
    )


def _scores_and_groups():
    answerable = [question("q-1", (RUN,)), question("q-2", (RUN,), query_style="identifier")]
    unanswerable = replace(question("q-9", ()), answer_in_code=False)
    scores = {
        "vector": [
            score_generated_answer(
                answerable[0], SOURCES, _generated(True, [Citation(1, 11, 12)]), 1000
            ),
            score_generated_answer(answerable[1], SOURCES, _generated(False, []), 2000),
            score_generated_answer(unanswerable, SOURCES, _generated(False, []), 500),
        ]
    }
    return question_groups(answerable), scores


def test_summaries_cover_all_questions_then_each_answerable_group():
    groups, scores = _scores_and_groups()

    summaries = answer_group_summaries(groups, scores)

    assert summaries["all questions"]["vector"].answerable_count == 2
    assert summaries["all questions"]["vector"].correct_refusal_count == 1
    assert summaries["name questions"]["vector"].cites_every_count == 0
    assert "written from the code" not in summaries


def test_the_answer_table_shows_cites_refusals_and_marks_an_incomplete_run():
    groups, scores = _scores_and_groups()
    summaries = answer_group_summaries(groups, scores)

    table = render_answer_table(
        replace(DETAILS, answer_model="answer-model"), summaries, completed_count=3, total_count=5
    )

    assert "# Answer evaluation: owner/repo 1.0" in table
    assert "INCOMPLETE: 3 of 5 questions" in table
    assert "Answer model: answer-model" in table
    assert "| vector | 1/2 (50%) | 1/2 (50%) | 1/1 | 1 | 0 | 0 | 100 | 1.0 | 2.0 | 3 |" in table
    assert "| Setup | from Stack Overflow (2) | name questions (1) |" in table
