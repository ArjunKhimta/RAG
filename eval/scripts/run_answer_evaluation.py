"""Answer every question from three search setups and save the answer evaluation.

Run from the repository root with the virtual environment active. The evaluation package is not
installed, so its folder is put on the path for the command:

    PYTHONPATH=eval/src python eval/scripts/run_answer_evaluation.py \
        eval/questions/pallets-flask-3.1.3.json

For every question, including those with no answer in the code, finds sources with three setups
(router, the previous default; vector, the default; vector + expand) and asks Gemini for an
answer from each, then scores whether it cites the expected definitions and whether it refuses
correctly (see `evaluation.answer_metrics`). The three setups run question by question, so a run
cut short still compares them on the same questions. Writes `eval/results/<time>-answers.json`
and `.md` and prints the table. A rejected reply's answer text and citations are kept in the JSON
as `rejected_answer` and `rejected_citations`, next to the problems the check found, so the
rejection can be diagnosed.

Costs 3 generation requests per question (150 for 50 questions; more if a temporary failure is
retried), paced under the per-minute limit, and no embedding requests for questions already
cached. A used-up daily quota stops the run: the questions finished so far are saved, marked
incomplete, and the exit code is 1. Refuses to run on a mismatched or unready index (see
`evaluation.preparation`). Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo.errors import PyMongoError
from retrieval.answer_generation import (
    AnswerModel,
    AnswerRejectedError,
    GeminiAnswerModel,
    GeneratedAnswer,
    generate_answer,
)
from retrieval.config import (
    ANSWER_REQUESTS_PER_MINUTE,
    ANSWER_TOKENS_PER_MINUTE,
    GEMINI_ANSWER_MODEL,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError
from retrieval.gemini_errors import GeminiRequestError
from retrieval.question_set import EvaluationQuestion
from retrieval.rate_limiter import RateLimiter
from retrieval.redaction import redact
from retrieval.reranker_model import RerankerModelError
from retrieval.search_results import SearchRefusedError

from evaluation.answer_metrics import (
    AnswerOutcome,
    AnswerScore,
    score_generated_answer,
    score_unanswered,
)
from evaluation.answer_results import answer_group_summaries, render_answer_table
from evaluation.preparation import prepare_run
from evaluation.results import (
    build_run_details,
    details_as_dict,
    question_groups,
    relative_path,
    result_paths,
    source_location,
    write_json,
)
from evaluation.search_setups import SearchContext, SearchSetup, SetupRun, run_setups

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "eval" / "results"

ANSWER_SETUPS = [SearchSetup.ROUTER, SearchSetup.VECTOR, SearchSetup.VECTOR_EXPAND]

MILLISECONDS_PER_SECOND = 1000


class _DailyQuotaUsedUp(Exception):
    """Raised when Gemini's daily quota stops the run part way."""


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        outcome = _evaluate(arguments.questions, arguments.output_directory)
    except SearchRefusedError as error:
        _print(f"Refused: {error}")
        return 1
    except _DailyQuotaUsedUp:
        _print("Stopped: a daily Gemini quota was used up before the first question finished.")
        return 1
    except (MissingConfigError, PyMongoError, RerankerModelError, EmbeddingRequestError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    json_path, table_path, table, is_complete = outcome
    for line in table.splitlines():
        _print(line)
    _print("")
    _print(f"Saved {_relative(json_path)} and {_relative(table_path)}")
    if not is_complete:
        _print("Stopped: a daily Gemini quota is used up; the saved results are marked incomplete.")
        return 1
    return 0


def _evaluate(question_path: Path, output_directory: Path) -> tuple[Path, Path, str, bool]:
    run_at = datetime.now(UTC)
    prepared = prepare_run(question_path)
    questions = list(prepared.question_set.questions)
    answer_model = GeminiAnswerModel(
        prepared.gemini_client, RateLimiter(ANSWER_REQUESTS_PER_MINUTE, ANSWER_TOKENS_PER_MINUTE)
    )
    records: list[dict[str, Any]] = []
    scores_by_setup: dict[str, list[AnswerScore]] = {str(setup): [] for setup in ANSWER_SETUPS}
    is_complete = True
    for question in questions:
        try:
            question_records, question_scores = _answer_question(
                prepared.context, answer_model, question
            )
        except _DailyQuotaUsedUp:
            is_complete = False
            break
        records.append(question_records)
        for setup, score in question_scores.items():
            scores_by_setup[setup].append(score)
    if not records:
        raise _DailyQuotaUsedUp()
    answered_ids = {record["id"] for record in records}
    answerable = [
        question
        for question in questions
        if question.answer_in_code and question.question_id in answered_ids
    ]
    summaries = answer_group_summaries(question_groups(answerable), scores_by_setup)
    details = build_run_details(
        run_at,
        question_path,
        prepared.question_set,
        prepared.repository_record,
        PROJECT_ROOT,
        answer_model=GEMINI_ANSWER_MODEL,
    )
    table = render_answer_table(details, summaries, len(records), len(questions))
    json_path, table_path = result_paths(output_directory, run_at, kind="answers")
    write_json(
        json_path,
        {
            "run": details_as_dict(details),
            "complete": is_complete,
            "completed_question_count": len(records),
            "question_count": len(questions),
            "setups": [str(setup) for setup in ANSWER_SETUPS],
            "questions": records,
        },
    )
    table_path.write_text(table, encoding="utf-8")
    return json_path, table_path, table, is_complete


def _answer_question(
    context: SearchContext, answer_model: AnswerModel, question: EvaluationQuestion
) -> tuple[dict[str, Any], dict[str, AnswerScore]]:
    runs = run_setups(context, question.question, ANSWER_SETUPS)
    setup_records: dict[str, Any] = {}
    scores: dict[str, AnswerScore] = {}
    for setup, run in runs.items():
        record, score = _answer_from(answer_model, question, run)
        setup_records[str(setup)] = record
        scores[str(setup)] = score
    question_record = {
        "id": question.question_id,
        "question": question.question,
        "origin": question.origin,
        "query_style": question.query_style,
        "answer_in_code": question.answer_in_code,
        "expected": [expected.qualified_name for expected in question.expected],
        "setups": setup_records,
    }
    return question_record, scores


def _answer_from(
    answer_model: AnswerModel, question: EvaluationQuestion, run: SetupRun
) -> tuple[dict[str, Any], AnswerScore]:
    started = time.perf_counter()
    base_record: dict[str, Any] = {
        "sources": [source_location(source) for source in run.sources],
        "related_notes": run.related_notes,
        "search_milliseconds": round(run.milliseconds, 1),
    }
    try:
        generated = generate_answer(question.question, run.sources, answer_model, run.related_notes)
    except AnswerRejectedError as error:
        milliseconds = _milliseconds_since(started)
        score = score_unanswered(question, run.sources, AnswerOutcome.REJECTED, milliseconds, 1)
        record = {
            **base_record,
            **_unanswered_record(score, error.problems),
            **_rejected_reply_record(error),
        }
        return record, score
    except GeminiRequestError as error:
        if error.is_daily_quota_exhausted:
            raise _DailyQuotaUsedUp() from error
        milliseconds = _milliseconds_since(started)
        score = score_unanswered(question, run.sources, AnswerOutcome.FAILED, milliseconds, 1)
        problem = f"{type(error).__name__}: {error}"
        return {**base_record, **_unanswered_record(score, [problem])}, score
    milliseconds = _milliseconds_since(started)
    score = score_generated_answer(question, run.sources, generated, milliseconds)
    return {**base_record, **_generated_record(score, generated)}, score


def _generated_record(score: AnswerScore, generated: GeneratedAnswer) -> dict[str, Any]:
    return {
        "outcome": str(score.outcome),
        "answer": generated.answer,
        "citations": [
            {
                "source_number": citation.source_number,
                "file_path": generated.sources[citation.source_number - 1].file_path,
                "qualified_name": generated.sources[citation.source_number - 1].qualified_name,
                "start_line": citation.start_line,
                "end_line": citation.end_line,
            }
            for citation in generated.citations
        ],
        "cited_count": score.cited_count,
        "expected_count": score.expected_count,
        "found_in_sources_count": score.found_in_sources_count,
        "prompt_tokens": generated.reply.prompt_tokens,
        "output_tokens": generated.reply.output_tokens,
        "request_count": generated.reply.attempt_count,
        "answer_milliseconds": round(score.milliseconds, 1),
    }


def _unanswered_record(score: AnswerScore, problems: list[str]) -> dict[str, Any]:
    return {
        "outcome": str(score.outcome),
        "problems": problems,
        "cited_count": 0,
        "expected_count": score.expected_count,
        "found_in_sources_count": score.found_in_sources_count,
        "request_count": score.request_count,
        "answer_milliseconds": round(score.milliseconds, 1),
    }


def _rejected_reply_record(error: AnswerRejectedError) -> dict[str, Any]:
    """What the model wrote in a rejected reply, kept to diagnose the rejection."""
    return {
        "rejected_answer": error.answer,
        "rejected_citations": [
            {
                "source_number": citation.source_number,
                "start_line": citation.start_line,
                "end_line": citation.end_line,
            }
            for citation in error.citations
        ],
    }


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _relative(path: Path) -> str:
    return relative_path(path, PROJECT_ROOT)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("questions", type=Path, help="a question file in eval/questions/")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="where to save the results (default eval/results/)",
    )
    return parser.parse_args()


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
