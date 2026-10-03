"""The answer evaluation's summary table, overall and split by kind of question.

The overall row covers every question, including those with no answer in the code, whose refusals
are counted separately. The splits use the same groups as the retrieval table, over the questions
whose answer is in the code, and show how many answers cite every expected definition.
"""

from __future__ import annotations

from evaluation.answer_metrics import AnswerScore, AnswerSummary, summarize_answers
from evaluation.results import QuestionGroup, RunDetails

ALL_QUESTIONS = "all questions"

MILLISECONDS_PER_SECOND = 1000

PERCENT = 100


def answer_group_summaries(
    answerable_groups: list[QuestionGroup], scores_by_setup: dict[str, list[AnswerScore]]
) -> dict[str, dict[str, AnswerSummary]]:
    """Summarize every setup over all questions, then within each non-empty answerable group."""
    summaries = {
        ALL_QUESTIONS: {
            setup: summarize_answers(setup, scores) for setup, scores in scores_by_setup.items()
        }
    }
    for group in answerable_groups:
        wanted_ids = set(group.question_ids)
        if not wanted_ids:
            continue
        summaries[group.label] = {
            setup: summarize_answers(
                setup, [score for score in scores if score.question_id in wanted_ids]
            )
            for setup, scores in scores_by_setup.items()
        }
    return summaries


def render_answer_table(
    details: RunDetails,
    summaries: dict[str, dict[str, AnswerSummary]],
    completed_count: int,
    total_count: int,
) -> str:
    overall = summaries[ALL_QUESTIONS]
    lines = [
        f"# Answer evaluation: {details.repository} {details.version}",
        "",
        *_details_lines(details, completed_count, total_count),
        "",
        "## All questions",
        "",
        _row(
            [
                "Setup",
                "Cites every expected",
                "Cites at least one",
                "Correct refusals",
                "Wrong refusals",
                "Rejected",
                "Failed",
                "Median prompt tokens",
                "Median s",
                "90th percentile s",
                "Requests",
            ]
        ),
        _row(["---"] * 11),
    ]
    for setup, summary in overall.items():
        lines.append(
            _row(
                [
                    setup,
                    _count_and_percent(summary.cites_every_count, summary.answerable_count),
                    _count_and_percent(summary.cites_any_count, summary.answerable_count),
                    f"{summary.correct_refusal_count}/{summary.refusal_question_count}",
                    str(summary.wrong_refusal_count),
                    str(summary.rejected_count),
                    str(summary.failed_count),
                    _tokens(summary.median_prompt_tokens),
                    _seconds(summary.median_milliseconds),
                    _seconds(summary.slow_milliseconds),
                    str(summary.request_count),
                ]
            )
        )
    group_labels = [label for label in summaries if label not in (ALL_QUESTIONS, "all answerable")]
    lines.extend(
        [
            "",
            "## Answers citing every expected definition, by kind of question",
            "",
            _row(["Setup", *(_group_heading(label, summaries) for label in group_labels)]),
            _row(["---"] * (len(group_labels) + 1)),
        ]
    )
    for setup in overall:
        cells = [
            _count_and_percent(
                summaries[label][setup].cites_every_count,
                summaries[label][setup].answerable_count,
            )
            for label in group_labels
        ]
        lines.append(_row([setup, *cells]))
    lines.extend(["", *_measure_notes()])
    return "\n".join(lines) + "\n"


def _details_lines(details: RunDetails, completed_count: int, total_count: int) -> list[str]:
    changes = " (with uncommitted changes)" if details.project_has_uncommitted_changes else ""
    completeness = f"all {total_count} questions"
    if completed_count < total_count:
        completeness = (
            f"INCOMPLETE: {completed_count} of {total_count} questions, stopped by a used-up "
            "daily Gemini quota"
        )
    return [
        f"- Run at {details.run_at}, project commit `{details.project_commit[:12]}`{changes}",
        f"- Questions: {completeness} from `{details.question_file}` "
        f"(SHA-256 `{details.question_file_sha256[:12]}`)",
        f"- Index: {details.repository} at commit `{details.indexed_commit[:12]}`, "
        f"{details.embedding_model} at {details.embedding_dimensions} dimensions",
        f"- Reranker: {details.reranker_model} at revision `{details.reranker_revision[:12]}`",
        f"- Answer model: {details.answer_model}",
    ]


def _group_heading(label: str, summaries: dict[str, dict[str, AnswerSummary]]) -> str:
    answerable_count = next(iter(summaries[label].values())).answerable_count
    return f"{label} ({answerable_count})"


def _measure_notes() -> list[str]:
    return [
        "Cites every expected: answers whose citations point into every expected definition, of "
        "the questions whose answer is in the code. Cites at least one: into at least one. "
        "Correct refusals: questions with no answer in the code where the model said so and cited "
        "nothing. Wrong refusals: the model said the answer was not found although an expected "
        "definition was among its sources. Rejected: replies refused by the citation check. "
        "Times include waits for the per-minute request limit. One run: answers vary between "
        "runs, so small differences are not meaningful.",
    ]


def _count_and_percent(count: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{count}/{total} ({count / total * PERCENT:.0f}%)"


def _tokens(token_count: int | None) -> str:
    return "not reported" if token_count is None else str(token_count)


def _seconds(milliseconds: float) -> str:
    return f"{milliseconds / MILLISECONDS_PER_SECOND:.1f}"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"
