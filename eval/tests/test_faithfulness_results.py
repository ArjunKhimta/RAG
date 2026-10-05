from __future__ import annotations

from evaluation.faithfulness_metrics import FaithfulnessSummary
from evaluation.faithfulness_results import FaithfulnessRunDetails, render_faithfulness_table

DETAILS = FaithfulnessRunDetails(
    run_at="2026-10-06T09:00:00+00:00",
    project_commit="a" * 40,
    project_has_uncommitted_changes=False,
    judge_model="gemini-3.5-flash-lite",
    answers_file="eval/results/2026-10-06-0830-answers.json",
    answers_file_sha256="b" * 64,
    answers_run_at="2026-10-06T08:30:00+00:00",
    answer_model="gemini-3.5-flash-lite",
    repository="pallets/flask",
    version="3.1.3",
    indexed_commit="c" * 40,
)

SUMMARY = FaithfulnessSummary(
    setup="vector",
    answer_count=41,
    judged_count=40,
    rejected_count=1,
    failed_count=0,
    sentence_count=200,
    supported_count=180,
    miscited_count=12,
    unsupported_count=8,
    uncited_sentence_count=0,
    fully_supported_count=30,
    any_unsupported_count=6,
    median_prompt_tokens=2400,
    request_count=41,
)


def test_the_table_shows_sentence_and_answer_rates_for_each_setup():
    table = render_faithfulness_table(DETAILS, {"vector": SUMMARY}, 41, 41)

    assert "| vector | 200 | 180/200 (90%) | 12/200 (6%) | 8/200 (4%) | 0 |" in table
    assert "| vector | 40/41 | 30/40 (75%) | 6/40 (15%) | 1 | 0 | 2400 | 41 |" in table


def test_the_details_name_the_judged_answer_run():
    table = render_faithfulness_table(DETAILS, {"vector": SUMMARY}, 41, 41)

    assert "- Judge model: gemini-3.5-flash-lite" in table
    assert "all 41 answered replies from `eval/results/2026-10-06-0830-answers.json`" in table
    assert "written at 2026-10-06T08:30:00+00:00 by gemini-3.5-flash-lite" in table
    assert "uncommitted" not in table


def test_a_run_stopped_early_is_marked_incomplete():
    table = render_faithfulness_table(DETAILS, {"vector": SUMMARY}, 20, 41)

    assert "INCOMPLETE: 20 of 41 answered replies" in table
